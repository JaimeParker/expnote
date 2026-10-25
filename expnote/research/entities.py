"""Entity views (plans, exec-plans, evidence, results, decisions, deviations).

Plain dicts built from a ProjectView; shared by the CLI list/show commands, the plan
bundle and (later) the GUI. Nothing here writes.
"""

from __future__ import annotations

import dataclasses
import re

from expnote.research.checks import (
    _check_plan,
    label_state,
    rules_section,
    rules_sha256,
)
from expnote.research.decision_store import read_rows
from expnote.research.decisions import parse_decision_ref
from expnote.research.errors import ResearchError
from expnote.research.model import Decision, DeviationRow, FindingSink, Note
from expnote.research.schemas import SUPPORTED_SCHEMAS
from expnote.research.snapshot import earlier_cycle_links
from expnote.research.summary import ONE_LINE_LIMIT, first_sentence, truncate
from expnote.research.textkit import (
    HTML_COMMENT_RE,
    find_section,
    headings,
    is_separator_row,
    iter_links,
    link_stem,
    markdown_tables,
    section_text,
    split_table_row,
)
from expnote.research.view import ProjectView

PLAN_ORDER = ("draft", "approved", "running", "read-out", "confirmed", "closed")
PLAN_TERMINAL = ("superseded", "withdrawn")


# ---- small text helpers -----------------------------------------------------------


def note_one_line(note: Note, limit: int = ONE_LINE_LIMIT) -> str:
    """`description`, else the first sentence of the first paragraph after the H1."""
    description = note.get("description")
    if isinstance(description, str) and description.strip():
        return truncate(description, limit)
    return first_sentence(note.body, limit)


def title_clause(text: str, delimiters: str, limit: int = 120) -> str:
    """Text up to the first title delimiter (profile), unescaped and shortened."""
    text = " ".join(text.replace("\\|", "|").split())
    if delimiters:
        cut = min((text.find(c) for c in delimiters if c in text), default=-1)
        if cut > 0:
            text = text[:cut]
    return truncate(text, limit)


def _delimiters(view: ProjectView) -> str:
    section = view.profile.raw.get("decisions")
    value = section.get("title_delimiters") if isinstance(section, dict) else ""
    return value if isinstance(value, str) else ""


def outline(note: Note, max_level: int = 3, cap: int = 80) -> list[dict]:
    items = [
        {"level": level, "text": title}
        for _, level, title in headings(note.body)
        if level <= max_level
    ]
    return items[:cap]


def paginate(items: list, limit: int | None, cursor: str | None):
    """(page, nextCursor); the cursor is the offset of the next page."""
    offset = 0
    if cursor:
        try:
            offset = int(cursor)
        except ValueError:
            raise ResearchError(
                "USAGE_BAD_CURSOR", f"cursor {cursor!r} is not valid", exit_code=2
            ) from None
    if limit is None:
        return items[offset:], None
    page = items[offset : offset + limit]
    more = offset + limit < len(items)
    return page, (str(offset + limit) if more else None)


# ---- lookups ----------------------------------------------------------------------


def notes_of(view: ProjectView, entity: str) -> list[Note]:
    snap = view.snapshot
    return [n for n in snap.registry if snap.entity_type(n) == entity]


def has_plan_id_shape(view: ProjectView, note: Note) -> bool:
    """The note's `id` fits the profile's plan id regex (no regex: always true)."""
    regex = view.profile.plan_id_regex
    if not regex:
        return True
    value = note.get("id")
    return isinstance(value, str) and re.fullmatch(regex, value) is not None


def plan_pages(view: ProjectView, *, include_archived: bool = False) -> list[Note]:
    """Plan pages whose id has the plan shape; archived ones only when asked."""
    pages = [
        n
        for n in notes_of(view, "plan")
        if has_plan_id_shape(view, n)
        and (include_archived or not view.snapshot.is_archived(n))
    ]
    return sorted(pages, key=lambda n: (str(n.get("id", "")), n.rel))


def plan_id_of(view: ProjectView, note: Note) -> str | None:
    """Id of the plan a note's `plan:` field points at (None when absent/unresolved)."""
    cstem = note.link_field("plan")
    target = view.snapshot.note(cstem) if cstem else None
    if target is None or target.type != "plan":
        return None
    value = target.get("id")
    return value if isinstance(value, str) else None


