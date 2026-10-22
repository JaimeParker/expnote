"""The decision store: one SQLite file per project, the truth for decisions.

Decisions are records an agent writes after asking the user in chat, so they live in
`<data home>/research/projects/<project id>/decisions.sqlite`, not in vault notes. The
vault only gets a derived JSONL export (`decision_export.py`). Every rule about what may
change is enforced here, in one place: text columns never change once a row exists
(also by trigger, so a stray UPDATE fails too), status moves only along
proposed -> confirmed|rejected and confirmed -> superseded, and every transition is
checked against the row's `version` (optimistic concurrency).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from expnote.db import now_iso
from expnote.research.errors import ResearchError
from expnote.research.schemas import DECISION_STATUS
from expnote.research.writeback import project_state_dir

STORE_SCHEMA = 1
STORE_FILE = "decisions.sqlite"
BUSY_TIMEOUT_SECONDS = 15
CONFIRMERS = ("user", "agent-for-user", "legacy")
SOURCES = ("migrated", "cli", "chat", "gui")
FLOW = {
    "proposed": ("confirmed", "rejected"),
    "confirmed": ("superseded",),
    "rejected": (),
    "superseded": (),
}
ACTIONS = {"confirmed": "approve", "rejected": "reject", "superseded": "supersede"}
JSON_COLUMNS = ("plans", "targets", "supersedes")
TEXT_COLUMNS = ("decided", "reason", "options", "lands")
IMMUTABLE_COLUMNS = (
    "number",
    "id",
    "title",
    "date",
    "layer",
    *TEXT_COLUMNS,
    *JSON_COLUMNS,
    "legacy",
    "status_raw",
    "review",
    "review_by",
    "source",
    "created_at",
)
COLUMNS = (
    "number",
    "id",
    "title",
    "date",
    "status",
    "layer",
    *TEXT_COLUMNS,
    *JSON_COLUMNS,
    "superseded_by",
    "confirmed_by",
    "confirmed_on",
    "authorization",
    "legacy",
    "status_raw",
    "review",
    "review_by",
    "source",
    "created_at",
    "updated_at",
    "version",
)
EVENT_COLUMNS = ("seq", "number", "at", "actor", "action", "before", "after")

_STATUS_LIST = ", ".join(repr(s) for s in DECISION_STATUS)
SCHEMA_SQL = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE decisions(
    number INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ({_STATUS_LIST})),
    layer TEXT NOT NULL,
    decided TEXT NOT NULL,
    reason TEXT NOT NULL,
    options TEXT NOT NULL,
    lands TEXT NOT NULL,
    plans TEXT NOT NULL,
    targets TEXT NOT NULL,
    supersedes TEXT NOT NULL,
    superseded_by INTEGER,
    confirmed_by TEXT,
    confirmed_on TEXT,
    authorization TEXT,
    legacy INTEGER NOT NULL,
    status_raw TEXT NOT NULL,
    review TEXT,
    review_by TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE decision_events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    before TEXT,
    after TEXT
);
CREATE INDEX decision_events_number ON decision_events(number);
CREATE TRIGGER decisions_text_immutable
BEFORE UPDATE OF {", ".join(IMMUTABLE_COLUMNS)} ON decisions
BEGIN SELECT RAISE(ABORT, 'decision text is immutable; supersede it instead'); END;
CREATE TRIGGER decisions_status_flow
BEFORE UPDATE OF status ON decisions
WHEN NOT (
    OLD.status = NEW.status
    OR (OLD.status = 'proposed' AND NEW.status IN ('confirmed', 'rejected'))
    OR (OLD.status = 'confirmed' AND NEW.status = 'superseded')
)
BEGIN SELECT RAISE(ABORT, 'illegal decision status transition'); END;
"""


def store_path(project_id: str) -> Path:
    return project_state_dir(project_id) / STORE_FILE


def decision_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number:04d}"


def store_exists(project_id: str) -> bool:
    return store_path(project_id).is_file()


def _row_dict(row: sqlite3.Row) -> dict:
    out = {key: row[key] for key in row.keys()}  # noqa: SIM118 - sqlite3.Row
    for key in JSON_COLUMNS:
        out[key] = json.loads(out[key])
    out["legacy"] = bool(out["legacy"])
    return out


