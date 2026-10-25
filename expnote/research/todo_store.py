"""The manual TODO store: one SQLite file per project, the truth for manual TODOs.

Manual TODOs live in `<data home>/research/projects/<project id>/todos.sqlite`, not in a
vault note; the vault only gets a derived JSONL export (`todo_export.py`). Unlike a
decision, a TODO's text may be edited, but rows are never deleted: a TODO nobody will do
is `dropped` (with a reason), which is terminal. Immutable columns are also guarded by
trigger, and every change is checked against the row's `version` (optimistic
concurrency). Each change appends a `todo_events` row.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from expnote.db import now_iso
from expnote.research.errors import ResearchError
from expnote.research.writeback import project_state_dir

STORE_SCHEMA = 1
STORE_FILE = "todos.sqlite"
BUSY_TIMEOUT_SECONDS = 15
ID_PREFIX = "T"
OWNERS = ("user", "agent")
STATES = ("open", "done", "dropped")
SOURCES = ("cli", "gui", "migrated")
ACTIONS = ("add", "edit", "done", "reopen", "drop", "migrate")
EDITABLE_COLUMNS = ("text", "owner", "plan", "due")
COLUMNS = (
    "number",
    "id",
    "text",
    "owner",
    "plan",
    "due",
    "by",
    "state",
    "done_on",
    "dropped_on",
    "drop_reason",
    "source",
    "created_at",
    "updated_at",
    "version",
)
EVENT_COLUMNS = ("seq", "number", "at", "actor", "action", "before", "after")
IMMUTABLE_COLUMNS = ("number", "id", "by", "source", "created_at")

_QUOTED = ",".join(f'"{c}"' for c in COLUMNS)  # `by` is an SQL keyword
_owner_list = ", ".join(repr(s) for s in OWNERS)
_state_list = ", ".join(repr(s) for s in STATES)
SCHEMA_SQL = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE todos(
    number INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    owner TEXT NOT NULL CHECK(owner IN ({_owner_list})),
    plan TEXT,
    due TEXT,
    "by" TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ({_state_list})),
    done_on TEXT,
    dropped_on TEXT,
    drop_reason TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE todo_events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    before TEXT,
    after TEXT
);
CREATE INDEX todo_events_number ON todo_events(number);
CREATE TRIGGER todos_identity_immutable
BEFORE UPDATE OF {", ".join(f'"{c}"' for c in IMMUTABLE_COLUMNS)} ON todos
BEGIN SELECT RAISE(ABORT, 'todo identity columns are immutable'); END;
CREATE TRIGGER todos_dropped_is_terminal
BEFORE UPDATE ON todos
WHEN OLD.state = 'dropped'
BEGIN SELECT RAISE(ABORT, 'a dropped todo cannot change'); END;
"""


def store_path(project_id: str) -> Path:
    return project_state_dir(project_id) / STORE_FILE


def todo_id(number: int) -> str:
    return f"{ID_PREFIX}-{number:04d}"


def store_exists(project_id: str) -> bool:
    return store_path(project_id).is_file()


def _row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}  # noqa: SIM118 - sqlite3.Row


def _event_dict(row: sqlite3.Row) -> dict:
    out = _row_dict(row)
    for key in ("before", "after"):
        out[key] = json.loads(out[key]) if out[key] is not None else None
    return out


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_rows(project_id: str) -> tuple[list[dict], list[dict]] | None:
    """(todo rows by number, events by seq) read-only; None without a store.

    Never creates the file: a missing store must read as "not migrated yet".
    """
    path = store_path(project_id)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        raise _unreadable(path, error) from None
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            _row_dict(r) for r in conn.execute("SELECT * FROM todos ORDER BY number")
        ]
        events = [
            _event_dict(r)
            for r in conn.execute("SELECT * FROM todo_events ORDER BY seq")
        ]
    except sqlite3.Error as error:
        raise _unreadable(path, error) from None
    finally:
        conn.close()
    return rows, events


def _unreadable(path: Path, error: Exception) -> ResearchError:
    return ResearchError(
        "ENV_TODO_STORE",
        f"cannot read the TODO store {path}: {error}",
        exit_code=6,
        remediation="expnote todo restore --from <ops/todos.jsonl> --apply",
    )


