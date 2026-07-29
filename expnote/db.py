from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ExpnotePaths:
    root: Path
    state_dir: Path
    db_path: Path
    events_path: Path
    config_path: Path


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def paths_for(root: Path, state_dir: Path | None = None) -> ExpnotePaths:
    state_dir = state_dir or root / ".expnote"
    return ExpnotePaths(
        root=root,
        state_dir=state_dir,
        db_path=state_dir / "expnote.sqlite",
        events_path=state_dir / "events.jsonl",
        config_path=state_dir / "config.toml",
    )


def connect(root: Path, state_dir: Path | None = None) -> sqlite3.Connection:
    paths = paths_for(root, state_dir)
    conn = sqlite3.connect(paths.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


@contextmanager
def transaction(root: Path, state_dir: Path | None = None):
    conn = connect(root, state_dir)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_store(
    root: Path,
    *,
    state_dir: Path | None = None,
    notes_dir: str,
    index_path: str,
    moc_path: str | None = None,
    project: str | None = None,
) -> None:
    paths = paths_for(root, state_dir)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    (root / notes_dir).mkdir(parents=True, exist_ok=True)
    if not paths.events_path.exists():
        paths.events_path.write_text("", encoding="utf-8")
    if not paths.config_path.exists():
        project_name = project or root.name
        paths.config_path.write_text(
            "\n".join(
                [
                    f'project = "{_toml_string(project_name)}"',
                    f'root = "{_toml_string(str(root))}"',
                    f'state_dir = "{_toml_string(str(paths.state_dir))}"',
                    f'notes_dir = "{_toml_string(notes_dir)}"',
                    (
                        f'moc_path = "{_toml_string(moc_path)}"'
                        if moc_path is not None
                        else f'index_path = "{_toml_string(index_path)}"'
                    ),
                    "",
                ]
            ),
            encoding="utf-8",
        )
    with transaction(root, state_dir=paths.state_dir) as conn:
        migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            topic_id TEXT NOT NULL REFERENCES topics(id),
            purpose TEXT NOT NULL DEFAULT '',
            relation TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            analysis TEXT NOT NULL DEFAULT '',
            analysis_rendered_hash TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS moc_entries (
            id TEXT PRIMARY KEY,
            moc_path TEXT NOT NULL,
            section TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id),
            position INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(moc_path, section, run_id)
        );

        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            src_run_id TEXT NOT NULL REFERENCES runs(id),
            dst_run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            kind TEXT NOT NULL,
            uri TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            deleted_at TEXT
        );
        """
    )
    _add_column_if_missing(conn, "runs", "analysis", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(
        conn,
        "runs",
        "analysis_rendered_hash",
        "TEXT NOT NULL DEFAULT ''",
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def read_config(root: Path, state_dir: Path | None = None) -> dict[str, str]:
    path = paths_for(root, state_dir).config_path
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Run `expnote init` first."
        )
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        config[key.strip()] = value.strip().strip('"')
    return config


def append_event(
    root: Path,
    event_type: str,
    payload: dict[str, Any],
    state_dir: Path | None = None,
) -> None:
    paths = paths_for(root, state_dir)
    record = {
        "id": uuid.uuid4().hex,
        "type": event_type,
        "ts": now_iso(),
        "payload": payload,
    }
    with paths.events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def row_to_dict(
    row: sqlite3.Row,
    *,
    include_internal: bool = False,
) -> dict[str, Any]:
    data = dict(row)
    if "metadata_json" in data:
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    if not include_internal:
        data.pop("analysis_rendered_hash", None)
    return data


def parse_meta(items: Iterable[str]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"metadata must use key=value syntax: {item!r}")
        meta[key] = value
    return meta


def parse_meta_json(items: Iterable[str]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"metadata must use key=json syntax: {item!r}")
        meta[key] = json.loads(value)
    return meta


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
