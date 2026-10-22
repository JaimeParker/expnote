"""M4 `migrate verify` (design 11.3 gates 1, 3-8, 10) and M5 `migrate cleanup`."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from expnote.research import migrate_backup as backup
from expnote.research import migrate_review as review
from expnote.research import migrate_texts as texts
from expnote.research.checks import rules_sha256, run_checks
from expnote.research.decision_export import parse_export, restore_rows
from expnote.research.decision_store import SCHEMA_SQL, DecisionStore, read_rows
from expnote.research.edits import decode_note
from expnote.research.errors import ResearchError
from expnote.research.migrate import (
    SCHEMA2_STEP1_TYPES,
    STEP_NAMES,
    LinkContext,
    Migration,
    build_step,
    legacy_table_text,
    link_context,
    now_stamp,
    offtwo_context,
    resolve_step,
    stored_migrated_rows,
)
from expnote.research.model import Note, parse_note
from expnote.research.snapshot import VaultSnapshot, scan_vault
from expnote.research.textkit import glob_to_regex
from expnote.research.writeback import (
    journaled_delete,
    remove_empty_directory,
    sha256_bytes,
)

PENDING_FAIL_CODES = frozenset(
    {"LIFE.RES_MULTIPLE_ACTIVE", "LIFE.PLAN_RES_COUNT", "LIFE.PLAN_RESULT_MISSING"}
)


_PENDING_EXPLANATION = (
    "legacy plan still waits for its single final result (derived TODO "
    "final-res-pending); not a FAIL until the coordinator drafts it"
)
EXPLAINED_WARNS = {
    "LIFE.PLAN_RESULT_MISSING": _PENDING_EXPLANATION,
    "LIFE.PLAN_RES_COUNT": _PENDING_EXPLANATION,
    "LIFE.RES_MULTIPLE_ACTIVE": _PENDING_EXPLANATION,
    "EV.LABEL_NA_BUT_FILLED": (
        "legacy evidence with a filled label table but no record of who labelled "
        "it: mapped to label-na (design 11.1); the coordinator decides labelled"
    ),
    "DEC.NO_OPTIONS": (
        "decisions at or above strict_from have no options section; the old table "
        "rows had no such column"
    ),
    "PROSE.RAW_NUMBERS": "existing prose of schema-2 results, now checked as schema 3",
}


@dataclasses.dataclass
class Gate:
    gate: str
    name: str
    status: str  # PASS | FAIL | REVIEW
    detail: str
    items: list = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---- golden --------------------------------------------------------------------------


def golden_snapshot(context) -> dict:
    """check findings and link counts of the vault now, under the Off2On profile."""
    snap = scan_vault(offtwo_context(context))
    result = run_checks(snap)
    return {
        "capturedAt": now_stamp(),
        "profile": "embedded Off2On profile (the parity copy's constants)",
        "summary": {
            "fail": sum(f.level == "FAIL" for f in result.findings),
            "warn": sum(f.level == "WARN" for f in result.findings),
            "info": sum(f.level == "INFO" for f in result.findings),
        },
        "stats": result.stats,
        "linkResolution": result.stats.get("links", {}),
        "findings": [
            {"level": f.level, "code": f.code, "where": f.where, "message": f.message}
            for f in result.findings
        ],
    }


def load_golden(m: Migration) -> dict:
    record = m.read_json("backup.json")
    if not record:
        raise ResearchError(
            "MIGRATION_NO_BACKUP", "no backup recorded", exit_code=1,
            remediation="expnote migrate backup --apply",
        )
    path = Path(record["dir"]) / "golden.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---- helpers -------------------------------------------------------------------------


def _pending_plan_rels(m: Migration, snap: VaultSnapshot) -> set[str]:
    status_rows = review.legacy_plan_status(snap)
    pending = {r["path"] for r in status_rows}
    pending |= {
        review.plan_pages(snap)[r["plan"]].rel
        for r in review.missing_final_res(snap, status_rows)
        if r["plan"] in review.plan_pages(snap)
    }
    return pending


def _without_projection(snap: VaultSnapshot, vault_root: Path, roots: list[Path]):
    """The snapshot as if the projection folders were already deleted."""
    prefixes = [r.relative_to(vault_root).as_posix().casefold() + "/" for r in roots]

    def keep(rel: str) -> bool:
        low = rel.casefold()
        return not any(low.startswith(p) for p in prefixes)

    return dataclasses.replace(
        snap,
        md_index={
            k: v2 for k, v in snap.md_index.items() if (v2 := [p for p in v if keep(p)])
        },
        attach_index={
            k: v2
            for k, v in snap.attach_index.items()
            if (v2 := [p for p in v if keep(p)])
        },
        rel_noext={p for p in snap.rel_noext if keep(p)},
        rel_attach={p for p in snap.rel_attach if keep(p)},
    )


# ---- gates ---------------------------------------------------------------------------


def gate_backup(m: Migration) -> Gate:
    record = m.read_json("backup.json")
    if not record:
        return Gate("1", "external backup + restore test", "FAIL", "no backup recorded")
    tar = Path(record["archive"])
    problems = []
    if not tar.is_file():
        problems.append("archive missing")
    elif backup.sha256_file(tar) != record["archiveSha256"]:
        problems.append("archive hash differs from the record")
    if not record.get("restoreOk"):
        problems.append("restore test did not pass")
    if (m.vault_root / ".git").exists() and not record.get("gitHead"):
        problems.append("vault has .git but no HEAD was recorded")
    if not (Path(record["dir"]) / "golden.json").is_file():
        problems.append("golden.json missing")
    detail = (
        f"{record['files']} files, {record['archiveBytes']} bytes, "
        f"git HEAD {record.get('gitHead') or '(none)'}, restore tested"
    )
    return Gate(
        "1",
        "external backup + restore test",
        "FAIL" if problems else "PASS",
        "; ".join(problems) or detail,
    )


def gate_check(m: Migration, snap: VaultSnapshot, golden: dict, strict: bool) -> Gate:
    result = run_checks(snap)
    pending_rels = _pending_plan_rels(m, snap)
    fails = [f for f in result.findings if f.level == "FAIL"]
    # Legacy plans report the result-count codes as WARN; they wait for the final RES.
    pending = [
        f
        for f in result.findings
        if f.level in ("FAIL", "WARN")
        and f.code in PENDING_FAIL_CODES
        and f.where in pending_rels
    ]
    hard = [f for f in fails if f not in pending]
    golden_warns = {
        (f["code"], f["where"]) for f in golden["findings"] if f["level"] == "WARN"
    }
    new_warns = [
        f
        for f in result.findings
        if f.level == "WARN" and (f.code, f.where) not in golden_warns
    ]
    counts = {
        lvl: sum(f.level == lvl for f in result.findings)
        for lvl in ("FAIL", "WARN", "INFO")
    }
    items = [
        {
            "level": f.level,
            "code": f.code,
            "where": f.where,
            "message": f.message[:140],
            "explained": EXPLAINED_WARNS.get(f.code) if f.level == "WARN" else None,
        }
        for f in [*hard, *(pending if strict else []), *new_warns]
    ]
    unexplained = [f for f in new_warns if f.code not in EXPLAINED_WARNS]
    status = "PASS"
    if hard or (strict and pending):
        status = "FAIL"
    elif unexplained:
        status = "REVIEW"
    detail = (
        f"FAIL {counts['FAIL']} (other {len(hard)}) "
        f"WARN-or-FAIL pending final RES / judgement {len(pending)}; "
        f"WARN {counts['WARN']} (new vs golden {len(new_warns)}) "
        f"INFO {counts['INFO']}; "
        f"golden FAIL {golden['summary']['fail']} WARN {golden['summary']['warn']} "
        f"INFO {golden['summary']['info']}"
    )
    return Gate("3", "check: FAIL 0, WARN explained", status, detail, items)


def gate_links(m: Migration, snap: VaultSnapshot, golden: dict) -> Gate:
    roots = [p for p in m.projection_roots() if p.exists()]
    simulated = _without_projection(snap, m.vault_root, roots)
    result = run_checks(simulated)
    now = {
        (f.code, f.where, f.message)
        for f in result.findings
        if f.code.startswith("LINK.")
    }
    before = {
        (f["code"], f["where"], f["message"])
        for f in golden["findings"]
        if f["code"].startswith("LINK.")
    }
    new = sorted(now - before)
    fixed = sorted(before - now)
    detail = (
        f"unresolved after (projection folder treated as deleted): {len(now)}; "
        f"golden: {len(before)}; fixed or removed by design: {len(fixed)}; "
        f"new: {len(new)}"
    )
    return Gate(
        "4",
        "links and embeds resolve",
        "FAIL" if new else "PASS",
        detail,
        [{"code": c, "where": w, "message": msg[:140]} for c, w, msg in new],
    )


def gate_bundles(m: Migration, snap: VaultSnapshot, strict: bool) -> Gate:
    from expnote.research.bundle import build_bundle
    from expnote.research.view import open_view

    pages = {
        pid: page
        for pid, page in review.plan_pages(snap).items()
        if not snap.is_archived(page)
    }
    pending_rels = _pending_plan_rels(m, snap)
    problems, rows = [], []
    with open_view(m.project_id, context=m.context) as view:
        for plan_id in sorted(pages):
            try:
                data, _ = build_bundle(view, plan_id)
            except ResearchError as error:
                problems.append(f"{plan_id}: {error.code}")
                continue
            fails = data["findings"]["fail"]
            pending = pages[plan_id].rel in pending_rels
            rows.append({"plan": plan_id, "fail": fails, "pending": pending})
            if fails and (strict or not pending):
                problems.append(f"{plan_id}: findings.fail = {fails}")
    return Gate(
        "5",
        "plan bundle builds for every plan",
        "FAIL" if problems else "PASS",
        "; ".join(problems) or f"{len(rows)} plans built, findings.fail 0 each",
        rows,
    )


def gate_roundtrip(m: Migration) -> Gate:
    table = legacy_table_text(m, save=False)
    if table is None:
        return Gate("6", "decision round-trip", "FAIL", "no legacy table copy")
    rows = texts.parse_legacy_rows(table, m.profile.legacy_decision_columns)
    stored = stored_migrated_rows(m)
    problems = texts.roundtrip_mismatches(rows, stored)
    problems += _export_restore_problems(m, stored)
    return Gate(
        "6",
        "decision round-trip (store -> table cells; export -> restore)",
        "FAIL" if problems else "PASS",
        f"{len(rows)} rows, {len(stored)} stored, {len(problems)} mismatches",
        problems[:20],
    )


def _export_restore_problems(m: Migration, stored: dict[int, dict]) -> list[str]:
    """Restoring the export into a scratch store must give back identical rows."""
    target = m.project_dir / m.profile.decision_export
    if not stored:
        return []
    if not target.is_file():
        return [f"{m.profile.decision_export} does not exist"]
    try:
        rows, events = parse_export(target.read_bytes())
    except ResearchError as error:
        return [error.message]
    with tempfile.TemporaryDirectory() as scratch:
        conn = sqlite3.connect(Path(scratch) / "restore.sqlite", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        copy = DecisionStore(conn, Path(scratch) / "restore.sqlite")
        with copy.transaction():
            restore_rows(copy, rows, events, replace=False)
        back = copy.rows()
        conn.close()
    live = read_rows(m.project_id)
    problems = []
    if back != (live[0] if live else []):
        problems.append("export -> restore does not reproduce the stored rows")
    if len(rows) != len(live[0] if live else []):
        problems.append("the export holds a different number of decisions")
    return problems


def _original_texts(m: Migration, names: set[str]) -> dict[str, str]:
    record = m.read_json("backup.json")
    raw = backup.read_member_text(Path(record["archive"]), names)
    return {k: decode_note(v)[0] for k, v in raw.items()}


def _in_registry(m: Migration, project_rel_path: str) -> bool:
    """True for notes the steps act on (not templates, not ignored previews)."""
    profile = m.profile
    if profile.template_dirs & set(Path(project_rel_path).parts[:-1]):
        return False
    return not any(
        glob_to_regex(g).match(project_rel_path) for g in profile.ignore_globs
    )


def expected_body(
    m: Migration,
    original: Note,
    *,
    in_registry: bool,
    context: LinkContext,
    in_scope_for_runs: bool,
) -> str:
    """The body the migration's pure transforms leave for an original note."""
    markers = m.profile.forbidden_markers
    body = original.body
    if (
        "schema" in context.applied
        and in_registry
        and original.schema == "2"
        and original.type in SCHEMA2_STEP1_TYPES
        and len(markers) == 2
    ):
        body = texts.excise_marker_block(body, markers[0], markers[1]).text
    return context.rewrite(m, body, runs=in_scope_for_runs)


