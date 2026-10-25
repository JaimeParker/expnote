"""Write planners for manual TODOs (the SQLite store) and the one-shot file migration.

Planners turn a command into a `WritePlan` carrying a `StoreOp` on the TODO store; the
shared `writes.execute_plan` runs the dry-run/apply and rewrites the export. The old
`paths.todo_file` is read only while a project is not migrated: until `todo migrate`
runs, reads come from the file and every write refuses with `TODO_NOT_MIGRATED`.
"""

from __future__ import annotations

import re
from pathlib import Path

from expnote.research import writes
from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.extract import TODO_FIELD_RE, TodoRecord, parse_todo_lines
from expnote.research.todo_export import plan_export, sync_export
from expnote.research.todo_store import (
    ID_PREFIX,
    OWNERS,
    TodoStore,
    conflict,
    gate,
    not_found,
    read_rows,
    store_exists,
    store_path,
    todo_id,
)
from expnote.research.view import ProjectView
from expnote.research.writeback import (
    WriteContext,
    journaled_delete,
    sha256_bytes,
    vault_rel,
)

TODO_BACKEND = writes.StoreBackend(
    TodoStore.open,
    TodoStore.open_for_preview,
    store_path,
    plan_export,
    sync_export,
    lambda view: view.context.resolved.project_dir / view.profile.todo_export,
    "expnote todo export --apply",
)
_ID_RE = re.compile(rf"{ID_PREFIX}-(\d+)", re.IGNORECASE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
KNOWN_FILE_FIELDS = frozenset({"id", "plan", "due", "by", "owner", "done"})
NOT_MIGRATED_REMEDIATION = "expnote todo migrate --apply"


# ---- the old file and the backend choice -------------------------------------


def todo_file_path(view: ProjectView) -> Path | None:
    name = view.profile.todo_file
    return view.context.resolved.project_dir / name if name else None


def legacy_tasks(view: ProjectView) -> list[TodoRecord]:
    """Task lines of the old TODO file as it is on disk now (empty without one)."""
    path = todo_file_path(view)
    if path is None or not path.is_file():
        return []
    return parse_todo_lines(path.read_text(encoding="utf-8"))


def require_store(view: ProjectView) -> None:
    """Writes need the store, or no old file with tasks that would be left behind."""
    if store_exists(view.project_id):
        return
    path = todo_file_path(view)
    if path is not None and legacy_tasks(view):
        raise ResearchError(
            "TODO_NOT_MIGRATED",
            f"the manual TODOs still live in {path.name}; they move to the TODO "
            "store first",
            exit_code=2,
            remediation=NOT_MIGRATED_REMEDIATION,
        )


def parse_todo_ref(ident: str) -> int:
    match = _ID_RE.fullmatch(ident.strip())
    if match is None:
        raise not_found(ident)
    return int(match.group(1))


def _current_row(view: ProjectView, ident: str) -> dict:
    loaded = read_rows(view.project_id)
    number = parse_todo_ref(ident)
    rows = loaded[0] if loaded else []
    row = next((r for r in rows if r["number"] == number), None)
    if row is None:
        raise not_found(ident)
    return row


# ---- validation ----------------------------------------------------------------


def _usage(code: str, message: str) -> ResearchError:
    return ResearchError(code, message, exit_code=2)


def _check_text(text: str) -> str:
    if TODO_FIELD_RE.search(text):
        raise _usage(
            "USAGE_BAD_VALUE",
            "the task text may not contain `[key:: value]` fields; use the options",
        )
    if "\n" in text or "\r" in text:
        raise _usage("USAGE_MULTILINE_VALUE", "the task text must be one line")
    if not text.strip():
        raise _usage("USAGE_EMPTY_TEXT", "the task text is empty")
    return text.strip()


def _check_owner(owner: str | None) -> None:
    if owner is not None and owner not in OWNERS:
        raise _usage("USAGE_BAD_OPTION", "--owner takes one of user, agent")


def _check_due(due: str | None) -> None:
    if due is not None and not _DATE_RE.fullmatch(due):
        raise _usage("USAGE_BAD_OPTION", "--due takes YYYY-MM-DD")


def _check_plan(plan: str | None) -> str | None:
    if plan is None:
        return None
    if not plan.strip() or "\n" in plan:
        raise _usage("USAGE_BAD_VALUE", "--plan is not a plan id")
    return plan.strip()


def _summary(pairs: list[tuple[str, object, object]]) -> str:
    lines: list[str] = []
    for name, old, new in pairs:
        if old is not None:
            lines.append(f"-{name}: {old}")
        if new is not None:
            lines.append(f"+{name}: {new}")
    return "\n".join(lines)


# ---- planners ------------------------------------------------------------------


def plan_todo_add(
    view: ProjectView,
    text: str,
    *,
    plan: str | None = None,
    due: str | None = None,
    by: str = "agent",
    owner: str | None = None,
    source: str = "cli",
) -> writes.WritePlan:
    text = _check_text(text)
    _check_due(due)
    _check_owner(owner)
    plan = _check_plan(plan)
    if by not in OWNERS:
        raise _usage("USAGE_BAD_OPTION", "--by takes one of user, agent")
    if owner is None and by == "agent":
        raise _usage(
            "USAGE_MISSING_OPTION",
            "an agent adding a task must pass --owner: `--owner agent` for the "
            "agent's own work, `--owner user` when the user must act (the text "
            "then starts with the user's action)",
        )
    require_store(view)
    loaded = read_rows(view.project_id)
    number = max((r["number"] for r in loaded[0]), default=0) + 1 if loaded else 1
    ident = todo_id(number)
    record = {
        "number": number,
        "id": ident,
        "text": text,
        "owner": owner or "user",
        "plan": plan,
        "due": due,
        "by": by,
        "source": source,
    }
    summary = "\n".join(
        f"+{key}: {value}" for key, value in record.items() if value is not None
    )
    op = writes.StoreOp(
        "todo-add",
        f"todo:{ident}",
        summary,
        lambda store, actor, at: store.insert(record, actor=actor, at=at),
        TODO_BACKEND,
    )
    return writes.WritePlan([], {"id": ident, "number": number}, store_op=op)


def plan_todo_edit(
    view: ProjectView,
    ident: str,
    *,
    text: str | None = None,
    owner: str | None = None,
    plan: str | None = None,
    clear_plan: bool = False,
    due: str | None = None,
    clear_due: bool = False,
    expected_version: int | None = None,
) -> writes.WritePlan:
    if all(c in (None, False) for c in (text, owner, plan, clear_plan, due, clear_due)):
        raise _usage(
            "USAGE_MISSING_OPTION",
            "give at least one of --text, --owner, --plan, --clear-plan, --due, "
            "--clear-due",
        )
    if plan is not None and clear_plan:
        raise _usage("USAGE_BAD_OPTION", "--plan and --clear-plan conflict")
    if due is not None and clear_due:
        raise _usage("USAGE_BAD_OPTION", "--due and --clear-due conflict")
    _check_owner(owner)
    _check_due(due)
    wanted: dict = {}
    if text is not None:
        wanted["text"] = _check_text(text)
    if owner is not None:
        wanted["owner"] = owner
    if plan is not None:
        wanted["plan"] = _check_plan(plan)
    if clear_plan:
        wanted["plan"] = None
    if due is not None:
        wanted["due"] = due
    if clear_due:
        wanted["due"] = None
    require_store(view)
    row = _current_row(view, ident)
    ident = row["id"]
    if row["state"] == "dropped":
        raise gate(f"{ident} is dropped; a dropped TODO cannot change")
    changes = {k: v for k, v in wanted.items() if row[k] != v}
    if not changes:
        return writes.WritePlan([], {"id": ident})
    _check_version(row, expected_version)
    version = row["version"] if expected_version is None else expected_version
    number = row["number"]
    op = writes.StoreOp(
        "todo-edit",
        f"todo:{ident}",
        _summary([(k, row[k], v) for k, v in changes.items()]),
        lambda store, actor, at: store.edit(
            number, changes, expected_version=version, actor=actor, at=at
        ),
        TODO_BACKEND,
    )
    return writes.WritePlan([], {"id": ident, "changed": sorted(changes)}, store_op=op)


def plan_todo_done(
    view: ProjectView,
    ident: str,
    *,
    undo: bool = False,
    expected_version: int | None = None,
    today: str | None = None,
) -> writes.WritePlan:
    require_store(view)
    row = _current_row(view, ident)
    ident = row["id"]
    if row["state"] == "dropped":
        raise gate(f"{ident} is dropped; a dropped TODO cannot be ticked or reopened")
    wanted = "open" if undo else "done"
    if row["state"] == wanted:
        return writes.WritePlan([], {"id": ident, "done": not undo})
    _check_version(row, expected_version)
    version = row["version"] if expected_version is None else expected_version
    number = row["number"]
    date = today or writes.written_date(view)
    op = writes.StoreOp(
        "todo-reopen" if undo else "todo-done",
        f"todo:{ident}",
        _summary([("state", row["state"], wanted)]),
        lambda store, actor, at: store.set_done(
            number, not undo, expected_version=version, done_on=date, actor=actor, at=at
        ),
        TODO_BACKEND,
    )
    return writes.WritePlan([], {"id": ident, "done": not undo}, store_op=op)


def plan_todo_drop(
    view: ProjectView,
    ident: str,
    *,
    reason: str,
    expected_version: int | None = None,
    today: str | None = None,
) -> writes.WritePlan:
    if not reason.strip():
        raise _usage("USAGE_EMPTY_TEXT", "--reason is empty: say why it is dropped")
    if "\n" in reason or "\r" in reason:
        raise _usage("USAGE_MULTILINE_VALUE", "--reason must be one line")
    require_store(view)
    row = _current_row(view, ident)
    ident = row["id"]
    if row["state"] == "dropped":
        return writes.WritePlan([], {"id": ident, "dropped": True})
    _check_version(row, expected_version)
    version = row["version"] if expected_version is None else expected_version
    number = row["number"]
    date = today or writes.written_date(view)
    op = writes.StoreOp(
        "todo-drop",
        f"todo:{ident}",
        _summary([("state", row["state"], "dropped"), ("drop_reason", None, reason)]),
        lambda store, actor, at: store.drop(
            number,
            reason,
            expected_version=version,
            dropped_on=date,
            actor=actor,
            at=at,
        ),
        TODO_BACKEND,
    )
    return writes.WritePlan([], {"id": ident, "dropped": True}, store_op=op)


def _check_version(row: dict, expected: int | None) -> None:
    if expected is not None and expected != row["version"]:
        raise conflict(row["id"], expected, row["version"])


# ---- migration of the old file ---------------------------------------------------


def build_import(
    records: list[TodoRecord], stamp: str
) -> tuple[list[dict], list[dict]]:
    """(rows by number, warnings). `T-n` ids stay unless repeated; the rest get new."""
    kept: dict[int, TodoRecord] = {}
    pending: list[TodoRecord] = []
    warnings: list[dict] = []
    for record in records:
        match = None if record.synthetic else _ID_RE.fullmatch(record.todo_id)
        if match is not None and int(match.group(1)) not in kept:
            kept[int(match.group(1))] = record
            continue
        pending.append(record)
        if not record.synthetic:
            warnings.append(
                {
                    "code": "TODO_ID_REASSIGNED",
                    "message": f"line {record.line}: id {record.todo_id!r} is not "
                    f"{ID_PREFIX}-<n> or is used twice; it gets a new id",
                }
            )
    numbers: dict[int, TodoRecord] = dict(kept)
    next_number = max(kept, default=0) + 1
    for record in pending:
        numbers[next_number] = record
        next_number += 1
    rows = []
    for number, record in sorted(numbers.items()):
        fields = record.fields
        unknown = sorted(set(fields) - KNOWN_FILE_FIELDS)
        if unknown:
            warnings.append(
                {
                    "code": "TODO_UNKNOWN_FIELDS",
                    "message": f"{todo_id(number)} (line {record.line}): fields "
                    f"{', '.join(unknown)} are not imported; they stay in the backup",
                }
            )
        owner = fields.get("owner", "user")
        if owner not in OWNERS:
            warnings.append(
                {
                    "code": "TODO_BAD_OWNER",
                    "message": f"{todo_id(number)}: owner {owner!r} is not user or "
                    "agent; imported as user",
                }
            )
            owner = "user"
        done = record.state == "done"
        rows.append(
            {
                "number": number,
                "id": todo_id(number),
                "text": record.text,
                "owner": owner,
                "plan": fields.get("plan") or None,
                "due": fields.get("due") or None,
                "by": fields.get("by") or "user",
                "state": record.state,
                "done_on": (fields.get("done") or None) if done else None,
                "source": "migrated",
                "created_at": stamp,
                "updated_at": stamp,
                "version": 1,
            }
        )
    return rows, warnings


def _refuse(code: str, message: str, status: int, fix: str | None = None):
    return ResearchError(code, message, exit_code=status, remediation=fix)


def migrate_todos(
    view: ProjectView,
    *,
    apply: bool,
    confirm_command: str | None,
    actor: str | None = None,
) -> CommandResult:
    """Import the old file into a new store and export; back the file up, delete it."""
    pid = view.project_id
    if store_exists(pid):
        raise _refuse(
            "TODO_STORE_EXISTS",
            "this project already has a TODO store; the file is ignored",
            5,
            "remove the old TODO file by hand if it is still there",
        )
    path = todo_file_path(view)
    if path is None:
        raise _refuse("PROFILE_KEY_MISSING", "the profile has no paths.todo_file", 2)
    if not path.is_file():
        raise _refuse("NOT_FOUND", f"there is no TODO file {path.name} to migrate", 3)
    payload = path.read_bytes()
    records = parse_todo_lines(payload.decode("utf-8"))
    context = WriteContext.for_project(view.context, actor)
    stamp = writes.written_at(view)
    rows, warnings = build_import(records, stamp)
    rel = vault_rel(context, path)
    target = TODO_BACKEND.export_target(view)

    def load(store: TodoStore) -> None:
        for row in rows:
            store.insert(row, actor=context.actor, at=stamp, action="migrate")

    with TodoStore.open_for_preview(pid) as store, store.dry_run():
        load(store)
        export = plan_export(store, context, target)
    if export.problem is not None:
        raise _refuse(
            "EXPORT_DIVERGED",
            f"{target.name} already exists and was not written by expnote",
            5,
            f"move {target.name} away, then run the command again",
        )
    changes = [
        {
            "path": store_path(pid).name,
            "absPath": str(store_path(pid)),
            "op": "todo-migrate",
            "address": "todo-store",
            "beforeSha256": None,
            "afterSha256": None,
            "unifiedDiff": "\n".join(
                f"+{r['id']} [{r['state']}] ({r['owner']}) {r['text']}" for r in rows
            ),
            "diffTruncated": False,
        },
        *([export.change.as_dict()] if export.change else []),
        {
            "path": rel,
            "absPath": str(path),
            "op": "delete",
            "address": "file",
            "beforeSha256": sha256_bytes(payload),
            "afterSha256": None,
            "unifiedDiff": f"the file is deleted; a copy goes to the journal "
            f"({len(rows)} task line(s) imported)",
            "diffTruncated": False,
        },
    ]
    data = {
        "rows": [
            {k: r[k] for k in ("id", "text", "owner", "state", "plan", "due", "by")}
            | {"doneOn": r["done_on"]}
            for r in rows
        ],
        "file": rel,
    }
    meta = view.meta()
    if not apply:
        data.update(
            changes=changes, applied=False, dryRun=True, confirmCommand=confirm_command
        )
        return CommandResult(data, warnings, meta, exit_code=writes.DRY_RUN_EXIT)
    applied = [changes[0]]
    created = False
    try:
        with TodoStore.open(pid) as store:
            created = True
            with store.transaction():
                load(store)
            _, record = sync_export(store, context, target, command="todo migrate")
            if record is not None:
                applied.append(record)
    except BaseException:
        if created:
            store_path(pid).unlink(missing_ok=True)
        raise
    try:
        removed = journaled_delete(
            context,
            [path],
            command="todo migrate",
            expected_sha256={rel: sha256_bytes(payload)},
        )
    except ResearchError as error:
        error.remediation = (
            f"the store is created; delete {path.name} by hand "
            "(its tasks are already imported)"
        )
        raise
    applied += [{**changes[-1], **item} for item in removed]
    data.update(changes=applied, applied=True, dryRun=False)
    return CommandResult(data, warnings, meta)