def conflict(ident: str, expected: int, current: int) -> ResearchError:
    return ResearchError(
        "CONFLICT",
        f"{ident} changed since it was read "
        f"(expected version {expected}, now {current})",
        exit_code=5,
        remediation="reload the TODO and try again",
        details={"expectedVersion": expected, "currentVersion": current},
    )


def not_found(ident: str) -> ResearchError:
    return ResearchError(
        "ADDRESS_NOT_FOUND",
        f"no TODO {ident!r}",
        exit_code=3,
        remediation="expnote todo list --source manual --include-done "
        "--include-dropped",
    )


def gate(message: str, code: str = "TODO.TRANSITION") -> ResearchError:
    return ResearchError(
        "GATE_FAILED",
        message,
        exit_code=1,
        details={"gates": [{"code": code, "ok": False, "message": message}]},
    )


class TodoStore:
    """Read/write handle; opening for write creates the file and the schema."""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, project_id: str) -> TodoStore:
        path = store_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        store = cls(conn, path)
        if fresh or not _has_schema(conn):
            conn.executescript(SCHEMA_SQL)
            store.meta_set("schema", str(STORE_SCHEMA))
            store.meta_set("project", project_id)
        return store

    @classmethod
    def open_for_preview(cls, project_id: str) -> TodoStore:
        """The store as it is, or an empty in-memory one before the first write."""
        if store_exists(project_id):
            return cls.open(project_id)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        return cls(conn, Path(":memory:"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> TodoStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """One writer at a time; everything inside commits or rolls back together."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    @contextlib.contextmanager
    def dry_run(self) -> Iterator[None]:
        """A transaction that is always rolled back (for previews)."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        finally:
            self.conn.execute("ROLLBACK")

    # ---- meta ----
    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- reads ----
    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]

    def get(self, number: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM todos WHERE number=?", (number,)
        ).fetchone()
        return _row_dict(row) if row else None

    def rows(self) -> list[dict]:
        return [
            _row_dict(r)
            for r in self.conn.execute("SELECT * FROM todos ORDER BY number")
        ]

    def events(self, number: int | None = None) -> list[dict]:
        sql = "SELECT * FROM todo_events"
        params: tuple = ()
        if number is not None:
            sql += " WHERE number=?"
            params = (number,)
        return [
            _event_dict(r) for r in self.conn.execute(sql + " ORDER BY seq", params)
        ]

    def max_number(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(MAX(number), 0) FROM todos"
        ).fetchone()[0]

    # ---- writes (call inside `transaction()`) ----
    def _add_event(
        self, number: int, at: str, actor: str, action: str, before, after
    ) -> None:
        self.conn.execute(
            "INSERT INTO todo_events(number, at, actor, action, before, after) "
            "VALUES(?,?,?,?,?,?)",
            (
                number,
                at,
                actor,
                action,
                _dump(before) if before is not None else None,
                _dump(after) if after is not None else None,
            ),
        )

    def _write_row(self, row: dict) -> None:
        marks = ",".join("?" * len(COLUMNS))
        self.conn.execute(
            f"INSERT INTO todos({_QUOTED}) VALUES({marks})", [row[c] for c in COLUMNS]
        )

    @staticmethod
    def _complete(record: dict, at: str) -> dict:
        row = {
            "plan": None,
            "due": None,
            "state": "open",
            "done_on": None,
            "dropped_on": None,
            "drop_reason": None,
            "created_at": at,
            "updated_at": at,
            "version": 1,
            **record,
        }
        missing = [c for c in COLUMNS if c not in row]
        if missing:
            raise ValueError(f"todo record lacks {missing}")
        return row

    def insert(
        self,
        record: dict,
        *,
        actor: str,
        at: str | None = None,
        action: str = "add",
    ) -> dict:
        """Add a TODO (`add`), or import one from the old file (`migrate`).

        `record` carries `number`, `id`, `text`, `owner`, `by`, `source` and optionally
        the other columns. The number must be free.
        """
        at = at or now_iso()
        number = record["number"]
        if self.get(number) is not None:
            raise ResearchError(
                "CONFLICT",
                f"TODO #{number} already exists",
                exit_code=5,
                remediation="run the command again to take the next number",
            )
        row = self._complete(record, at)
        self._write_row(row)
        self._add_event(
            number, at, actor, action, None, {"state": row["state"], "version": 1}
        )
        return row

    def _update(
        self,
        row: dict,
        expected_version: int,
        changes: dict,
        *,
        actor: str,
        at: str,
        action: str,
    ) -> dict:
        number = row["number"]
        if row["version"] != expected_version:
            raise conflict(row["id"], expected_version, row["version"])
        if row["state"] == "dropped":
            raise gate(f"{row['id']} is dropped; a dropped TODO cannot change")
        stamped = {**changes, "updated_at": at, "version": row["version"] + 1}
        assignments = ", ".join(f'"{key}"=?' for key in stamped)
        self.conn.execute(
            f"UPDATE todos SET {assignments} WHERE number=? AND version=?",
            (*stamped.values(), number, expected_version),
        )
        self._add_event(
            number,
            at,
            actor,
            action,
            {**{k: row[k] for k in changes}, "version": row["version"]},
            {**changes, "version": row["version"] + 1},
        )
        return self.get(number) or {}

    def edit(
        self,
        number: int,
        changes: dict,
        *,
        expected_version: int,
        actor: str,
        at: str | None = None,
    ) -> dict:
        """Change text, owner, plan or due (a key set to None clears plan/due)."""
        unknown = set(changes) - set(EDITABLE_COLUMNS)
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        row = self._require(number)
        return self._update(
            row,
            expected_version,
            changes,
            actor=actor,
            at=at or now_iso(),
            action="edit",
        )

    def set_done(
        self,
        number: int,
        done: bool,
        *,
        expected_version: int,
        done_on: str | None = None,
        actor: str,
        at: str | None = None,
    ) -> dict:
        """Tick (open -> done) or reopen (done -> open)."""
        at = at or now_iso()
        row = self._require(number)
        if done and row["state"] != "open":
            raise gate(
                f"{row['id']} is {row['state']}; only an open TODO can be ticked"
            )
        if not done and row["state"] != "done":
            raise gate(f"{row['id']} is {row['state']}; only a done TODO can reopen")
        changes = (
            {"state": "done", "done_on": done_on or at[:10]}
            if done
            else {"state": "open", "done_on": None}
        )
        return self._update(
            row,
            expected_version,
            changes,
            actor=actor,
            at=at,
            action="done" if done else "reopen",
        )

    def drop(
        self,
        number: int,
        reason: str,
        *,
        expected_version: int,
        dropped_on: str | None = None,
        actor: str,
        at: str | None = None,
    ) -> dict:
        """open or done -> dropped (terminal); the reason is required."""
        at = at or now_iso()
        if not reason.strip():
            raise ResearchError(
                "USAGE_EMPTY_TEXT", "a drop needs a reason", exit_code=2
            )
        row = self._require(number)
        return self._update(
            row,
            expected_version,
            {
                "state": "dropped",
                "dropped_on": dropped_on or at[:10],
                "drop_reason": reason.strip(),
            },
            actor=actor,
            at=at,
            action="drop",
        )

    def _require(self, number: int) -> dict:
        row = self.get(number)
        if row is None:
            raise not_found(todo_id(number))
        return row

    def replace_all(self, rows: list[dict], events: list[dict]) -> None:
        """Wipe and reload (restore); the triggers still guard later updates."""
        self.conn.execute("DELETE FROM todo_events")
        self.conn.execute("DELETE FROM todos")
        for row in rows:
            self._write_row(row)
        for event in events:
            self.conn.execute(
                "INSERT INTO todo_events"
                "(seq, number, at, actor, action, before, after) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    event["seq"],
                    event["number"],
                    event["at"],
                    event["actor"],
                    event["action"],
                    _dump(event["before"]) if event["before"] is not None else None,
                    _dump(event["after"]) if event["after"] is not None else None,
                ),
            )


def _has_schema(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='todos'"
        ).fetchone()
        is not None
    )