def gate_body_preservation(m: Migration) -> Gate:
    record = m.read_json("backup.json")
    if not record:
        return Gate("7", "body preservation", "FAIL", "no backup to compare with")
    manifest = backup.read_manifest(Path(record["dir"]))
    project_rel = m.project_dir.relative_to(m.vault_root).as_posix() + "/"
    outside = set(record.get("outsideNotes", []))
    paths = [
        e["path"]
        for e in manifest
        if e["path"].endswith(".md")
        and (e["path"].startswith(project_rel) or e["path"] in outside)
    ]
    originals = _original_texts(m, set(paths))
    context = link_context(m)
    table = m.profile.legacy_decision_table
    skip = {f"{project_rel}{table}"} if table else set()
    skip |= {f"{project_rel}{g}" for g in m.profile.generated_notes}
    skip.add(m.context.resolved.profile_note.relative_to(m.vault_root).as_posix())
    problems, checked = [], 0
    for rel, text in sorted(originals.items()):
        if rel in skip:
            continue
        current_rel = context.new_names.get(rel, rel)
        path = m.vault_root / current_rel
        if not path.is_file():
            continue  # deleted by design
        original = parse_note(path, rel, text)
        want = expected_body(
            m,
            original,
            in_registry=_in_registry(m, rel[len(project_rel) :]),
            context=context,
            in_scope_for_runs=rel.startswith(project_rel) or rel in outside,
        )
        have = parse_note(path, current_rel, decode_note(path.read_bytes())[0]).body
        checked += 1
        if have != want:
            problems.append(f"{rel}: body differs beyond the allowed edits")
    return Gate(
        "7",
        "body preservation (bodies differ only by the listed edits)",
        "FAIL" if problems else "PASS",
        f"{checked} notes compared with the backup, {len(problems)} differ",
        problems[:20],
    )


