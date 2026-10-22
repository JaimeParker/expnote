"""`plan bundle`: everything an agent needs about one plan, token-bounded."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from expnote.research.entities import (
    decision_item,
    decision_roles,
    deviation_item,
    ev_item,
    exec_item,
    find_plan,
    note_one_line,
    plan_core,
    plan_gates,
    plan_members,
    result_item,
)
from expnote.research.errors import ResearchError
from expnote.research.extract import first_h1
from expnote.research.index import linked_workspaces
from expnote.research.model import Note
from expnote.research.runs import join_runs
from expnote.research.todos import derived_todos, manual_todos
from expnote.research.view import ProjectView
from expnote.workspace import data_home

SECTIONS = (
    "plan",
    "gates",
    "decisions",
    "execPlans",
    "evidence",
    "results",
    "deviations",
    "related",
    "analysisDocs",
    "runs",
    "openItems",
    "findings",
    "links",
)
DEFAULT_MAX_BYTES = 65536
ENVELOPE_OVERHEAD = 600
# Types that are plan entities (or bookkeeping) and so never "related" material.
NON_RELATED_TYPES = frozenset(
    {
        "plan",
        "exec-plan",
        "evidence",
        "evidence-appendix",
        "result",
        "decision",
        "todo-list",
        "project-profile",
    }
)


def parse_sections(value: str | None, flag: str = "--sections") -> list[str] | None:
    if not value:
        return None
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        raise ResearchError(
            "USAGE_BAD_SECTIONS",
            f"{flag}: unknown section(s) {unknown}; choose from {list(SECTIONS)}",
            exit_code=2,
        )
    return [s for s in SECTIONS if s in names]


def _note_entry(view: ProjectView, note: Note, via: str) -> dict:
    status = note.get("status")
    title = note.get("title")
    return {
        "stem": note.stem,
        "type": note.type,
        "status": status if isinstance(status, str) else None,
        "path": view.vault_rel(note),
        "title": title
        if isinstance(title, str) and title
        else first_h1(note.body) or note.stem,
        "oneLine": note_one_line(note),
        "via": via,
    }


def _project_notes_by_vault_rel(view: ProjectView) -> dict[str, Note]:
    return {view.vault_rel(n): n for n in view.snapshot.registry}


def _file_id(view: ProjectView, note: Note) -> int | None:
    row = view.conn.execute(
        "SELECT id FROM files WHERE rel = ?", (view.vault_rel(note),)
    ).fetchone()
    return row[0] if row else None


def _excluded_from_related(view: ProjectView, note: Note) -> bool:
    snap, profile = view.snapshot, view.profile
    if note.type in NON_RELATED_TYPES or snap.entity_type(note) is not None:
        return True
    return note.rel in (profile.legacy_decision_table, profile.deviation_register)


def related_notes(
    view: ProjectView, plan: Note, members: list[Note], depth: int
) -> list[dict]:
    by_rel = _project_notes_by_vault_rel(view)
    entries: dict[str, dict] = {}

    def add(note: Note, via: str) -> None:
        if note is plan or _excluded_from_related(view, note):
            return
        entries.setdefault(note.rel, _note_entry(view, note, via))

    for member in members:
        add(member, "plan-field")
    plan_file = _file_id(view, plan)
    if plan_file is not None:
        for row in view.conn.execute(
            "SELECT DISTINCT f.rel FROM links l JOIN files f ON f.id = l.src_file_id "
            "WHERE l.resolved_file_id = ? ORDER BY f.nrel",
            (plan_file,),
        ):
            note = by_rel.get(row[0])
            if note is not None:
                add(note, "inbound-link")
    if depth >= 2:
        for source in (plan, *members):
            file_id = _file_id(view, source)
            if file_id is None:
                continue
            for row in view.conn.execute(
                "SELECT DISTINCT t.rel FROM links l "
                "JOIN files t ON t.id = l.resolved_file_id "
                "WHERE l.src_file_id = ? AND l.kind = 'link' ORDER BY t.nrel",
                (file_id,),
            ):
                note = by_rel.get(row[0])
                if note is not None:
                    add(note, "cited")
    return sorted(entries.values(), key=lambda e: (e["type"] or "", e["stem"]))


def _cited_ids(view: ProjectView, notes: list[Note]) -> dict[str, list[str]]:
    """id -> stems of the notes citing it, from `runs:` and run-shaped `[[id]]` links.

    Run-shaped means the profile's run id regex; whether a workspace knows the id is
    decided later by the join, so this also works while a projection still exists.
    """
    cites: dict[str, list[str]] = {}

    def cite(key: str, stem: str) -> None:
        stems = cites.setdefault(key, [])
        if stem not in stems:
            stems.append(stem)

    for note in notes:
        value = note.get("runs")
        for run_id in value if isinstance(value, list) else []:
            if isinstance(run_id, str) and run_id:
                cite(run_id, note.stem)
    regex = view.profile.run_id_regex
    by_vault_rel = {view.vault_rel(n): n for n in notes}
    if regex and by_vault_rel:
        pattern = re.compile(regex)
        marks = ",".join("?" * len(by_vault_rel))
        for row in view.conn.execute(
            "SELECT DISTINCT f.rel, l.target_stem FROM links l "
            "JOIN files f ON f.id = l.src_file_id "
            f"WHERE l.kind = 'link' AND f.rel IN ({marks})",
            list(by_vault_rel),
        ):
            if pattern.fullmatch(row["target_stem"]):
                cite(row["target_stem"], by_vault_rel[row["rel"]].stem)
    return cites


def _unresolved_links(view: ProjectView, notes: list[Note]) -> list[dict]:
    rels = {view.vault_rel(n): n for n in notes}
    marks = ",".join("?" * len(rels))
    rows = view.conn.execute(
        "SELECT f.rel, l.target, l.line FROM links l "
        "JOIN files f ON f.id = l.src_file_id "
        f"WHERE l.resolution = 'none' AND f.rel IN ({marks}) ORDER BY f.nrel, l.line",
        list(rels),
    )
    return [
        {"from": rels[r["rel"]].stem, "target": r["target"], "line": r["line"]}
        for r in rows
    ]


def build_bundle(
    view: ProjectView,
    plan_id: str,
    *,
    depth: int = 1,
    include_body: bool = False,
    sections: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    """(data with the requested sections in canonical order, warnings)."""
    snap = view.snapshot
    wanted = set(sections or SECTIONS)
    plan = find_plan(view, plan_id)
    members = plan_members(view, plan)
    warnings: list[dict] = []
    data: dict = {}

    def with_body(entry: dict, note: Note | None) -> dict:
        if include_body and note is not None:
            entry["body"] = note.body
        return entry

    if "plan" in wanted:
        data["plan"] = plan_core(view, plan, include_body)
    if "gates" in wanted:
        data["gates"] = plan_gates(view, plan)
    if "decisions" in wanted:
        roles = decision_roles(view, plan, members)
        rows = []
        for decision, why in snap.decisions_for_plan(plan_id):
            item = decision_item(view, decision)
            item["role"] = roles.get(decision.number, "other")
            item["why"] = why
            if include_body:
                item["body"] = decision.body_text
            rows.append(item)
        data["decisions"] = rows
    if "execPlans" in wanted:
        data["execPlans"] = [
            with_body(exec_item(view, m), m)
            for m in members
            if snap.entity_type(m) == "exec-plan"
        ]
    if "evidence" in wanted:
        data["evidence"] = [
            with_body(ev_item(view, m), m)
            for m in members
            if snap.entity_type(m) == "evidence"
        ]
    if "results" in wanted:
        data["results"] = [
            with_body(result_item(view, m), m)
            for m in members
            if snap.entity_type(m) == "result"
        ]
    if "deviations" in wanted:
        data["deviations"] = [
            deviation_item(view, row) for row in snap.deviations_for_plan(plan_id)
        ]
    if "related" in wanted:
        related = related_notes(view, plan, members, depth)
        if include_body:
            by_stem = {n.stem: n for n in snap.registry}
            for entry in related:
                note = by_stem.get(entry["stem"])
                if note is not None:
                    entry["body"] = note.body
        data["related"] = related
    if wanted & {"analysisDocs", "runs"}:
        citing = [
            m
            for m in members
            if snap.entity_type(m) in ("evidence", "evidence-appendix", "exec-plan")
        ]
        run_cites = _cited_ids(view, citing)
        names = linked_workspaces(view.context)
        if run_cites:
            if not names:
                warnings.append(
                    {
                        "code": "RUNS_NOT_LINKED",
                        "message": "the plan cites runs but the project has no "
                        "linked workspace",
                    }
                )
            joined = join_runs(names, set(run_cites), set(run_cites))
            warnings += [
                {"code": "RUNS_UNAVAILABLE", "message": f"workspace {w}: {why}"}
                for w, why in joined.unavailable.items()
            ]
            runs = [
                {
                    "id": r.id,
                    "workspace": r.workspace,
                    "topic": r.topic,
                    "status": r.status,
                    "result": r.result,
                    "updatedAt": r.updated_at,
                    "citedBy": run_cites.get(r.id, []),
                }
                for r in joined.runs
            ]
            docs = [
                {
                    "id": d.id,
                    "workspace": d.workspace,
                    "title": d.title,
                    "oneLine": d.one_line,
                    "linkedRuns": sorted(d.linked_runs),
                }
                for d in joined.docs
            ]
        else:
            runs, docs = [], []
        data["runs"] = runs
        data["analysisDocs"] = docs
    if "openItems" in wanted:
        derived, _ = derived_todos(view, include_runs=False)
        manual = manual_todos(view)
        data["openItems"] = [
            {
                k: item[k]
                for k in (
                    "id",
                    "kind",
                    "owner",
                    "severity",
                    "message",
                    "ref",
                    "action",
                    "command",
                )
            }
            for item in [*derived, *manual]
            if item["plan"] == plan_id
        ]
    if "findings" in wanted:
        result = view.checks(plan_id)
        shown = [f for f in result.findings if f.level in ("FAIL", "WARN")]
        data["findings"] = {
            "fail": sum(1 for f in shown if f.level == "FAIL"),
            "warn": sum(1 for f in shown if f.level == "WARN"),
            "items": [
                {
                    "level": f.level,
                    "code": f.code,
                    "where": f.where,
                    "message": f.message,
                }
                for f in shown
            ],
        }
    if "links" in wanted:
        data["links"] = _links_section(view, plan, members) if depth >= 2 else None
    return {name: data[name] for name in SECTIONS if name in data}, warnings


def _links_section(view: ProjectView, plan: Note, members: list[Note]) -> dict:
    plan_file = _file_id(view, plan)
    inbound = []
    if plan_file is not None:
        for row in view.conn.execute(
            "SELECT DISTINCT n.stem, n.type FROM links l "
            "JOIN notes n ON n.file_id = l.src_file_id "
            "JOIN files f ON f.id = n.file_id "
            "WHERE l.resolved_file_id = ? ORDER BY n.stem",
            (plan_file,),
        ):
            inbound.append({"stem": row["stem"], "type": row["type"]})
    return {"inbound": inbound, "unresolved": _unresolved_links(view, [plan, *members])}


# ---- size budget and spill -----------------------------------------------------------


def serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def byte_size(value) -> int:
    return len(serialized(value).encode("utf-8"))


def fit_sections(
    data: dict, max_bytes: int, start: str | None = None
) -> tuple[dict, str | None]:
    """(sections that fit in order, name of the first section left out)."""
    names = list(data)
    if start is not None:
        if start not in data:
            raise ResearchError(
                "USAGE_BAD_CURSOR",
                f"cursor {start!r} is not one of the selected sections {names}",
                exit_code=2,
            )
        names = names[names.index(start) :]
    used = ENVELOPE_OVERHEAD
    included: dict = {}
    for name in names:
        size = byte_size({name: data[name]})
        if included and used + size > max_bytes:
            return included, name
        included[name] = data[name]
        used += size
    return included, None


def spill_dir() -> Path:
    return data_home() / "research" / "spill"


def write_spill(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )  # key order kept: sections stay in canonical order
    os.replace(temp, path)
    return path


def spill_path(plan_id: str, payload: dict) -> Path:
    digest = hashlib.sha256(serialized(payload).encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w.-]", "_", plan_id)
    return spill_dir() / f"bundle-{safe}-{digest}.json"
