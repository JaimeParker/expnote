"""The TODO export: a derived JSONL file in the vault, and restore from it.

Same contract as the decision export (`decision_export.py`): after every committed
change the tool rewrites `<project>/<paths.todo_export>` (default `ops/todos.jsonl`), a
backup the vault's sync client carries off the machine, never hand-edited. A file whose
sha256 is not the one recorded at the last export was changed by someone else, so the
tool reports `EXPORT_DIVERGED` and leaves it alone unless told to `--force`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from expnote.research.decision_export import sha256_bytes
from expnote.research.errors import ResearchError
from expnote.research.todo_store import COLUMNS, EVENT_COLUMNS, TodoStore, store_path
from expnote.research.writeback import (
    Change,
    WriteContext,
    plan_owned_file,
    write_owned_file,
)

META_EXPORT_SHA = "export_sha256"
EXPORT_ADDRESS = "todo-export"


def export_bytes(rows: list[dict], events: list[dict]) -> bytes:
    """Deterministic JSONL: TODOs by number, then events by seq."""
    lines = [
        {"kind": "todo", **{c: row[c] for c in COLUMNS}}
        for row in sorted(rows, key=lambda r: r["number"])
    ]
    lines += [
        {"kind": "event", **{c: event[c] for c in EVENT_COLUMNS}}
        for event in sorted(events, key=lambda e: e["seq"])
    ]
    text = "".join(
        json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n" for line in lines
    )
    return text.encode("utf-8")


def parse_export(data: bytes) -> tuple[list[dict], list[dict]]:
    """(rows, events) of an export file; anything malformed is an error."""
    rows: list[dict] = []
    events: list[dict] = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(line)
            kind = record.pop("kind")
        except (ValueError, KeyError):
            raise _invalid(f"line {number} is not an export record") from None
        if kind == "todo":
            columns, bucket = COLUMNS, rows
        elif kind == "event":
            columns, bucket = EVENT_COLUMNS, events
        else:
            raise _invalid(f"line {number}: unknown kind {kind!r}")
        missing = [c for c in columns if c not in record]
        if missing:
            raise _invalid(f"line {number}: missing {missing}")
        bucket.append({c: record[c] for c in columns})
    return rows, events


def _invalid(message: str) -> ResearchError:
    return ResearchError(
        "EXPORT_INVALID", f"the TODO export is not valid: {message}", exit_code=2
    )


def diverged(path: Path, found: str, recorded: str | None) -> ResearchError:
    return ResearchError(
        "EXPORT_DIVERGED",
        f"{path.name} was changed outside expnote (sha256 {found[:12]}, last export "
        f"{(recorded or 'none')[:12]}); it was left alone",
        exit_code=5,
        remediation="inspect it, then `expnote todo export --apply --force`",
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
    store: TodoStore, ctx: WriteContext, target: Path, *, force: bool = False
) -> ExportPlan:
    data = export_bytes(store.rows(), store.events())
    change = plan_owned_file(ctx, target, data, EXPORT_ADDRESS)
    problem = None
    if change is not None and change.before_sha256 is not None and not force:
        recorded = store.meta_get(META_EXPORT_SHA)
        if change.before_sha256 != recorded:
            problem = diverged(target, change.before_sha256, recorded)
    return ExportPlan(change, data, sha256_bytes(data), problem)


def sync_export(
    store: TodoStore,
    ctx: WriteContext,
    target: Path,
    *,
    command: str,
    force: bool = False,
) -> tuple[ExportPlan, dict | None]:
    """Write the export if it changed. Raises EXPORT_DIVERGED unless `force`."""
    plan = plan_export(store, ctx, target, force=force)
    if plan.problem is not None:
        raise plan.problem
    record = None
    if plan.change is not None:
        record = write_owned_file(ctx, plan.change, command=command)
    if store.meta_get(META_EXPORT_SHA) != plan.sha256:
        store.meta_set(META_EXPORT_SHA, plan.sha256)
    return plan, record


def export_state(project_id: str, ctx: WriteContext, target: Path) -> dict:
    """Doctor/`todo export` view of the store and whether the export is current."""
    path = store_path(project_id)
    state: dict = {"store": str(path), "exists": path.is_file(), "rows": 0}
    if not path.is_file():
        state.update(exportFile=str(target), inSync=None, status="no-store")
        return state
    with TodoStore.open(project_id) as store:
        plan = plan_export(store, ctx, target)
        state["rows"] = store.count()
    if plan.problem is not None:
        status = "diverged"
    elif plan.change is None:
        status = "in-sync"
    else:
        status = "stale" if plan.change.before is not None else "missing"
    state.update(exportFile=str(target), inSync=status == "in-sync", status=status)
    return state


def restore_rows(
    store: TodoStore, rows: list[dict], events: list[dict], *, replace: bool
) -> int:
    """Load an export into the store (inside a transaction); returns rows loaded."""
    if store.count() and not replace:
        raise ResearchError(
            "STORE_NOT_EMPTY",
            f"the TODO store already holds {store.count()} TODO(s)",
            exit_code=5,
            remediation="pass --replace to overwrite them",
        )
    store.replace_all(rows, events)
    return len(rows)