def gate_rules_hash(m: Migration) -> Gate:
    record = m.read_json("backup.json")
    if not record:
        return Gate("8", "rules_sha256 unchanged", "FAIL", "no backup to compare with")
    manifest = backup.read_manifest(Path(record["dir"]))
    candidates = {e["path"] for e in manifest if e["path"].endswith(".md")}
    originals = _original_texts(m, candidates)
    problems, checked = [], 0
    for rel, text in sorted(originals.items()):
        note = parse_note(m.vault_root / rel, rel, text)
        if note.type != "exec-plan" or note.schema not in ("2", "3"):
            continue
        path = m.vault_root / rel
        if not path.is_file() or "_refactor_preview" in rel:
            continue
        current = parse_note(path, rel, decode_note(path.read_bytes())[0])
        checked += 1
        stored_before = note.get("rules_sha256")
        if stored_before != current.get("rules_sha256"):
            problems.append(f"{rel}: stored rules_sha256 changed")
        if rules_sha256(note.body, m.profile) != rules_sha256(current.body, m.profile):
            problems.append(f"{rel}: recomputed rules hash changed")
    return Gate(
        "8",
        "rules_sha256 unchanged for exec-plans",
        "FAIL" if problems else "PASS",
        f"{checked} exec-plans compared, {len(problems)} changed",
        problems,
    )


