"""A checked, project-by-project cutover from the combined decision store."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import sqlite3
import tempfile
from pathlib import Path

from expnote.research.checks import run_checks
from expnote.research.decision_export import (
    plan_export,
    sha256_bytes,
    sync_export,
    target_for,
)
from expnote.research.decision_store import (
    ENGINEERING,
    RESEARCH,
    SCOPES,
    DecisionStore,
    read_store,
    store_exists,
)
from expnote.research.decisions import decision_from_row
from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.view import ProjectView
from expnote.research.writeback import WriteContext, _append_audit

COMMAND = "decision migrate-scopes"
MIGRATION_KEY = "scope_migration"
ARCHIVE_TYPE = "engineering-decision-log"
_ROW_NUMBER = re.compile(r"^\|\s*(\d+)\s*\|")


def _conflict(message: str) -> ResearchError:
    return ResearchError("CONFLICT", message, exit_code=5)


def _invalid(message: str) -> ResearchError:
    return ResearchError("USAGE_SCOPE_MANIFEST", message, exit_code=2)


def _load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.expanduser().read_bytes())
    except (OSError, ValueError) as error:
        raise _invalid(f"cannot read {path}: {error}") from None
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("decisions"), dict
    ):
        raise _invalid("the manifest must contain a decisions object keyed by ID")
    for ident, assignment in manifest["decisions"].items():
        if (
            not isinstance(assignment, dict)
            or assignment.get("scope") not in SCOPES
            or type(assignment.get("version")) is not int
        ):
            raise _invalid(
                f"{ident}: expected research or engineering scope and a version"
            )
    return manifest


def _archive(
    view: ProjectView, spec: dict | None
) -> tuple[bytes | None, dict[int, str]]:
    if spec is None:
        return None, {}
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get("path"), str)
        or not isinstance(spec.get("sha256"), str)
    ):
        raise _invalid("archive needs a material-relative path and sha256")
    root = view.context.resolved.material_dir.resolve()
    path = (root / spec["path"]).resolve()
    if not path.is_relative_to(root) or not any(
        n.path.resolve() == path and n.type == ARCHIVE_TYPE for n in view.snapshot.notes
    ):
        raise _invalid("archive must be a project engineering-decision-log note")
    raw = path.read_bytes()
    if sha256_bytes(raw) != spec["sha256"]:
        raise _conflict("the original engineering archive changed")
    lines = raw.decode("utf-8-sig").splitlines()
    header = next(
        (i for i, line in enumerate(lines) if re.match(r"^\|\s*#\s*\|", line)),
        None,
    )
    if header is None:
        raise _invalid("the engineering archive needs a numbered Markdown table")
    sources = {}
    for line in lines[header + 2 :]:
        match = _ROW_NUMBER.match(line)
        if match:
            number = int(match[1])
            if number in sources:
                raise _invalid(f"duplicate historical source #{number}")
            sources[number] = "\n".join([*lines[header : header + 2], line])
    if not sources:
        raise _invalid("no numbered records in the engineering archive")
    return raw, sources


def _backup(
    store: DecisionStore, targets: list[Path], raw: bytes | None, manifest: dict
) -> Path:
    root = store.path.parent / "migration"
    root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="decision-scopes-", dir=root))
    # The caller holds BEGIN IMMEDIATE. A separate reader can snapshot the
    # committed database before the writer starts changing any of its pages.
    with (
        contextlib.closing(
            sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True)
        ) as source,
        contextlib.closing(sqlite3.connect(backup / "decisions.sqlite")) as copy,
    ):
        source.backup(copy)
    for target, name in zip(
        targets, ("decisions.jsonl", "engineering-decisions.jsonl"), strict=True
    ):
        if target.is_file():
            (backup / name).write_bytes(target.read_bytes())
    if raw is not None:
        (backup / "archive.md").write_bytes(raw)
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return backup


def _validate_submissions(store: DecisionStore, rows: list[dict]) -> None:
    candidate = {row["number"]: row for row in rows}
    tables = {
        row[0]
        for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table in ("plan_submissions", "workflow_proposals"):
        if table not in tables:
            continue
        for (number,) in store.conn.execute(f"SELECT decision FROM {table}"):
            if candidate.get(number, {}).get("scope") != RESEARCH:
                raise _conflict(
                    f"PLAN submission #{number} requires its research decision"
                )


def validate_references(
    view: ProjectView, store: DecisionStore, rows: list[dict]
) -> None:
    """Use the same checks as normal workflow reads, tolerating existing findings."""
    _validate_submissions(store, rows)
    decisions = {row["number"]: decision_from_row(row) for row in rows}
    candidate = dataclasses.replace(
        view.snapshot, decisions=decisions, separated_decisions=True
    )
    before = {
        (f.code, f.where, f.message)
        for f in view.checks().findings
        if f.level == "FAIL"
    }
    after = {
        (f.code, f.where, f.message)
        for f in run_checks(candidate).findings
        if f.level == "FAIL"
    }
    if broken := sorted(after - before):
        raise ResearchError(
            "DECISION_REFERENCES",
            "the proposed scope changes would break existing references",
            exit_code=5,
            details={"findings": broken},
        )


def _prepare(
    view: ProjectView, store: DecisionStore, manifest: dict
) -> tuple[list[dict], bytes | None, dict[int, str]]:
    rows = store.rows()
    assignments = manifest["decisions"]
    if set(assignments) != {row["id"] for row in rows}:
        raise _conflict("the manifest must account for every current decision ID")
    if set(view.snapshot.decisions) != {row["number"] for row in rows}:
        raise _conflict(
            "migrate the legacy Markdown decisions before separating scopes"
        )
    for row in rows:
        assignment = assignments[row["id"]]
        if assignment["version"] != row["version"]:
            raise _conflict(f"{row['id']} changed since the manifest was prepared")
        row["scope"] = assignment["scope"]
    raw, sources = _archive(view, manifest.get("archive"))
    if not sources.keys() <= {row["number"] for row in rows}:
        raise _invalid("the archive contains a decision missing from SQLite")
    validate_references(view, store, rows)
    return rows, raw, sources


def migrate_scopes(
    view: ProjectView, path: Path, *, apply: bool, confirm_command: str
) -> CommandResult:
    manifest = _load_manifest(path)
    fingerprint = sha256_bytes(json.dumps(manifest, sort_keys=True).encode())
    ctx = WriteContext.for_project(view.context)
    targets = [target_for(view.context, scope) for scope in SCOPES]
    if len(set(targets)) != len(SCOPES):
        raise _invalid("the two decision exports must have different paths")
    if not store_exists(view.project_id):
        raise _invalid("a migration needs an existing decision store")
    changes = []
    warnings = []
    applied = False
    backup = None
    try:
        with contextlib.ExitStack() as stack:
            if apply:
                store = stack.enter_context(DecisionStore.open(view.project_id))
                stack.enter_context(store.transaction())
            else:
                store = stack.enter_context(read_store(view.project_id))
                if store is None:
                    raise _invalid("migrate the legacy Markdown decisions first")
            if store.separated:
                if store.meta_get(MIGRATION_KEY) != fingerprint:
                    raise _conflict(
                        "this project's decisions have already been separated"
                    )
                rows = store.rows()
                sources = {n for scope in SCOPES for n in store.archives(scope)}
                backup = store.meta_get("scope_migration_backup")
            else:
                rows, raw, sources = _prepare(view, store, manifest)
                existing_export = plan_export(store, ctx, targets[0])
                if existing_export.problem:
                    raise existing_export.problem
                if targets[1].exists():
                    raise _conflict(
                        f"an engineering export already exists at {targets[1]}"
                    )
                if apply:
                    history = store.events()
                    backup = str(_backup(store, targets, raw, manifest))
                    store.split(
                        {r["number"] for r in rows if r["scope"] == ENGINEERING},
                        sources,
                    )
                    if store.rows() != rows or store.events() != history:
                        raise ResearchError(
                            "ENV_DECISION_MIGRATION",
                            "partitioned decisions or events differ from the source",
                            exit_code=6,
                        )
                    store.meta_set(MIGRATION_KEY, fingerprint)
                    store.meta_set("scope_migration_backup", backup)
                    applied = True
        # Export is derived from the committed transaction. Repeating an apply
        # with the same manifest can finish a partially written export.
        if apply:
            with DecisionStore.open(view.project_id) as store:
                for scope, target in zip(SCOPES, targets, strict=True):
                    try:
                        _, change = sync_export(
                            store, ctx, target, scope=scope, command=COMMAND
                        )
                    except (OSError, ResearchError) as error:
                        warnings.append(
                            {
                                "code": getattr(error, "code", "EXPORT_IO"),
                                "message": (
                                    f"{error}; retry the export with --scope {scope}"
                                ),
                            }
                        )
                    else:
                        if change:
                            changes.append(change)
            if applied:
                try:
                    _append_audit(
                        ctx,
                        {
                            "command": COMMAND,
                            "op": "decision-scopes",
                            "path": "decisions.sqlite",
                            "backup": backup,
                        },
                    )
                except OSError as error:
                    warnings.append({"code": "AUDIT_LOG_IO", "message": str(error)})
    except (OSError, sqlite3.Error, UnicodeDecodeError) as error:
        raise ResearchError(
            "ENV_DECISION_MIGRATION",
            f"could not separate decisions: {error}",
            exit_code=6,
        ) from None
    dry_run = not apply and not view.snapshot.separated_decisions
    return CommandResult(
        {
            "scopes": {
                scope: sum(r["scope"] == scope for r in rows) for scope in SCOPES
            },
            "decisions": [
                {"id": r["id"], "scope": r["scope"], "version": r["version"]}
                for r in rows
            ],
            "archivedSources": len(sources),
            "backup": backup,
            "exports": [str(target) for target in targets],
            "changes": changes,
            "applied": applied or bool(changes),
            "dryRun": dry_run,
            "confirmCommand": confirm_command if dry_run else None,
        },
        warnings,
        view.meta(),
        exit_code=4 if dry_run else 0,
    )
