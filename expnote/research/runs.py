"""Read-only join with the linked expnote workspaces (runs, docs, stale runs)."""

from __future__ import annotations

import contextlib
import dataclasses
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from expnote.db import connect_readonly
from expnote.research.summary import first_sentence, truncate
from expnote.workspace import resolve_workspace

CHUNK = 400


def open_workspace(name: str) -> tuple[sqlite3.Connection, Path]:
    """Read-only connection to a workspace DB; raises sqlite3.Error / OSError."""
    try:
        context = resolve_workspace(workspace=name)
    except typer.BadParameter as error:
        raise OSError(str(error)) from None
    db_path = context.workspace_dir / "expnote.sqlite"
    if not db_path.is_file():
        raise OSError(f"no database at {db_path}")
    conn = connect_readonly(context.root, context.workspace_dir)
    conn.execute("SELECT 1 FROM runs LIMIT 1")  # fail early on a wrong file
    return conn, db_path


@contextlib.contextmanager
def workspace_connections(
    names: Iterable[str],
) -> Iterator[tuple[dict[str, sqlite3.Connection], dict[str, str]]]:
    """(open connections by name, unavailable workspaces with the reason)."""
    opened: dict[str, sqlite3.Connection] = {}
    problems: dict[str, str] = {}
    try:
        for name in names:
            try:
                opened[name] = open_workspace(name)[0]
            except (sqlite3.Error, OSError) as error:
                problems[name] = str(error)
        yield opened, problems
    finally:
        for conn in opened.values():
            conn.close()