def gate_idempotence(m: Migration) -> Gate:
    pending = []
    for step in STEP_NAMES:
        plan = build_step(m, step)
        resolved = resolve_step(m, plan)
        if resolved.change_count:
            pending.append(
                f"step {step} ({plan.name}): {resolved.change_count} change(s)"
            )
    return Gate(
        "10",
        "re-running every step is a no-op",
        "FAIL" if pending else "PASS",
        "; ".join(pending) or "all 10 steps plan zero changes",
    )


def run_verify(m: Migration, *, strict: bool = False) -> list[Gate]:
    snap = m.snapshot()
    golden = load_golden(m)
    return [
        gate_backup(m),
        gate_check(m, snap, golden, strict),
        gate_links(m, snap, golden),
        gate_bundles(m, snap, strict),
        gate_roundtrip(m),
        gate_body_preservation(m),
        gate_rules_hash(m),
        gate_idempotence(m),
    ]


def render_gates(gates: list[Gate]) -> str:
    lines = [f"{'gate':<5}{'status':<8}{'what':<58}detail"]
    for g in gates:
        lines.append(f"{g.gate:<5}{g.status:<8}{g.name[:56]:<58}{g.detail}")
    return "\n".join(lines)


# ---- cleanup (M5) --------------------------------------------------------------------

DELETABLE_DOT_DIRS = {".codex", ".aws"}
CODE_SUFFIXES = (".py", ".pyc")


