"""P4 vault migration: the nine mechanical steps, the templates step and the plan.

Every step builds its edits from the vault as it is now, so applying a step twice
changes nothing the second time. All writes go through `writeback`: edits are
compare-and-swap transforms, creates never clobber, deletes and the one rename copy a
pre-image to the journal first. Judgement items (plan membership of legacy evidence,
legacy plan status, final RES, hygiene) are only proposed in review tables; the one
way they are applied is the coordinator's override files in the migration directory.
"""

from __future__ import annotations

import dataclasses
import difflib
import functools
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from expnote.research import migrate_backup as backup
from expnote.research import migrate_review as review
from expnote.research import migrate_templates as templates
from expnote.research import migrate_texts as texts
from expnote.research.decision_export import sync_export
from expnote.research.decision_store import DecisionStore, read_rows
from expnote.research.edits import (
    decode_note,
    edit_frontmatter,
    yaml_list,
    yaml_quote,
)
from expnote.research.errors import ResearchError
from expnote.research.frontmatter import split_frontmatter
from expnote.research.migrate_profile import OFF2ON_PROFILE_TEXT
from expnote.research.model import Note, parse_note
from expnote.research.profile import parse_profile_text
from expnote.research.registry import ProjectContext, open_project
from expnote.research.schemas import PLAN_STATUS_V3
from expnote.research.snapshot import VaultSnapshot, scan_vault
from expnote.research.textkit import fold, iter_links, link_stem
from expnote.research.writeback import (
    Change,
    FileEdit,
    WriteContext,
    apply_changes,
    journaled_delete,
    plan_changes,
    project_state_dir,
    rename_path,
)

STEP_NAMES = {
    1: "schema",
    2: "decisions",
    3: "hub",
    4: "view",
    5: "types",
    6: "exec",
    7: "runlinks",
    8: "projection",
    9: "rename",
    10: "templates",
}
STEP_TITLES = {
    1: "plans and schema-2 notes: schema 3, decision ids, status tails",
    2: "decision store rows from the legacy table (round-trip gate)",
    3: "hub stub replaces the decision table",
    4: "delete the generated research view, re-point its links",
    5: "legacy evidence / result cards / long-tail types",
    6: "exec-plans (rules_sha256 asserted unchanged)",
    7: "run-link rewrites (project notes and the outside notes)",
    8: "stop the projection of the bound workspace",
    9: "rename notes with characters Windows rejects, follow the links",
    10: "schema 3 templates",
}
DECISION_FIELDS = {
    "plan": (("approved_decision", "closed_decision"), ()),
    "evidence": (("label_authorization",), ()),
    "evidence-appendix": ((), ()),
    "result": (("confirmed_decision",), ("decisions",)),
    "exec-plan": (("rules_decision",), ()),
}
SCHEMA2_STEP1_TYPES = ("plan", "evidence", "evidence-appendix", "result")
LONG_TAIL_SKIP = frozenset(
    {
        "plan",
        "exec-plan",
        "evidence",
        "evidence-appendix",
        "result",
        "result-card",
        "decision",
        "decision-log",
        "decision-log-view",
        "decision-hub",
        "project-moc",
        "project-profile",
        "progress-board",
        "template-spec",
        "deviation-log",
        "todo-list",
    }
)
CONFIG_COMMENT = "# expnote migrate: Markdown projection disabled; originals below"


# ---- context and state --------------------------------------------------------------


def migration_dir(project_id: str) -> Path:
    return project_state_dir(project_id) / "migration"