def find_plan(view: ProjectView, plan_id: str) -> Note:
    """The plan page for an id: a live page beats an archived copy."""
    candidates = [
        n for n in plan_pages(view, include_archived=True) if n.get("id") == plan_id
    ]
    live = [n for n in candidates if not view.snapshot.is_archived(n)]
    if len(live) > 1:
        raise ResearchError(
            "AMBIGUOUS_ID",
            f"{len(live)} plan pages share the id {plan_id!r}",
            exit_code=3,
            remediation="give one of the notes a different id or archive it",
            details={"candidates": [view.vault_rel(n) for n in live]},
        )
    if live or candidates:
        return (live or candidates)[0]
    known = [str(n.get("id")) for n in plan_pages(view)]
    raise ResearchError(
        "PLAN_NOT_FOUND",
        f"no plan with id {plan_id!r}",
        exit_code=3,
        remediation="expnote plan list",
        details={"known": known},
    )


def find_by_id(view: ProjectView, entity: str, ident: str) -> Note:
    """Entity note by `id` or file stem; ambiguous or absent is an error."""
    hits = [
        n
        for n in notes_of(view, entity)
        if n.get("id") == ident or n.stem == ident or n.cstem == ident.casefold()
    ]
    if not hits:
        raise ResearchError(
            "NOT_FOUND",
            f"no {entity} {ident!r}",
            exit_code=3,
            details={"entity": entity},
        )
    if len(hits) > 1:
        raise ResearchError(
            "AMBIGUOUS",
            f"{entity} {ident!r} matches {len(hits)} notes",
            exit_code=3,
            details={"paths": [view.vault_rel(h) for h in hits]},
        )
    return hits[0]


def is_legacy(view: ProjectView, note: Note) -> bool:
    return note.is_true("legacy") or note.schema not in SUPPORTED_SCHEMAS


def normalized_result_status(view: ProjectView, note: Note) -> str | None:
    status = note.get("status")
    if not isinstance(status, str):
        return None
    if note.schema in SUPPORTED_SCHEMAS:
        return status
    return view.profile.legacy_res_status_map.get(status, status)