def cleanup_plan(m: Migration) -> dict:
    """What M5 would delete, what it keeps for the coordinator, and what is unsafe."""
    record = m.read_json("backup.json")
    manifest = {
        e["path"]: e["sha256"] for e in backup.read_manifest(Path(record["dir"]))
    }
    delete_files: list[Path] = []
    remove_dirs: list[Path] = []
    unknown: list[dict] = []
    not_backed_up: list[str] = []

    def rel(path: Path) -> str:
        return path.relative_to(m.vault_root).as_posix()

    def mark_for_deletion(path: Path) -> None:
        if manifest.get(rel(path)) != backup.sha256_file(path):
            not_backed_up.append(rel(path))
        delete_files.append(path)

    def add_tree(base: Path, label: str, *, only_code: bool = False) -> None:
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if only_code and path.suffix not in CODE_SUFFIXES:
                unknown.append({"path": rel(path), "why": f"not code, in {label}"})
                continue
            mark_for_deletion(path)
        remove_dirs.extend(
            sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True)
        )
        remove_dirs.append(base)

    for projection in m.projections():
        if projection.enabled:
            unknown.append(
                {
                    "path": str(projection.config_path),
                    "why": "projection still enabled (run step 8)",
                }
            )
        if projection.root and projection.root.is_dir():
            add_tree(projection.root, "projection folder")
    for preview in sorted(m.project_dir.rglob("_refactor_preview")):
        add_tree(preview, "preview folder")
    section = m.profile.raw.get("templates")
    folder = (section.get("dir") if isinstance(section, dict) else None) or "templates"
    tdir = m.project_dir / folder
    if tdir.is_dir():
        for path in sorted(tdir.glob("*.py")):
            mark_for_deletion(path)
        for name in ("tests", "__pycache__"):
            if (tdir / name).is_dir():
                add_tree(tdir / name, f"{folder}/{name}", only_code=True)
    for dirpath, dirnames, _ in os.walk(m.project_dir):
        for name in list(dirnames):
            if not name.startswith(".") or name == ".obsidian":
                continue
            path = Path(dirpath) / name
            if not any(path.rglob("*")) and name in DELETABLE_DOT_DIRS:
                remove_dirs.append(path)
            else:
                empty = not any(path.rglob("*"))
                why = (
                    "empty dot-directory not on the delete list"
                    if empty
                    else "non-empty dot-directory"
                )
                unknown.append({"path": rel(path), "why": why})
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    return {
        "deleteFiles": delete_files,
        "removeDirs": list(dict.fromkeys(remove_dirs)),
        "unknown": unknown,
        "notBackedUp": not_backed_up,
        "manifest": manifest,
    }


def run_cleanup(m: Migration, *, apply: bool) -> dict:
    gates = run_verify(m)
    failing = [g for g in gates if g.status == "FAIL"]
    if failing:
        raise ResearchError(
            "VERIFY_FAILED",
            "cleanup needs a passing verify; failing gates: "
            + ", ".join(g.gate for g in failing),
            exit_code=1,
            details={"gates": [g.as_dict() for g in gates]},
            remediation="expnote migrate verify",
        )
    plan = cleanup_plan(m)
    if plan["notBackedUp"]:
        raise ResearchError(
            "CLEANUP_NOT_IN_BACKUP",
            f"{len(plan['notBackedUp'])} file(s) to delete are not in the backup",
            exit_code=1,
            details={"files": plan["notBackedUp"][:20]},
        )
    rel = lambda p: p.relative_to(m.vault_root).as_posix()  # noqa: E731
    data = {
        "files": [rel(p) for p in plan["deleteFiles"]],
        "directories": [rel(p) for p in plan["removeDirs"]],
        "unknownKept": plan["unknown"],
        "gates": [g.as_dict() for g in gates],
        "applied": False,
    }
    if not apply:
        return data
    deleted = journaled_delete(
        m.wctx,
        plan["deleteFiles"],
        command="migrate cleanup",
        expected_sha256=plan["manifest"],
    )
    removed = 0
    for directory in plan["removeDirs"]:
        if directory.is_dir() and remove_empty_directory(m.wctx, directory):
            removed += 1
    if m.wctx.reindex is not None:
        try:
            m.wctx.reindex()
        except ResearchError:
            pass
    data.update(applied=True, deletedFiles=len(deleted), removedDirectories=removed)
    return data


def sha_of(path: Path) -> str:
    return sha256_bytes(path.read_bytes())