def now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclasses.dataclass
class Migration:
    context: ProjectContext
    wctx: WriteContext
    no_quiet: bool = False

    @classmethod
    def open(cls, project_id: str | None, *, no_quiet: bool = False) -> Migration:
        context = open_project(project_id)
        profile = context.profile
        if not (
            profile.legacy_decision_table
            and profile.legacy_decision_columns
            and len(profile.forbidden_markers) == 2
        ):
            raise ResearchError(
                "PROFILE_NOT_MIGRATION_READY",
                "the profile note does not carry the legacy.* and forbidden.* "
                "constants the migration reads (is it still the generic scaffold?)",
                exit_code=1,
                remediation="expnote migrate profile --apply, then review the note",
            )
        return cls(context, WriteContext.for_project(context, "migration"), no_quiet)

    @property
    def project_id(self) -> str:
        return self.context.entry.id

    @property
    def profile(self):
        return self.context.profile

    @property
    def vault_root(self) -> Path:
        return self.context.resolved.vault_root

    @property
    def project_dir(self) -> Path:
        return self.context.resolved.project_dir

    @property
    def state_dir(self) -> Path:
        return migration_dir(self.project_id)

    def snapshot(self) -> VaultSnapshot:
        return scan_vault(self.context)

    # state files (outside the vault)
    def state_path(self, name: str) -> Path:
        return self.state_dir / name

    def read_json(self, name: str, default=None):
        path = self.state_path(name)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, name: str, payload) -> Path:
        path = self.state_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.state_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # facts about the projection
    def projections(self) -> list[backup.Projection]:
        saved = (self.read_json("projection.json", {}) or {}).get("workspaces", [])
        return backup.projections(self.context, saved)

    def projection_roots(self) -> list[Path]:
        return [p.root for p in self.projections() if p.root is not None]

    def remember_projection(self) -> None:
        live = [p.as_dict() for p in self.projections() if p.enabled]
        if live:
            self.write_json("projection.json", {"workspaces": live})


def offtwo_context(context: ProjectContext) -> ProjectContext:
    """The same project read with the embedded Off2On profile (the 'before' golden)."""
    note = parse_profile_text(OFF2ON_PROFILE_TEXT)
    return dataclasses.replace(
        context, profile=note.profile, profile_sha256=note.sha256 or ""
    )


# ---- step plans ----------------------------------------------------------------------


@dataclasses.dataclass
class StepPlan:
    step: int
    edits: list[FileEdit] = dataclasses.field(default_factory=list)
    deletes: list[Path] = dataclasses.field(default_factory=list)
    renames: list[tuple[Path, Path]] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    problems: list[str] = dataclasses.field(default_factory=list)
    summary: dict = dataclasses.field(default_factory=dict)
    external_only: bool = False
    imports: list[dict] = dataclasses.field(default_factory=list)  # decision rows

    @property
    def name(self) -> str:
        return STEP_NAMES[self.step]


def parse_step(value: str) -> list[int]:
    if value == "all":
        return list(STEP_NAMES)
    by_name = {name: number for number, name in STEP_NAMES.items()}
    for part in [value]:
        if part.isdigit() and int(part) in STEP_NAMES:
            return [int(part)]
        if part in by_name:
            return [by_name[part]]
    raise ResearchError(
        "USAGE_BAD_STEP",
        f"unknown step {value!r}",
        exit_code=2,
        remediation="use 1-10, a step name ("
        + ", ".join(STEP_NAMES.values())
        + ") or all",
    )


def _body(text: str) -> str:
    return split_frontmatter(text)[1]


def _assert_body_equal(before: str, after: str, what: str) -> None:
    if _body(before) != _body(after):
        raise ResearchError(
            "MIGRATION_BODY_CHANGED",
            f"{what}: the body would change; refusing",
            exit_code=1,
        )


def _is_stub(text: str) -> bool:
    return bool(re.search(r"^type:\s*decision-hub\s*$", text, re.M))


# ---- step 1: plans and schema-2 notes ------------------------------------------------


def _schema2_text(text: str, *, path: Path, rel: str, prefix: str, markers) -> str:
    note = parse_note(path, rel, text)
    scalar_keys, list_keys = DECISION_FIELDS.get(note.type or "", ((), ()))
    out = texts.canonicalise_decision_fields(text, note, prefix, scalar_keys, list_keys)
    if len(markers) == 2:
        cut = texts.excise_marker_block(out, markers[0], markers[1])
        if cut.removed:
            texts.assert_pure_excision(out, cut.text, cut.removed)
            out = cut.text
    out = texts.set_schema_first(out, "3")
    _assert_body_equal(
        texts.excise_marker_block(text, *markers).text if len(markers) == 2 else text,
        out,
        rel,
    )
    return out