def _chunks(values: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _placeholders(values: list[str]) -> str:
    return ",".join("?" * len(values))


def ids_in_workspaces(
    names: list[str], candidates: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """{id: 'run' | 'doc'} for candidates the workspaces know, plus problems."""
    found: dict[str, str] = {}
    ordered = sorted(candidates)
    with workspace_connections(names) as (connections, problems):
        for conn in connections.values():
            for chunk in _chunks(ordered):
                marks = _placeholders(chunk)
                for (row_id,) in conn.execute(
                    f"SELECT id FROM runs WHERE deleted_at IS NULL AND id IN ({marks})",
                    chunk,
                ):
                    found[row_id] = "run"
                for (row_id,) in conn.execute(
                    f"SELECT id FROM docs WHERE deleted_at IS NULL AND id IN ({marks})",
                    chunk,
                ):
                    found.setdefault(row_id, "doc")
    return found, problems


def one_line(text: str, limit: int = 160) -> str:
    """First non-empty line, truncated (run results are single headlines)."""
    for line in (text or "").splitlines():
        if line.strip():
            return truncate(line, limit)
    return ""


@dataclasses.dataclass
class RunRow:
    id: str
    workspace: str
    topic: str
    status: str
    result: str
    updated_at: str


@dataclasses.dataclass
class DocRow:
    id: str
    workspace: str
    title: str
    one_line: str
    linked_runs: list[str]


@dataclasses.dataclass
class RunsJoin:
    runs: list[RunRow]
    docs: list[DocRow]
    unavailable: dict[str, str]
    missing: list[str]  # cited ids no linked workspace knows


def join_runs(
    names: list[str], run_ids: set[str], doc_ids: set[str] | None = None
) -> RunsJoin:
    """Runs by id, plus docs linked to those runs (or named in `doc_ids`)."""
    runs: dict[str, RunRow] = {}
    docs: dict[tuple[str, str], DocRow] = {}
    ordered = sorted(run_ids)
    wanted_docs = sorted(doc_ids or ())
    with workspace_connections(names) as (connections, problems):
        for workspace, conn in connections.items():
            for chunk in _chunks(ordered):
                marks = _placeholders(chunk)
                for row in conn.execute(
                    "SELECT r.id, r.status, r.result, r.updated_at, "
                    "COALESCE(t.title, '') AS topic FROM runs r "
                    "LEFT JOIN topics t ON t.id = r.topic_id "
                    f"WHERE r.deleted_at IS NULL AND r.id IN ({marks})",
                    chunk,
                ):
                    runs.setdefault(
                        row["id"],
                        RunRow(
                            row["id"],
                            workspace,
                            row["topic"],
                            row["status"],
                            one_line(row["result"]),
                            row["updated_at"],
                        ),
                    )
                for row in conn.execute(
                    "SELECT d.id, d.title, d.body, dr.run_id FROM docs d "
                    "JOIN doc_runs dr ON dr.doc_id = d.id "
                    "WHERE d.deleted_at IS NULL AND dr.deleted_at IS NULL "
                    f"AND dr.run_id IN ({marks})",
                    chunk,
                ):
                    doc = docs.setdefault(
                        (workspace, row["id"]),
                        DocRow(
                            row["id"],
                            workspace,
                            row["title"],
                            first_sentence(row["body"]),
                            [],
                        ),
                    )
                    if row["run_id"] not in doc.linked_runs:
                        doc.linked_runs.append(row["run_id"])
            for chunk in _chunks(wanted_docs):
                marks = _placeholders(chunk)
                for row in conn.execute(
                    "SELECT id, title, body FROM docs WHERE deleted_at IS NULL "
                    f"AND id IN ({marks})",
                    chunk,
                ):
                    docs.setdefault(
                        (workspace, row["id"]),
                        DocRow(
                            row["id"],
                            workspace,
                            row["title"],
                            first_sentence(row["body"]),
                            [],
                        ),
                    )
    return RunsJoin(
        runs=sorted(runs.values(), key=lambda r: r.id),
        docs=sorted(docs.values(), key=lambda d: (d.workspace, d.id)),
        unavailable=problems,
        missing=sorted(set(ordered) - set(runs)) if len(problems) < len(names) else [],
    )


def recent_runs(names: list[str], limit: int = 10) -> tuple[list[dict], dict[str, str]]:
    """The most recently updated runs across the workspaces, newest first."""
    found: list[dict] = []
    with workspace_connections(names) as (connections, problems):
        for workspace, conn in connections.items():
            for row in conn.execute(
                "SELECT r.id, r.status, r.result, r.updated_at, "
                "COALESCE(t.title, '') AS topic FROM runs r "
                "LEFT JOIN topics t ON t.id = r.topic_id "
                "WHERE r.deleted_at IS NULL ORDER BY r.updated_at DESC, r.id LIMIT ?",
                (limit,),
            ):
                found.append(
                    {
                        "id": row["id"],
                        "workspace": workspace,
                        "topic": row["topic"],
                        "status": row["status"],
                        "result": one_line(row["result"]),
                        "updatedAt": row["updated_at"],
                    }
                )
    found.sort(key=lambda r: (r["updatedAt"], r["id"]), reverse=True)
    return found[:limit], problems


def stale_running_runs(
    names: list[str], now: datetime | None = None, days: int = 2
) -> tuple[list[dict], dict[str, str]]:
    """Running runs with an empty result older than `days` (same rule as `validate`)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    stale: list[dict] = []
    with workspace_connections(names) as (connections, problems):
        for workspace, conn in connections.items():
            for row in conn.execute(
                "SELECT r.id, r.started_at, t.title AS topic FROM runs r "
                "JOIN topics t ON t.id = r.topic_id "
                "WHERE r.deleted_at IS NULL AND t.deleted_at IS NULL "
                "AND r.status = 'running' AND TRIM(r.result) = '' "
                "ORDER BY r.started_at, r.id"
            ):
                try:
                    started = datetime.fromisoformat(row["started_at"])
                except ValueError:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                if started <= cutoff:
                    stale.append(
                        {
                            "runId": row["id"],
                            "workspace": workspace,
                            "topic": row["topic"],
                            "ageDays": (now - started).days,
                        }
                    )
    return stale, problems
