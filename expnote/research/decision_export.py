"""The decision export: a derived JSONL file in the vault, and restore from it.

After every committed change the tool rewrites `<project>/<paths.decision_export>`
(default `ops/decisions.jsonl`). The file is a backup the vault's sync client carries
off the machine, not a view and not hand-edited: restoring the SQLite store from it is
its only reader. It is written only when its bytes would change. A file whose sha256 is
not the one recorded at the last export was changed by someone else, so the tool
reports `EXPORT_DIVERGED` and leaves it alone unless told to `--force`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from expnote.research.decision_store import (
    COLUMNS,
    ENGINEERING,
    EVENT_COLUMNS,
    JSON_COLUMNS,
    RESEARCH,
    DecisionStore,
    read_store,
    store_path,
)
from expnote.research.errors import ResearchError
from expnote.research.writeback import (
    Change,
    WriteContext,
    plan_owned_file,
    write_owned_file,
)

META_EXPORT_SHA = "export_sha256"


def target_for(context, scope: str = RESEARCH) -> Path:
    relative = (
        "ops/engineering-decisions.jsonl"
        if scope == ENGINEERING
        else context.profile.decision_export
    )
    return context.resolved.material_dir / relative


def export_key(scope: str) -> str:
    return "engineering_export_sha256" if scope == ENGINEERING else META_EXPORT_SHA


def export_bytes(
    rows: list[dict],
    events: list[dict],
    *,
    scope: str | None = None,
    sources: dict[int, str] | None = None,
) -> bytes:
    """Deterministic JSONL: decisions by number, then events by seq."""
    lines = [
        {"kind": "decision", **{c: row[c] for c in COLUMNS}}
        for row in sorted(rows, key=lambda r: r["number"])
    ]
    lines += [
        {"kind": "event", **{c: event[c] for c in EVENT_COLUMNS}}
        for event in sorted(events, key=lambda e: e["seq"])
    ]
    if scope is not None:
        lines.insert(0, {"kind": "decision-scope", "scope": scope})
        lines.extend(
            {"kind": "historical-source", "number": number, "markdown": markdown}
            for number, markdown in sorted((sources or {}).items())
        )
    text = "".join(
        json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n" for line in lines
    )
    return text.encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_export(
    data: bytes, *, scope: str | None = None, sources: dict[int, str] | None = None
) -> tuple[list[dict], list[dict]]:
    """(rows, events) of an export file; anything malformed is an error."""
    rows: list[dict] = []
    events: list[dict] = []
    found_scope = None
    archive = {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid("expected UTF-8") from None
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(line)
            kind = record.pop("kind")
        except (ValueError, KeyError):
            raise _invalid(f"line {number} is not an export record") from None
        if kind == "decision-scope":
            if number != 1 or scope is None or record.get("scope") != scope:
                raise _invalid(
                    f"line {number}: export does not match the requested decision scope"
                )
            found_scope = scope
            continue
        if kind == "historical-source" and found_scope is not None:
            ident, body = record.get("number"), record.get("markdown")
            if type(ident) is not int or ident in archive or not isinstance(body, str):
                raise _invalid(f"line {number}: invalid or duplicate historical source")
            archive[ident] = body
            continue
        if kind == "decision":
            columns, bucket = COLUMNS, rows
        elif kind == "event":
            columns, bucket = EVENT_COLUMNS, events
        else:
            raise _invalid(f"line {number}: unknown kind {kind!r}")
        missing = [c for c in columns if c not in record]
        if missing:
            raise _invalid(f"line {number}: missing {missing}")
        bucket.append({c: record[c] for c in columns})
    for row in rows:
        for column in JSON_COLUMNS:
            if not isinstance(row[column], list):
                raise _invalid(f"decision #{row['number']}: {column} must be a list")
    if scope is not None:
        if found_scope is None:
            raise _invalid("expected an export with a decision-scope header")
        numbers = [row["number"] for row in rows]
        if (
            any(type(number) is not int or number < 1 for number in numbers)
            or len(set(numbers)) != len(numbers)
            or any(event["number"] not in numbers for event in events)
            or not archive.keys() <= set(numbers)
        ):
            raise _invalid("duplicate decision numbers or orphaned history")
        if sources is not None:
            sources.update(archive)
    return rows, events


def _invalid(message: str) -> ResearchError:
    return ResearchError(
        "EXPORT_INVALID", f"the decision export is not valid: {message}", exit_code=2
    )


def diverged(path: Path, found: str, recorded: str | None) -> ResearchError:
    return ResearchError(
        "EXPORT_DIVERGED",
        f"{path.name} was changed outside expnote (sha256 {found[:12]}, last export "
        f"{(recorded or 'none')[:12]}); it was left alone",
        exit_code=5,
        remediation="inspect it, then `expnote decision export --apply --force`",
        details={"path": str(path), "currentSha256": found, "recordedSha256": recorded},
    )


@dataclasses.dataclass
class ExportPlan:
    """What an export would do, computed from the store as it is right now."""

    change: Change | None  # None: the file already holds exactly these bytes
    data: bytes
    sha256: str
    problem: ResearchError | None = None  # EXPORT_DIVERGED unless forced


def plan_export(
    store: DecisionStore,
    ctx: WriteContext,
    target: Path,
    *,
    force: bool = False,
    scope: str = RESEARCH,
) -> ExportPlan:
    data = export_bytes(
        store.rows(scope),
        store.events(scope=scope),
        scope=scope if store.separated else None,
        sources=store.archives(scope),
    )
    change = plan_owned_file(ctx, target, data)
    problem = None
    if change is not None and change.before_sha256 is not None and not force:
        recorded = store.meta_get(export_key(scope))
        if change.before_sha256 != recorded:
            problem = diverged(target, change.before_sha256, recorded)
    return ExportPlan(change, data, sha256_bytes(data), problem)


def sync_export(
    store: DecisionStore,
    ctx: WriteContext,
    target: Path,
    *,
    command: str,
    force: bool = False,
    scope: str = RESEARCH,
) -> tuple[ExportPlan, dict | None]:
    """Write the export if it changed. Raises EXPORT_DIVERGED unless `force`."""
    with store.transaction():
        plan = plan_export(store, ctx, target, force=force, scope=scope)
        if plan.problem is not None:
            raise plan.problem
        record = None
        if plan.change is not None:
            record = write_owned_file(ctx, plan.change, command=command)
        if store.meta_get(export_key(scope)) != plan.sha256:
            store.meta_set(export_key(scope), plan.sha256)
    return plan, record


def export_state(
    project_id: str, ctx: WriteContext, target: Path, *, scope: str = RESEARCH
) -> dict:
    """Doctor/`decision export` view of the store and whether the export is current."""
    path = store_path(project_id)
    state: dict = {"store": str(path), "exists": path.is_file(), "rows": 0}
    if not path.is_file():
        state.update(exportFile=str(target), inSync=None, status="no-store")
        return state
    with read_store(project_id) as store:
        assert store is not None
        plan = plan_export(store, ctx, target, scope=scope)
        state["rows"] = store.count(scope)
    if plan.problem is not None:
        status = "diverged"
    elif plan.change is None:
        status = "in-sync"
    else:
        status = "stale" if plan.change.before is not None else "missing"
    state.update(exportFile=str(target), inSync=status == "in-sync", status=status)
    return state


def restore_rows(
    store: DecisionStore,
    rows: list[dict],
    events: list[dict],
    *,
    replace: bool,
    scope: str = RESEARCH,
    sources: dict[int, str] | None = None,
) -> int:
    """Load an export into one scope in the caller's transaction."""
    existing = store.count(scope)
    if existing and not replace:
        raise ResearchError(
            "STORE_NOT_EMPTY",
            f"the decision scope already holds {existing} decision(s)",
            exit_code=5,
            remediation="pass --replace to overwrite them",
        )
    if store.separated:
        other = ENGINEERING if scope == RESEARCH else RESEARCH
        if {row["number"] for row in rows} & {
            row["number"] for row in store.rows(other)
        }:
            raise ResearchError(
                "CONFLICT",
                "decision numbers are already used in the other scope",
                exit_code=5,
            )
    store.replace_all(rows, events, scope=scope, sources=sources)
    return len(rows)