def _event_dict(row: sqlite3.Row) -> dict:
    out = {key: row[key] for key in row.keys()}  # noqa: SIM118 - sqlite3.Row
    for key in ("before", "after"):
        out[key] = json.loads(out[key]) if out[key] is not None else None
    return out


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_rows(project_id: str) -> tuple[list[dict], list[dict]] | None:
    """(decision rows by number, events by seq) read-only; None without a store.

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
            _row_dict(r)
            for r in conn.execute("SELECT * FROM decisions ORDER BY number")
        ]
        events = [
            _event_dict(r)
            for r in conn.execute("SELECT * FROM decision_events ORDER BY seq")
        ]
    except sqlite3.Error as error:
        raise _unreadable(path, error) from None
    finally:
        conn.close()
    return rows, events


def _unreadable(path: Path, error: Exception) -> ResearchError:
    return ResearchError(
        "ENV_DECISION_STORE",
        f"cannot read the decision store {path}: {error}",
        exit_code=6,
        remediation="expnote decision restore --from <ops/decisions.jsonl> --apply",
    )


def _conflict(number: int, expected: int, current: int) -> ResearchError:
    return ResearchError(
        "CONFLICT",
        f"decision #{number} changed since it was read "
        f"(expected version {expected}, now {current})",
        exit_code=5,
        remediation="reload the decision and decide again",
        details={"expectedVersion": expected, "currentVersion": current},
    )


def _not_found(number: int) -> ResearchError:
    return ResearchError(
        "NOT_FOUND",
        f"no decision #{number}",
        exit_code=3,
        remediation="expnote decision list",
    )


def _gate(message: str, code: str = "DEC.TRANSITION") -> ResearchError:
    return ResearchError(
        "GATE_FAILED",
        message,
        exit_code=1,
        details={"gates": [{"code": code, "ok": False, "message": message}]},
    )


class DecisionStore:
    """Read/write handle; opening for write creates the file and the schema."""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, project_id: str) -> DecisionStore:
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
    def open_for_preview(cls, project_id: str) -> DecisionStore:
        """The store as it is, or an empty in-memory one before the first write."""
        if store_exists(project_id):
            return cls.open(project_id)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        return cls(conn, Path(":memory:"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> DecisionStore:
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
        return self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    def get(self, number: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE number=?", (number,)
        ).fetchone()
        return _row_dict(row) if row else None

    def rows(self) -> list[dict]:
        return [
            _row_dict(r)
            for r in self.conn.execute("SELECT * FROM decisions ORDER BY number")
        ]

    def events(self, number: int | None = None) -> list[dict]:
        sql = "SELECT * FROM decision_events"
        params: tuple = ()
        if number is not None:
            sql += " WHERE number=?"
            params = (number,)
        cursor = self.conn.execute(sql + " ORDER BY seq", params)
        return [_event_dict(r) for r in cursor]

    def max_number(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(number), 0) FROM decisions")
        return row.fetchone()[0]

    # ---- writes (call inside `transaction()`) ----
    def _add_event(
        self, number: int, at: str, actor: str, action: str, before, after
    ) -> None:
        self.conn.execute(
            "INSERT INTO decision_events(number, at, actor, action, before, after) "
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

    def insert(
        self,
        record: dict,
        *,
        actor: str,
        at: str | None = None,
        action: str | None = None,
    ) -> dict:
        """Add a decision as proposed, or as confirmed when recorded from chat.

        `record` carries the immutable columns plus, for a confirmed record,
        `confirmed_by`/`confirmed_on`/`authorization`. The number must be free.
        """
        at = at or now_iso()
        status = record.get("status", "proposed")
        if status not in ("proposed", "confirmed"):
            raise _gate(f"a new decision starts proposed or confirmed, not {status!r}")
        if status == "confirmed" and record.get("confirmed_by") not in CONFIRMERS:
            raise _gate("a confirmed record needs confirmed_by", "DEC.AGENT_NO_AUTH")
        if (
            status == "confirmed"
            and record.get("confirmed_by") == "agent-for-user"
            and not str(record.get("authorization") or "").strip()
        ):
            raise _gate(
                "an agent-recorded confirmation needs the user's words",
                "DEC.AGENT_NO_AUTH",
            )
        number = record["number"]
        if self.get(number) is not None:
            raise ResearchError(
                "CONFLICT",
                f"decision #{number} already exists",
                exit_code=5,
                remediation="run the command again to take the next number",
            )
        fresh = {
            k: v
            for k, v in record.items()
            if k not in ("created_at", "updated_at", "version")
        }
        row = self._complete(fresh, status, at)
        self._write_row(row)
        self._add_event(
            number,
            at,
            actor,
            "record" if status == "confirmed" else "propose",
            None,
            {"status": status, "version": row["version"]},
        )
        return row

    def insert_imported(
        self, record: dict, *, actor: str, at: str | None = None
    ) -> dict:
        """A row from the legacy table in any status (migration only)."""
        at = at or now_iso()
        row = self._complete(record, record["status"], at)
        self._write_row(row)
        self._add_event(
            row["number"],
            at,
            actor,
            "import",
            None,
            {"status": row["status"], "version": row["version"]},
        )
        return row

    @staticmethod
    def _complete(record: dict, status: str, at: str) -> dict:
        row = {
            "superseded_by": None,
            "confirmed_by": None,
            "confirmed_on": None,
            "authorization": None,
            "options": "",
            "lands": "",
            "review": None,
            "review_by": None,
            "status_raw": status,
            "legacy": False,
            "created_at": at,
            "updated_at": at,
            "version": 1,
            **record,
            "status": status,
        }
        missing = [c for c in COLUMNS if c not in row]
        if missing:
            raise ValueError(f"decision record lacks {missing}")
        return row

    def _write_row(self, row: dict) -> None:
        values = [
            _dump(row[c])
            if c in JSON_COLUMNS
            else int(row[c])
            if c == "legacy"
            else row[c]
            for c in COLUMNS
        ]
        marks = ",".join("?" * len(COLUMNS))
        self.conn.execute(
            f"INSERT INTO decisions({','.join(COLUMNS)}) VALUES({marks})", values
        )

    def transition(
        self,
        number: int,
        to: str,
        *,
        expected_version: int,
        actor: str,
        at: str | None = None,
        confirmed_by: str | None = None,
        confirmed_on: str | None = None,
        authorization: str | None = None,
        superseded_by: int | None = None,
    ) -> dict:
        at = at or now_iso()
        row = self.get(number)
        if row is None:
            raise _not_found(number)
        if row["version"] != expected_version:
            raise _conflict(number, expected_version, row["version"])
        if to not in FLOW[row["status"]]:
            raise _gate(
                f"decision #{number} is {row['status']!r}; it cannot become {to}"
            )
        changes: dict = {"status": to}
        if to in ("confirmed", "rejected"):
            if confirmed_by not in ("user", "agent-for-user"):
                raise _gate(
                    "confirming or rejecting needs confirmed_by", "DEC.AGENT_NO_AUTH"
                )
            if confirmed_by == "agent-for-user" and not (authorization or "").strip():
                raise _gate(
                    "an agent-recorded decision needs the user's words",
                    "DEC.AGENT_NO_AUTH",
                )
            changes |= {
                "confirmed_by": confirmed_by,
                "confirmed_on": confirmed_on or at[:10],
                "authorization": (authorization or "").strip() or None,
            }
        else:
            if superseded_by is None or superseded_by == number:
                raise _gate(
                    f"superseding #{number} needs a successor decision",
                    "DEC.SUPERSEDED_NO_SUCCESSOR",
                )
            changes["superseded_by"] = superseded_by
        changes |= {"updated_at": at, "version": row["version"] + 1}
        assignments = ", ".join(f"{key}=?" for key in changes)
        self.conn.execute(
            f"UPDATE decisions SET {assignments} WHERE number=? AND version=?",
            (*changes.values(), number, expected_version),
        )
        self._add_event(
            number,
            at,
            actor,
            ACTIONS[to],
            {"status": row["status"], "version": row["version"]},
            {k: v for k, v in changes.items() if k != "updated_at"},
        )
        return self.get(number) or {}

    def replace_all(self, rows: list[dict], events: list[dict]) -> None:
        """Wipe and reload (restore); the triggers still guard later updates."""
        self.conn.execute("DELETE FROM decision_events")
        self.conn.execute("DELETE FROM decisions")
        for row in rows:
            self._write_row(row)
        for event in events:
            self.conn.execute(
                "INSERT INTO decision_events"
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
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()
        is not None
    )