def _str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _strs(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _linked_note(view: ProjectView, note: Note, key: str) -> Note | None:
    cstem = note.link_field(key)
    return view.snapshot.note(cstem) if cstem else None


# ---- decisions --------------------------------------------------------------------


def decision_id(view: ProjectView, decision: Decision) -> str:
    return (
        decision.decision_id
        or f"{view.profile.decision_id_prefix}-{decision.number:04d}"
    )


def decision_plans(view: ProjectView) -> dict[int, list[str]]:
    """Decision number -> ids of the plans it concerns (cached)."""
    if "decision_plans" not in view.cache:
        mapping: dict[int, list[str]] = {}
        for page in plan_pages(view):
            plan_id = page.get("id")
            if not isinstance(plan_id, str):
                continue
            for decision, _ in view.snapshot.decisions_for_plan(plan_id):
                plans = mapping.setdefault(decision.number, [])
                if plan_id not in plans:
                    plans.append(plan_id)
        view.cache["decision_plans"] = mapping
    return view.cache["decision_plans"]


def decision_item(view: ProjectView, decision: Decision) -> dict:
    if decision.source == "db":
        title = decision.title or decision.decision_text
    else:
        title = title_clause(decision.decision_text, _delimiters(view))
    confirmed_by = decision.confirmed_by
    superseded_by = (
        f"{view.profile.decision_id_prefix}-{decision.successor:04d}"
        if decision.successor is not None
        else None
    )
    plans = list(decision.plans)
    for plan_id in decision_plans(view).get(decision.number, []):
        if plan_id not in plans:
            plans.append(plan_id)
    return {
        "id": decision_id(view, decision),
        "number": decision.number,
        "date": decision.date,
        "status": decision.status,
        "statusRaw": decision.status_raw,
        "layer": decision.layer,
        "title": title,
        "plans": plans,
        "confirmedBy": confirmed_by,
        "legacy": decision.legacy,
        "source": decision.source,
        "version": decision.version,
        "recordedVia": decision.recorded_via or None,
        "confirmedOn": decision.confirmed_on,
        "path": _table_path(view) if decision.source == "table" else None,
        "supersedes": [
            f"{view.profile.decision_id_prefix}-{n:04d}" for n in decision.supersedes
        ],
        "supersededBy": superseded_by,
    }


def _table_path(view: ProjectView) -> str | None:
    table = view.profile.legacy_decision_table
    return view.vault_rel(table) if table else None


def list_decisions(
    view: ProjectView,
    *,
    plan: str | None = None,
    status: str | None = None,
    layer: str | None = None,
    since: str | None = None,
) -> list[dict]:
    snap = view.snapshot
    if plan:
        find_plan(view, plan)
        chosen = [d for d, _ in snap.decisions_for_plan(plan)]
    else:
        chosen = [snap.decisions[n] for n in sorted(snap.decisions)]
    rows = []
    for decision in chosen:
        if status and decision.status != status:
            continue
        if layer and decision.layer != layer:
            continue
        if since and decision.date < since:
            continue
        rows.append(decision_item(view, decision))
    return rows


def parse_decision_ident(view: ProjectView, ident: str) -> int:
    number = parse_decision_ref(ident.strip(), view.profile, lenient=True)
    if number is None:
        raise ResearchError(
            "USAGE_BAD_DECISION_ID",
            f"{ident!r} is not a decision id "
            f"(use {view.profile.decision_id_prefix}-NNNN "
            "or the number)",
            exit_code=2,
        )
    return number


def decision_detail(view: ProjectView, ident: str, include_body: bool) -> dict:
    number = parse_decision_ident(view, ident)
    decision = view.snapshot.decisions.get(number)
    if decision is None:
        raise ResearchError(
            "NOT_FOUND",
            f"no decision {ident!r}",
            exit_code=3,
            remediation="expnote decision list",
        )
    item = decision_item(view, decision)
    detail: dict = {"decision": item, "refsIn": decision_refs_in(view, number)}
    if decision.source == "db":
        item["sections"] = {
            "decided": decision.decision_text,
            "reason": decision.reason_text,
            "options": decision.options_text,
            "lands": decision.lands_raw,
        }
        item.update(
            targets=list(decision.targets),
            authorization=decision.authorization,
            reviewBy=decision.review_by,
            history=decision_history(view, number),
        )
    else:
        item["decisionText"] = decision.decision_text
        item["landsText"] = decision.lands_text
    if include_body:
        item["body"] = _decision_body(decision)
    return detail


def _decision_body(decision: Decision) -> str:
    return decision.body_text


def decision_history(view: ProjectView, number: int) -> list[dict]:
    loaded = read_rows(view.project_id)
    if loaded is None:
        return []
    return [
        {
            "seq": e["seq"],
            "at": e["at"],
            "actor": e["actor"],
            "action": e["action"],
            "before": e["before"],
            "after": e["after"],
        }
        for e in loaded[1]
        if e["number"] == number
    ]


def decision_refs_in(view: ProjectView, number: int) -> list[dict]:
    rows = view.conn.execute(
        "SELECT f.rel, n.stem, n.type, r.form, r.line FROM decision_refs r "
        "JOIN files f ON f.id = r.src_file_id JOIN notes n ON n.file_id = f.id "
        "WHERE r.number = ? ORDER BY f.nrel, r.line, r.form",
        (number,),
    ).fetchall()
    return [
        {
            "stem": r["stem"],
            "path": r["rel"],
            "type": r["type"],
            "form": r["form"],
            "line": r["line"],
        }
        for r in rows
    ]


def decision_ref_id(view: ProjectView, value) -> str | None:
    """Canonical decision id for a typed field value (a quote stays as written)."""
    if not isinstance(value, str) or not value:
        return None
    number = parse_decision_ref(value, view.profile, lenient=True)
    if number is None:
        return value
    return f"{view.profile.decision_id_prefix}-{number:04d}"


def decision_roles(
    view: ProjectView, plan: Note, members: list[Note]
) -> dict[int, str]:
    profile, snap = view.profile, view.snapshot
    roles: dict[int, str] = {}

    def add(value, role: str) -> None:
        number = parse_decision_ref(value, profile, lenient=True)
        if number is not None:
            roles.setdefault(number, role)

    add(plan.get("approved_decision"), "approval")
    add(plan.get("closed_decision"), "closing")
    for member in members:
        entity = snap.entity_type(member)
        if entity == "exec-plan":
            add(member.get("rules_decision"), "rules")
        elif entity == "evidence":
            add(member.get("label_authorization"), "label-auth")
        elif entity == "result":
            add(member.get("confirmed_decision"), "result")
    return roles


# ---- plans ------------------------------------------------------------------------


def exec_item(view: ProjectView, note: Note) -> dict:
    stored = _str(note.get("rules_sha256"))
    computed = rules_sha256(note.body, view.profile)
    status = note.get("status")
    return {
        "id": _str(note.get("id")),
        "stem": note.stem,
        "plan": plan_id_of(view, note),
        "stage": _str(note.get("stage")),
        "status": status if isinstance(status, str) else None,
        "legacy": is_legacy(view, note),
        "frozen": status in ("frozen", "running", "done"),
        "rulesShaMatches": (stored == computed) if stored and computed else None,
        "rulesDecision": decision_ref_id(view, note.get("rules_decision")),
        "amendedBy": _strs(note.get("rules_amended_by")),
        "approvedBy": _str(note.get("approved_by")),
        "newRules": {"true": True, "false": False}.get(note.get("new_rules")),
        "frozenOn": _str(note.get("frozen_on")),
        "path": view.vault_rel(note),
    }


def ev_label_state(view: ProjectView, note: Note) -> str:
    if note.get("status") == "label-na":
        return "na"
    return label_state(note.body, view.profile)


def ev_item(view: ProjectView, note: Note) -> dict:
    appendix = _linked_note(view, note, "appendix")
    exec_note = _linked_note(view, note, "exec_plan")
    return {
        "stem": note.stem,
        "status": _str(note.get("status")),
        "labelState": ev_label_state(view, note),
        "labelledBy": _str(note.get("labelled_by")),
        "labelAuthorization": decision_ref_id(view, note.get("label_authorization")),
        "plan": plan_id_of(view, note),
        "execPlan": exec_note.stem if exec_note else None,
        "figures": _strs(note.get("figures")),
        "appendix": (
            {"stem": appendix.stem, "path": view.vault_rel(appendix)}
            if appendix
            else None
        ),
        "runs": _strs(note.get("runs")),
        "legacy": is_legacy(view, note),
        "path": view.vault_rel(note),
    }


def _table_cells(table_lines: list[str]) -> tuple[list[str], list[list[str]]]:
    header = split_table_row(table_lines[0]) or []
    rows = [split_table_row(line) or [] for line in table_lines[2:]]
    return header, [r for r in rows if not is_separator_row(r)]


def ev_tables(view: ProjectView, note: Note) -> dict:
    """The label table (rows with a filled flag), the remark and the endpoint table."""
    profile, body = view.profile, note.body

    def label_title(title: str) -> bool:
        return any(k in title for k in profile.heading_labels)

    section = find_section(body, label_title)
    labels = None
    if section is not None:
        text = HTML_COMMENT_RE.sub("", section_text(body, label_title) or "")
        tables = markdown_tables(text)
        if tables:
            header, rows = _table_cells(tables[0])
            column = profile.label_value_columns[0]
            labels = {
                "header": header,
                "valueColumns": list(range(1, len(header))),
                "filledColumn": column,
                "rows": [
                    {
                        "key": row[0] if row else "",
                        "values": row[1:],
                        "filled": len(row) > column and bool(row[column].strip()),
                    }
                    for row in rows
                ],
            }
    remark_keys = profile.heading_labels[1:]
    remark = (
        section_text(body, lambda t: any(k in t for k in remark_keys))
        if remark_keys
        else None
    )
    lines = body.split("\n")
    outside = (
        "\n".join([*lines[: section[0]], *lines[section[1] :]]) if section else body
    )
    endpoint = markdown_tables(HTML_COMMENT_RE.sub("", outside))
    endpoint_table = None
    if endpoint:
        header, rows = _table_cells(endpoint[0])
        endpoint_table = {"header": header, "rows": rows}
    return {
        "labelTable": labels,
        "remark": HTML_COMMENT_RE.sub("", remark).strip() if remark else None,
        "endpointTable": endpoint_table,
    }


def ev_detail(view: ProjectView, ident: str) -> dict:
    note = find_by_id(view, "evidence", ident)
    item = ev_item(view, note)
    item.update(
        sha256=view.file_sha256(note.rel),
        frontmatter=note.fm,
        **ev_tables(view, note),
    )
    return {"ev": item}


def result_item(view: ProjectView, note: Note) -> dict:
    superseded = _linked_note(view, note, "superseded_by")
    return {
        "id": _str(note.get("id")),
        "stem": note.stem,
        "plan": plan_id_of(view, note),
        "status": normalized_result_status(view, note),
        "statusRaw": _str(note.get("status")),
        "confirmedDecision": decision_ref_id(view, note.get("confirmed_decision")),
        "deviationImpact": _str(note.get("deviation_impact")),
        "supersededBy": superseded.stem if superseded else None,
        "evidence": [
            link_stem(target)
            for value in _strs(note.get("evidence"))
            for _, target in iter_links(value)
        ],
        "legacy": is_legacy(view, note),
        "path": view.vault_rel(note),
    }


def deviation_item(view: ProjectView, row: DeviationRow) -> dict:
    return {
        "id": row.did,
        "date": row.date,
        "status": "closed" if row.closed else "open",
        "found": row.found,
        "impact": row.impact,
        "experiment": row.experiment,
        "cards": row.cards,
        "summary": title_clause(row.content, _delimiters(view)),
    }


def deviation_plans(view: ProjectView, row: DeviationRow) -> list[str]:
    plans = []
    for page in plan_pages(view):
        plan_id = page.get("id")
        if not isinstance(plan_id, str):
            continue
        pattern = re.compile(re.escape(plan_id) + r"(?![\w-])")
        if pattern.search(row.experiment) or pattern.search(row.cards):
            plans.append(plan_id)
    return plans


def list_deviations(
    view: ProjectView, *, plan: str | None = None, status: str | None = None
) -> list[dict]:
    snap = view.snapshot
    if plan:
        find_plan(view, plan)
        rows = snap.deviations_for_plan(plan)
    else:
        rows = sorted(
            snap.deviations.values(),
            key=lambda d: (int((re.search(r"\d+", d.did) or [0])[0] or 0), d.did),
        )
    items = []
    for row in rows:
        item = deviation_item(view, row)
        if status and item["status"] != status:
            continue
        items.append(item)
    return items


def deviation_detail(view: ProjectView, ident: str) -> dict:
    row = view.snapshot.deviations.get(ident)
    if row is None:
        raise ResearchError(
            "NOT_FOUND",
            f"no deviation {ident!r}",
            exit_code=3,
            remediation="expnote deviation list",
        )
    item = deviation_item(view, row)
    item.update(
        content=row.content,
        disposition=row.disposition,
        plans=deviation_plans(view, row),
    )
    return {"deviation": item}


def plan_cycle_members(view: ProjectView, plan: Note) -> tuple[list[Note], list[Note]]:
    """(current-cycle, earlier-cycle) members of a plan."""
    return view.snapshot.plan_cycle_members(str(plan.get("id", "")))


def current_plan_members(view: ProjectView, plan: Note) -> list[Note]:
    return view.snapshot.current_plan_members(str(plan.get("id", "")))


DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")


def note_date(view: ProjectView, note: Note) -> str | None:
    """`frozen_on` for an exec-plan, else `created`; an appendix follows its parent."""
    snap = view.snapshot
    is_exec = snap.entity_type(note) == "exec-plan"
    for key in ("frozen_on", "created") if is_exec else ("created",):
        value = note.get(key)
        if isinstance(value, str) and DATE_PREFIX.match(value.strip()):
            return value.strip()[:10]
    parent = note.link_field("parent")
    parent_note = snap.note(parent) if parent else None
    if parent_note is not None and parent_note is not note:
        return note_date(view, parent_note)
    return None


def lifecycle_members(view: ProjectView, plan: Note) -> list[Note]:
    """Exec-plans, results, evidence and appendices: the notes cycles are split by."""
    return [
        m for m in plan_members(view, plan) if view.snapshot.entity_type(m) is not None
    ]


def plan_members(view: ProjectView, plan: Note) -> list[Note]:
    return view.snapshot.plan_members(str(plan.get("id", "")))


def plan_gates(view: ProjectView, plan: Note) -> dict:
    """What blocks each next transition, from the same rules `check` applies."""
    snap = view.snapshot
    status = plan.get("status")

    def unmet(candidate: Note) -> list[dict]:
        sink = FindingSink()
        _check_plan(snap, candidate, sink)
        return [
            {"code": f.code, "message": f.message}
            for f in sink.items
            if f.level == "FAIL"
        ]

    def clone(to: str) -> Note:
        return dataclasses.replace(plan, fm={**plan.fm, "status": to})

    targets: list[str] = []
    if status in PLAN_ORDER and status != PLAN_ORDER[-1]:
        targets.append(PLAN_ORDER[PLAN_ORDER.index(status) + 1])
    if status not in PLAN_TERMINAL:
        targets += list(PLAN_TERMINAL)
    steps = []
    for target in targets:
        problems = unmet(clone(target))
        steps.append({"to": target, "ok": not problems, "unmet": problems})
    return {
        "current": status,
        "known": status in PLAN_ORDER or status in PLAN_TERMINAL,
        "currentUnmet": unmet(plan),
        "next": steps,
    }


def plan_status_counts(view: ProjectView) -> dict[str, int]:
    counts: dict[str, int] = {}
    for page in plan_pages(view):
        key = str(page.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def plan_item(view: ProjectView, plan: Note, open_items: int | None = None) -> dict:
    snap = view.snapshot
    plan_id = str(plan.get("id", ""))
    members = snap.current_plan_members(plan_id)
    results = [m for m in members if snap.entity_type(m) == "result"]
    active = [
        r
        for r in results
        if normalized_result_status(view, r) not in ("superseded", "withdrawn")
    ]
    res_status = normalized_result_status(view, active[0]) if active else None
    return {
        "id": plan_id,
        "title": _str(plan.get("title")) or plan.stem,
        "status": _str(plan.get("status")),
        "legacy": is_legacy(view, plan),
        "archived": snap.is_archived(plan),
        "path": view.vault_rel(plan),
        "openItems": open_items,
        "decisionCount": len(snap.decisions_for_plan(plan_id)),
        "evCount": sum(1 for m in members if snap.entity_type(m) == "evidence"),
        "resStatus": res_status,
    }


def plan_core(view: ProjectView, plan: Note, include_body: bool) -> dict:
    result = _linked_note(view, plan, "result")
    return {
        "id": _str(plan.get("id")),
        "title": _str(plan.get("title")) or plan.stem,
        "status": _str(plan.get("status")),
        "legacy": is_legacy(view, plan),
        "path": view.vault_rel(plan),
        "absPath": str(view.abs_path(plan)),
        "sha256": view.file_sha256(plan.rel),
        "owner": _str(plan.get("owner")),
        "reopenedOn": _str(plan.get("reopened_on")),
        "earlierCycle": earlier_cycle_links(plan),
        "approvedDecision": decision_ref_id(view, plan.get("approved_decision")),
        "closedDecision": decision_ref_id(view, plan.get("closed_decision")),
        "result": (_str(result.get("id")) or result.stem)
        if result
        else _str(plan.get("result")),
        "summary": note_one_line(plan),
        "outline": outline(plan),
        "body": plan.body if include_body else None,
    }


def exec_detail(view: ProjectView, ident: str, include_body: bool) -> dict:
    note = find_by_id(view, "exec-plan", ident)
    item = exec_item(view, note)
    item.update(
        sha256=view.file_sha256(note.rel),
        rulesSha256Stored=_str(note.get("rules_sha256")),
        rulesSha256Computed=rules_sha256(note.body, view.profile),
        hasRulesSection=rules_section(note.body, view.profile) is not None,
        outline=outline(note),
        body=note.body if include_body else None,
    )
    return {"exec": item}