def _legacy_plan_text(
    text: str, *, path: Path, rel: str, prefix: str, judgement: dict
) -> str:
    note = parse_note(path, rel, text)
    sets: list[tuple[str, str]] = [("legacy", "true")]
    original = note.get("status")
    if judgement.get("status"):
        sets.append(("status", str(judgement["status"])))
        if original and original != judgement["status"]:
            sets.append(("legacy_status", yaml_quote(str(original))))
    if not note.get("title"):
        heading = next(
            (ln[2:].strip() for ln in note.body.split("\n") if ln.startswith("# ")), ""
        )
        if heading:
            sets.append(("title", yaml_quote(heading)))
    for key in ("approved_decision", "closed_decision"):
        if judgement.get(key) is not None:
            canon = texts.canonical_decision_id(str(judgement[key]), prefix)
            if canon:
                sets.append((key, yaml_quote(canon)))
    out = edit_frontmatter(text, sets)
    out = texts.set_schema_first(out, "3")
    _assert_body_equal(text, out, rel)
    return out


def step_schema(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(1)
    prefix = m.profile.decision_id_prefix
    markers = m.profile.forbidden_markers
    judgements = (m.read_json("judgements.json", {}) or {}).get("plans", {})
    for note in snap.registry:
        if note.fm_error or not note.has_fm:
            continue
        if note.schema == "2" and note.type in SCHEMA2_STEP1_TYPES:
            transform = functools.partial(
                _schema2_text,
                path=note.path,
                rel=note.rel,
                prefix=prefix,
                markers=markers,
            )
            plan.edits.append(
                FileEdit(note.path, "migrate", "schema2->3", transform=transform)
            )
        elif note.type == "plan" and note.schema is None:
            judgement = judgements.get(note.get("id") or "")
            if judgement and not snap.is_archived(note):
                status = judgement.get("status") or note.get("status")
                if status not in PLAN_STATUS_V3:
                    plan.notes.append(
                        f"{note.rel}: held, status {status!r} is not in the schema 3 "
                        "vocabulary and the judgement does not map it"
                    )
                    continue
                transform = functools.partial(
                    _legacy_plan_text,
                    path=note.path,
                    rel=note.rel,
                    prefix=prefix,
                    judgement=judgement,
                )
                plan.edits.append(
                    FileEdit(
                        note.path, "migrate", "legacy-plan->3", transform=transform
                    )
                )
    untouched = [
        n.rel
        for n in review.legacy_format_plans(snap)
        if (judgements or {}).get(n.get("id") or "") is None
    ]
    if untouched:
        plan.notes.append(
            "legacy-format plans left alone until the coordinator writes "
            f"judgements.json: {', '.join(untouched)}"
        )
    plan.summary = {"edits": len(plan.edits)}
    return plan


# ---- what later steps did to links (steps 2, 3 and the verify gates need it) ---------


def applied_steps(m: Migration) -> set[str]:
    return {r["name"] for r in (m.read_json("steps.json", []) or [])}


@dataclasses.dataclass
class LinkContext:
    """The link rewrites of steps 4, 7 and 9, to predict a note's text after them."""

    applied: set[str]
    run_stems: frozenset
    stem_renames: dict[str, str]
    new_names: dict[str, str]  # original vault-relative path -> renamed path

    def rewrite(self, m: Migration, text: str, *, runs: bool = True) -> str:
        table = m.profile.legacy_decision_table
        if "view" in self.applied and table and m.profile.generated_notes:
            hub = Path(table).stem
            renames = {fold(Path(g).stem): hub for g in m.profile.generated_notes}
            text = texts.retarget_links(text, renames)[0]
        if "runlinks" in self.applied and runs and self.run_stems:
            text = texts.runs_to_plain(text, set(self.run_stems))[0]
        if "rename" in self.applied and self.stem_renames:
            text = texts.retarget_links(text, self.stem_renames)[0]
        return text


def link_context(m: Migration) -> LinkContext:
    record = m.read_json("backup.json") or {}
    paths = (
        [e["path"] for e in backup.read_manifest(Path(record["dir"]))] if record else []
    )
    new_names = {
        rel: str(Path(rel).with_name(texts.straighten_quotes(Path(rel).name)))
        for rel in paths
        if '"' in Path(rel).name
    }
    return LinkContext(
        applied=applied_steps(m),
        run_stems=frozenset(run_link_stems(m)[0]),
        stem_renames={
            fold(Path(old).stem): Path(new).stem for old, new in new_names.items()
        },
        new_names=new_names,
    )



# ---- step 2: decision files ----------------------------------------------------------


def legacy_table_text(m: Migration, *, save: bool) -> str | None:
    """The legacy decision table: live while it holds rows, else the saved copy."""
    table = m.profile.legacy_decision_table
    if not table:
        return None
    live = m.project_dir / table
    columns = m.profile.legacy_decision_columns
    if live.is_file():
        text = decode_note(live.read_bytes())[0]
        if texts.parse_legacy_rows(text, columns):
            if save:
                m.write_text("legacy-decision-table.md", text)
            return text
    saved = m.state_path("legacy-decision-table.md")
    return saved.read_text(encoding="utf-8") if saved.exists() else None


def _plan_ids(snap: VaultSnapshot) -> list[str]:
    return sorted(review.plan_pages(snap))


def stored_migrated_rows(m: Migration) -> dict[int, dict]:
    """Decision-store rows by number (empty before the store exists)."""
    loaded = read_rows(m.project_id)
    return {row["number"]: row for row in loaded[0]} if loaded else {}


def step_decisions(
    m: Migration, snap: VaultSnapshot, *, save: bool = False
) -> StepPlan:
    plan = StepPlan(2)
    table = legacy_table_text(m, save=save)
    if table is None:
        plan.notes.append("no legacy decision table (nothing to import)")
        return plan
    rows = texts.parse_legacy_rows(table, m.profile.legacy_decision_columns)
    made = texts.build_decision_records(rows, m.profile, _plan_ids(snap))
    stored = stored_migrated_rows(m)
    records: dict[int, dict] = {}
    for item in made:
        have = stored.get(item.number)
        if have is None:
            plan.imports.append(item.record)
            records[item.number] = item.record
            plan.notes += [f"{item.decision_id}: {p}" for p in item.problems]
            continue
        records[item.number] = have
        differs = texts.import_differences(item.record, have)
        if differs:
            plan.problems.append(
                f"{item.decision_id}: the store row differs from the table in "
                f"{', '.join(differs)}"
            )
    mismatches = texts.roundtrip_mismatches(rows, records)
    plan.problems += [f"round-trip: {p}" for p in mismatches]
    plan.summary = {
        "rows": len(rows),
        "create": len(plan.imports),
        "alreadyThere": len(rows) - len(plan.imports),
        "roundTripMismatches": len(mismatches),
    }
    return plan


# ---- step 3: hub stub ----------------------------------------------------------------


def _hub_transform(text: str, stub: str) -> str:
    return text if _is_stub(text) else stub


def step_hub(
    m: Migration, snap: VaultSnapshot, *, assume_decisions: bool = False
) -> StepPlan:
    plan = StepPlan(3)
    table = m.profile.legacy_decision_table
    if not table:
        return plan
    hub = m.project_dir / table
    if not hub.is_file():
        plan.notes.append(f"{table} does not exist")
        return plan
    live = decode_note(hub.read_bytes())[0]
    if _is_stub(live):
        return plan
    saved = legacy_table_text(m, save=False)
    rows = texts.parse_legacy_rows(saved or "", m.profile.legacy_decision_columns)
    if not rows:
        plan.problems.append(f"{table} holds no decision rows and is not the stub")
        return plan
    records = stored_migrated_rows(m)
    if assume_decisions:  # a plan run: step 2 has not been applied yet
        for made in texts.build_decision_records(rows, m.profile, _plan_ids(snap)):
            records.setdefault(made.number, made.record)
    mismatches = texts.roundtrip_mismatches(rows, records)
    plan.problems += [f"round-trip: {p}" for p in mismatches]
    stub = templates.hub_stub_text(m.profile.decision_id_prefix, Path(table).stem)
    plan.edits.append(
        FileEdit(
            hub,
            "migrate",
            "hub-stub",
            transform=functools.partial(_hub_transform, stub=stub),
        )
    )
    plan.summary = {"rows": len(rows), "storedRows": len(records)}
    return plan


# ---- step 4: research view -----------------------------------------------------------


def _retarget_text(text: str, *, renames: dict[str, str]) -> str:
    return texts.retarget_links(text, renames)[0]


def step_view(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(4)
    table = m.profile.legacy_decision_table
    generated = [Path(g) for g in m.profile.generated_notes]
    if not table or not generated:
        return plan
    hub_stem = Path(table).stem
    renames = {fold(g.stem): hub_stem for g in generated}
    doomed = {m.project_dir / g for g in generated}
    for note in snap.notes:
        if note.path in doomed or not any(g.stem in note.text for g in generated):
            continue
        plan.edits.append(
            FileEdit(
                note.path,
                "migrate",
                "repoint:research-view",
                transform=functools.partial(_retarget_text, renames=renames),
            )
        )
    plan.deletes = [p for p in sorted(doomed) if p.is_file()]
    plan.summary = {"candidates": len(plan.edits), "deleted": len(plan.deletes)}
    return plan


# ---- step 5: legacy evidence, result cards, long-tail types --------------------------


def load_overrides(m: Migration) -> dict:
    return m.read_json("plan-overrides.json", {}) or {}


def _legacy_ev_text(
    text: str, *, path: Path, rel: str, plan_link: str | None, exts: frozenset
) -> str:
    note = parse_note(path, rel, text)
    original = note.get("status")
    sets: list[tuple[str, str]] = [("legacy", "true")]
    if original in review.EV_STATUS_KEEP:
        pass
    else:
        sets.append(("status", "label-na"))
        if original:
            sets.append(("legacy_status", yaml_quote(str(original))))
    if not note.get("figures"):
        figures = review.figures_of(note, exts)
        sets.append(("figures", yaml_list(figures)))
    if plan_link and not note.get("plan"):
        sets.append(("plan", yaml_quote(plan_link)))
    out = texts.set_schema_first(edit_frontmatter(text, sets), "3")
    _assert_body_equal(text, out, rel)
    return out


def _result_card_text(
    text: str, *, path: Path, rel: str, res_id: str, status_map: dict[str, str]
) -> str:
    note = parse_note(path, rel, text)
    original = note.get("status") or ""
    status = status_map.get(original, original)
    if status not in ("first-read", "confirmed", "superseded", "withdrawn"):
        status = "first-read"
    sets: list[tuple[str, str]] = [
        ("type", "result"),
        ("id", yaml_quote(res_id)),
        ("status", status),
        ("legacy", "true"),
    ]
    evidence = note.get("evidence")
    if isinstance(evidence, str) and evidence.strip():
        sets.append(("evidence", yaml_list([evidence])))
    out = texts.set_schema_first(edit_frontmatter(text, sets), "3")
    _assert_body_equal(text, out, rel)
    return out


def _light_schema_text(text: str, *, rel: str, plan_link: str | None) -> str:
    sets = [("plan", yaml_quote(plan_link))] if plan_link else []
    out = texts.set_schema_first(edit_frontmatter(text, sets) if sets else text, "3")
    _assert_body_equal(text, out, rel)
    return out


def _skippable(m: Migration, note: Note) -> bool:
    return note.fm_error is not None or not note.has_fm


def step_types(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(5)
    profile = m.profile
    pages = review.plan_pages(snap)
    known = sorted(pages)
    overrides = load_overrides(m)
    rows = review.infer_ev_plans(snap, overrides)
    row_by_rel = {r["ev"]: r for r in rows}
    held: list[str] = []
    used_ids: set[str] = set()
    for note in snap.registry:
        if _skippable(m, note) or note.schema is not None:
            continue
        if note.type == "evidence":
            row = row_by_rel.get(note.rel)
            applied = row["appliedPlan"] if row else None
            if applied is None and not note.get("plan"):
                held.append(note.rel)
                continue
            link = f"[[{pages[applied].stem}]]" if applied in pages else None
            transform = functools.partial(
                _legacy_ev_text,
                path=note.path,
                rel=note.rel,
                plan_link=link,
                exts=profile.attachment_exts,
            )
            plan.edits.append(
                FileEdit(note.path, "migrate", "legacy-ev->3", transform=transform)
            )
        elif note.type in profile.legacy_type_map:
            res_id = texts.result_id_slug(note.stem)
            candidate, n = res_id, 1
            while candidate in used_ids:
                n += 1
                candidate = f"{res_id}-{n}"
            used_ids.add(candidate)
            transform = functools.partial(
                _result_card_text,
                path=note.path,
                rel=note.rel,
                res_id=candidate,
                status_map=profile.legacy_res_status_map,
            )
            plan.edits.append(
                FileEdit(note.path, "migrate", "result-card->3", transform=transform)
            )
        elif note.type and note.type not in LONG_TAIL_SKIP:
            hits = review.plan_ids_in(note.stem, known)
            link = (
                f"[[{pages[hits[0]].stem}]]"
                if len(hits) == 1 and not note.get("plan")
                else None
            )
            transform = functools.partial(
                _light_schema_text, rel=note.rel, plan_link=link
            )
            plan.edits.append(
                FileEdit(note.path, "migrate", "long-tail->3", transform=transform)
            )
    if held:
        plan.notes.append(
            f"{len(held)} legacy evidence note(s) without a high-confidence plan are "
            "left unmigrated (review table a)"
        )
    plan.summary = {"edits": len(plan.edits), "evidenceHeld": len(held)}
    return plan


# ---- step 6: exec-plans --------------------------------------------------------------


def _exec_text(text: str, *, path: Path, rel: str, prefix: str, rules_hash) -> str:
    note = parse_note(path, rel, text)
    scalar_keys, list_keys = DECISION_FIELDS["exec-plan"]
    out = texts.canonicalise_decision_fields(text, note, prefix, scalar_keys, list_keys)
    out = texts.set_schema_first(out, "3")
    _assert_body_equal(text, out, rel)
    before = rules_hash(note.body)
    after = rules_hash(parse_note(path, rel, out).body)
    if before != after or note.get("rules_sha256") != parse_note(path, rel, out).get(
        "rules_sha256"
    ):
        raise ResearchError(
            "MIGRATION_RULES_HASH",
            f"{rel}: rules_sha256 would change; refusing",
            exit_code=1,
        )
    return out


def step_exec(m: Migration, snap: VaultSnapshot) -> StepPlan:
    from expnote.research.checks import rules_sha256

    plan = StepPlan(6)
    rules_hash = functools.partial(rules_sha256, profile=m.profile)
    for note in snap.registry:
        if note.fm_error or note.type != "exec-plan" or note.schema != "2":
            continue
        transform = functools.partial(
            _exec_text,
            path=note.path,
            rel=note.rel,
            prefix=m.profile.decision_id_prefix,
            rules_hash=lambda body, h=rules_hash: h(body),
        )
        plan.edits.append(
            FileEdit(note.path, "migrate", "exec2->3", transform=transform)
        )
    plan.summary = {"edits": len(plan.edits)}
    return plan


# ---- step 7: run links ---------------------------------------------------------------


def _runs_text(text: str, *, stems: frozenset) -> str:
    return texts.runs_to_plain(text, set(stems))[0]


def run_link_stems(m: Migration) -> tuple[dict[str, str], list[Path]]:
    """Projected stems safe to rewrite (live folder, else the list saved earlier)."""
    roots = m.projection_roots()
    live = [r for r in roots if r.is_dir()]
    if not live:
        saved = m.read_json("run-stems.json", {}) or {}
        return dict(saved), roots
    stems = backup.projected_stems(m.vault_root, live)
    shared = backup.shared_stems(m.vault_root, roots, stems)
    return {k: v for k, v in stems.items() if k not in shared}, roots


def remember_run_stems(m: Migration) -> None:
    stems, roots = run_link_stems(m)
    if stems and any(r.is_dir() for r in roots):
        m.write_json("run-stems.json", stems)


def step_runlinks(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(7)
    stems, roots = run_link_stems(m)
    if not stems:
        plan.notes.append("no projected notes (projection folder gone or not set)")
        return plan
    frozen = frozenset(stems)
    marker = "[["
    for note in snap.notes:
        if marker not in note.text:
            continue
        if not any(fold(link_stem(t)) in frozen for _, t in iter_links(note.text)):
            continue
        plan.edits.append(
            FileEdit(
                note.path,
                "migrate",
                "runlinks:project",
                transform=functools.partial(_runs_text, stems=frozen),
            )
        )
    outside = backup.outside_notes(m.vault_root, m.project_dir, roots, set(frozen))
    live = [o for o in outside if not o["archived"]]
    for entry in live:
        plan.edits.append(
            FileEdit(
                m.vault_root / entry["path"],
                "migrate",
                "runlinks:outside",
                transform=functools.partial(_runs_text, stems=frozen),
            )
        )
    plan.summary = {
        "projectNotes": len(plan.edits) - len(live),
        "outsideNotes": len(live),
        "archivedLeftAlone": len(outside) - len(live),
    }
    return plan


# ---- step 8: projection off ----------------------------------------------------------


def _config_text(text: str) -> str:
    kept, removed = [], []
    for line in text.split("\n"):
        key = line.partition("=")[0].strip()
        if key in backup.PROJECTION_KEYS and not line.strip().startswith("#"):
            removed.append(line)
        else:
            kept.append(line)
    if not removed:
        return text
    while kept and not kept[-1].strip():
        kept.pop()
    block = [CONFIG_COMMENT, *[f"# {line}" for line in removed], 'projection = "off"']
    return "\n".join([*kept, *block]) + "\n"


def step_projection(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(8, external_only=True)
    for projection in m.projections():
        if not projection.enabled:
            continue
        plan.edits.append(
            FileEdit(
                projection.config_path,
                "migrate",
                "projection:off",
                transform=_config_text,
                external=True,
            )
        )
        plan.notes.append(
            f"workspace {projection.workspace}: remove "
            f"{', '.join(sorted(projection.original))} from {projection.config_path}"
        )
    plan.summary = {"workspaces": len(plan.edits)}
    return plan


# ---- step 9: names Windows rejects ---------------------------------------------------


def renameable_notes(m: Migration, snap: VaultSnapshot) -> list[tuple[Path, Path]]:
    pairs = []
    for note in snap.notes:
        if '"' in note.path.name:
            target = note.path.with_name(texts.straighten_quotes(note.path.name))
            pairs.append((note.path, target))
    return pairs


def step_rename(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(9)
    pairs = renameable_notes(m, snap)
    if not pairs:
        return plan
    renames = {fold(src.stem): dst.stem for src, dst in pairs}
    for note in snap.notes:
        if not any(src.stem in note.text for src, _ in pairs):
            continue
        plan.edits.append(
            FileEdit(
                note.path,
                "migrate",
                "rename:follow-links",
                transform=functools.partial(_retarget_text, renames=renames),
            )
        )
    plan.renames = [(s, d) for s, d in pairs if s.exists() and not d.exists()]
    plan.summary = {"renamed": len(plan.renames), "linkEdits": len(plan.edits)}
    return plan


# ---- step 10: templates --------------------------------------------------------------


def step_templates(m: Migration, snap: VaultSnapshot) -> StepPlan:
    plan = StepPlan(10)
    section = m.profile.raw.get("templates")
    folder = (section.get("dir") if isinstance(section, dict) else None) or "templates"
    for name, text in templates.schema3_template_files(m.profile).items():
        target = m.project_dir / folder / name
        if target.exists():
            if decode_note(target.read_bytes())[0] != text:
                plan.notes.append(f"{name}: exists and differs; left alone")
            continue
        plan.edits.append(FileEdit(target, "create", "template:schema3", new_text=text))
    plan.summary = {"create": len(plan.edits)}
    return plan


STEP_BUILDERS: dict[int, Callable[..., StepPlan]] = {
    1: step_schema,
    2: step_decisions,
    3: step_hub,
    4: step_view,
    5: step_types,
    6: step_exec,
    7: step_runlinks,
    8: step_projection,
    9: step_rename,
    10: step_templates,
}


def build_step(m: Migration, step: int, **kwargs) -> StepPlan:
    return STEP_BUILDERS[step](m, m.snapshot(), **kwargs)


# ---- planning and applying -----------------------------------------------------------


@dataclasses.dataclass
class ResolvedStep:
    plan: StepPlan
    changes: list[Change]

    @property
    def change_count(self) -> int:
        plan = self.plan
        files = len(self.changes) + len(plan.deletes) + len(plan.renames)
        return files + len(plan.imports)


def resolve_step(m: Migration, plan: StepPlan) -> ResolvedStep:
    return ResolvedStep(plan, plan_changes(m.wctx, plan.edits))


def full_diff(change: Change) -> str:
    before = (change.before or b"").decode("utf-8", errors="replace").splitlines()
    after = change.after.decode("utf-8", errors="replace").splitlines()
    return "\n".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="/dev/null" if change.create else f"a/{change.rel}",
            tofile=f"b/{change.rel}",
            lineterm="",
        )
    )


def change_dict(change: Change) -> dict:
    return {
        "path": change.rel,
        "op": change.op,
        "address": change.address,
        "create": change.create,
        "beforeSha256": change.before_sha256,
        "afterSha256": change.after_sha256,
    }


def step_changes_json(m: Migration, resolved: ResolvedStep) -> dict:
    plan = resolved.plan
    rel = lambda p: p.relative_to(m.vault_root).as_posix()  # noqa: E731
    return {
        "step": plan.step,
        "name": plan.name,
        "title": STEP_TITLES[plan.step],
        "summary": plan.summary,
        "changes": [change_dict(c) for c in resolved.changes],
        "deletes": [rel(p) for p in plan.deletes],
        "imports": [{"id": r["id"], "number": r["number"]} for r in plan.imports],
        "renames": [{"from": rel(a), "to": rel(b)} for a, b in plan.renames],
        "notes": plan.notes,
        "problems": plan.problems,
    }


def import_decisions(
    m: Migration, records: list[dict], command: str, warnings: list[dict]
) -> int:
    """Load the table rows into the decision store (one transaction) and export."""
    if not records:
        return 0
    with DecisionStore.open(m.project_id) as store:
        with store.transaction():
            for record in records:
                store.insert_imported(record, actor="migration")
        target = m.project_dir / m.profile.decision_export
        try:
            sync_export(store, m.wctx, target, command=command)
        except ResearchError as error:
            warnings.append({"code": error.code, "message": error.message})
    return len(records)


def apply_resolved(m: Migration, resolved: ResolvedStep) -> dict:
    plan = resolved.plan
    if plan.problems:
        raise ResearchError(
            "MIGRATION_PRECONDITION",
            f"step {plan.step} ({plan.name}) is blocked: "
            + "; ".join(plan.problems[:3]),
            exit_code=1,
            details={"problems": plan.problems},
        )
    applied: list[dict] = []
    warnings: list[dict] = []
    command = f"migrate apply --step {plan.name}"
    if resolved.changes:
        result = apply_changes(
            m.wctx,
            resolved.changes,
            command=command,
            no_quiet=m.no_quiet or plan.external_only,
        )
        applied += result.applied
        warnings += result.warnings
    imported = import_decisions(m, plan.imports, command, warnings)
    renamed = [
        rename_path(m.wctx, src, dst, command=command) for src, dst in plan.renames
    ]
    deleted = (
        journaled_delete(m.wctx, plan.deletes, command=command) if plan.deletes else []
    )
    if (renamed or deleted) and m.wctx.reindex is not None:
        try:
            m.wctx.reindex()
        except ResearchError as error:
            warnings.append({"code": "REINDEX_FAILED", "message": error.message})
    record = {
        "step": plan.step,
        "name": plan.name,
        "appliedAt": now_stamp(),
        "changed": len(applied),
        "imported": imported,
        "renamed": len(renamed),
        "deleted": len(deleted),
    }
    if applied or renamed or deleted or imported:
        log = m.read_json("steps.json", []) or []
        log.append(record)
        m.write_json("steps.json", log)
    return {
        "record": record,
        "applied": applied,
        "imported": imported,
        "renamed": renamed,
        "deleted": deleted,
        "warnings": warnings,
    }


def run_step(m: Migration, step: int, *, apply: bool) -> tuple[dict, dict | None]:
    """(plan json, apply result or None for a dry run)."""
    if apply and step == 7:
        remember_run_stems(m)
    plan = build_step(m, step, save=apply) if step == 2 else build_step(m, step)
    resolved = resolve_step(m, plan)
    data = step_changes_json(m, resolved)
    if not apply:
        return data, None
    return data, apply_resolved(m, resolved)
