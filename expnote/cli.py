from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from expnote.adapters.rlgarden import load_config, run_fields_from_config
from expnote.db import (
    append_event,
    init_store,
    new_id,
    now_iso,
    parse_meta,
    row_to_dict,
    transaction,
)
from expnote.markdown import sync_markdown

app = typer.Typer(help="Local-first experiment notes.")
topic_app = typer.Typer(help="Manage experiment topics.")
run_app = typer.Typer(help="Manage experiment runs.")
relation_app = typer.Typer(help="Manage run relations.")
artifact_app = typer.Typer(help="Manage run artifacts.")
sync_app = typer.Typer(help="Sync projections.")
import_app = typer.Typer(help="Import external metadata.")

app.add_typer(topic_app, name="topic")
app.add_typer(run_app, name="run")
app.add_typer(relation_app, name="relation")
app.add_typer(artifact_app, name="artifact")
app.add_typer(sync_app, name="sync")
app.add_typer(import_app, name="import")


RootOption = Annotated[
    Path,
    typer.Option("--root", "-r", help="Experiment workspace root."),
]


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(
            data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        )


def _topic_id(conn: sqlite3.Connection, title: str) -> str:
    row = conn.execute(
        "SELECT id FROM topics WHERE title = ? AND deleted_at IS NULL", (title,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"topic not found: {title}")
    return str(row["id"])


@app.command()
def init(
    root: RootOption = Path("."),
    notes_dir: Annotated[
        str, typer.Option(help="Directory for run notes.")
    ] = "notes/runs",
    moc_path: Annotated[
        str, typer.Option(help="Markdown MOC path.")
    ] = "notes/experiments.md",
    project: Annotated[str | None, typer.Option(help="Project name.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Initialize an expnote workspace."""
    root = root.resolve()
    init_store(root, notes_dir=notes_dir, moc_path=moc_path, project=project)
    append_event(
        root,
        "init",
        {"root": str(root), "notes_dir": notes_dir, "moc_path": moc_path},
    )
    _emit(
        {"root": str(root), "notes_dir": notes_dir, "moc_path": moc_path},
        json_output,
    )


@topic_app.command("add")
def topic_add(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    root: RootOption = Path("."),
    summary: Annotated[str, typer.Option(help="Short topic summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ts = now_iso()
    topic_id = new_id("topic")
    with transaction(root.resolve()) as conn:
        conn.execute(
            """
            INSERT INTO topics(id, title, summary, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (topic_id, title, summary, ts, ts),
        )
    payload = {"id": topic_id, "title": title, "summary": summary}
    append_event(root.resolve(), "topic.add", payload)
    _emit(payload, json_output)


@topic_app.command("list")
def topic_list(
    root: RootOption = Path("."),
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with transaction(root.resolve()) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"SELECT * FROM topics {where} ORDER BY created_at DESC, title DESC"
            )
        ]
    _emit(rows, json_output)


@topic_app.command("update")
def topic_update(
    title: Annotated[str, typer.Argument(help="Existing topic title.")],
    root: RootOption = Path("."),
    new_title: Annotated[str | None, typer.Option(help="New title.")] = None,
    summary: Annotated[str | None, typer.Option(help="New summary.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    with transaction(root) as conn:
        tid = _topic_id(conn, title)
        if new_title is not None:
            conn.execute(
                "UPDATE topics SET title = ?, updated_at = ? WHERE id = ?",
                (new_title, ts, tid),
            )
        if summary is not None:
            conn.execute(
                "UPDATE topics SET summary = ?, updated_at = ? WHERE id = ?",
                (summary, ts, tid),
            )
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (tid,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "topic.update", data)
    _emit(data, json_output)


@topic_app.command("delete")
def topic_delete(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    with transaction(root) as conn:
        tid = _topic_id(conn, title)
        conn.execute(
            "UPDATE topics SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, tid),
        )
    payload = {"title": title, "deleted_at": ts}
    append_event(root, "topic.delete", payload)
    _emit(payload, json_output)


@run_app.command("add")
def run_add(
    run_id: Annotated[str, typer.Option("--run-id", help="Stable run id.")],
    topic: Annotated[str, typer.Option(help="Topic title.")],
    root: RootOption = Path("."),
    purpose: Annotated[str, typer.Option(help="Short run purpose.")] = "",
    relation: Annotated[str, typer.Option(help="Relation summary.")] = "",
    result: Annotated[str, typer.Option(help="Result summary.")] = "",
    status: Annotated[str, typer.Option(help="Run status.")] = "running",
    meta: Annotated[
        list[str] | None, typer.Option("--meta", help="Metadata key=value.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    metadata = parse_meta(meta or [])
    data = _insert_run(
        root,
        run_id=run_id,
        topic=topic,
        purpose=purpose,
        relation=relation,
        result=result,
        status=status,
        metadata=metadata,
    )
    _emit(data, json_output)


def _insert_run(
    root: Path,
    *,
    run_id: str,
    topic: str,
    purpose: str,
    relation: str,
    result: str,
    status: str,
    metadata: dict[str, str],
) -> dict[str, object]:
    ts = now_iso()
    with transaction(root) as conn:
        tid = _topic_id(conn, topic)
        conn.execute(
            """
            INSERT INTO runs(
                id, topic_id, purpose, relation, result, status,
                started_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tid,
                purpose,
                relation,
                result,
                status,
                ts,
                ts,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "run.add", data)
    return data


@run_app.command("list")
def run_list(
    root: RootOption = Path("."),
    topic: Annotated[
        str | None, typer.Option(help="Filter by topic title.")
    ] = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    params: list[object] = []
    clauses = []
    if not include_deleted:
        clauses.append("runs.deleted_at IS NULL")
    if topic is not None:
        clauses.append("topics.title = ?")
        params.append(topic)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with transaction(root) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT runs.*, topics.title AS topic_title
                FROM runs JOIN topics ON runs.topic_id = topics.id
                {where}
                ORDER BY runs.started_at DESC, runs.id DESC
                """,
                params,
            )
        ]
    _emit(rows, json_output)


@run_app.command("show")
def run_show(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    with transaction(root.resolve()) as conn:
        row = conn.execute(
            """
            SELECT runs.*, topics.title AS topic_title
            FROM runs JOIN topics ON runs.topic_id = topics.id
            WHERE runs.id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise typer.BadParameter(f"run not found: {run_id}")
    _emit(row_to_dict(row), json_output)


@run_app.command("update")
def run_update(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    purpose: Annotated[str | None, typer.Option(help="New purpose.")] = None,
    relation: Annotated[str | None, typer.Option(help="New relation.")] = None,
    result: Annotated[str | None, typer.Option(help="New result.")] = None,
    status: Annotated[str | None, typer.Option(help="New status.")] = None,
    meta: Annotated[
        list[str] | None, typer.Option("--meta", help="Metadata key=value to merge.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    updates = {
        "purpose": purpose,
        "relation": relation,
        "result": result,
        "status": status,
    }
    with transaction(root) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise typer.BadParameter(f"run not found: {run_id}")
        for key, value in updates.items():
            if value is not None:
                conn.execute(
                    f"UPDATE runs SET {key} = ?, updated_at = ? WHERE id = ?",
                    (value, ts, run_id),
                )
        if meta:
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata.update(parse_meta(meta))
            conn.execute(
                "UPDATE runs SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), ts, run_id),
            )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "run.update", data)
    _emit(data, json_output)


@run_app.command("delete")
def run_delete(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    with transaction(root) as conn:
        conn.execute(
            "UPDATE runs SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, run_id),
        )
    payload = {"id": run_id, "deleted_at": ts}
    append_event(root, "run.delete", payload)
    _emit(payload, json_output)


@run_app.command("query")
def run_query(
    root: RootOption = Path("."),
    where: Annotated[
        str, typer.Option(help="Restricted SQL-like WHERE expression.")
    ] = "1 = 1",
    order_by: Annotated[
        str, typer.Option(help="Restricted SQL-like ORDER BY expression.")
    ] = "started_at DESC",
    limit: Annotated[int, typer.Option(help="Max rows.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    where_sql, where_params = _compile_run_where(where)
    order_sql = _compile_run_order_by(order_by)
    with transaction(root.resolve()) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT runs.*, topics.title AS topic_title
                FROM runs JOIN topics ON runs.topic_id = topics.id
                WHERE runs.deleted_at IS NULL AND ({where_sql})
                ORDER BY {order_sql}
                LIMIT ?
                """,
                [*where_params, limit],
            )
        ]
    _emit(rows, json_output)


@relation_app.command("add")
def relation_add(
    src_run_id: Annotated[str, typer.Argument(help="Source run id.")],
    dst_run_id: Annotated[str, typer.Argument(help="Destination run id.")],
    kind: Annotated[str, typer.Option(help="Relation kind.")],
    root: RootOption = Path("."),
    note: Annotated[str, typer.Option(help="Relation note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    rid = new_id("rel")
    with transaction(root) as conn:
        conn.execute(
            """
            INSERT INTO relations(id, src_run_id, dst_run_id, kind, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (rid, src_run_id, dst_run_id, kind, note, ts),
        )
    payload = {
        "id": rid,
        "src_run_id": src_run_id,
        "dst_run_id": dst_run_id,
        "kind": kind,
    }
    append_event(root, "relation.add", payload)
    _emit(payload, json_output)


@relation_app.command("delete")
def relation_delete(
    relation_id: Annotated[str, typer.Argument(help="Relation id.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    with transaction(root) as conn:
        conn.execute(
            "UPDATE relations SET deleted_at = ? WHERE id = ?",
            (ts, relation_id),
        )
    payload = {"id": relation_id, "deleted_at": ts}
    append_event(root, "relation.delete", payload)
    _emit(payload, json_output)


@artifact_app.command("add")
def artifact_add(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    uri: Annotated[str, typer.Argument(help="Artifact URI or path.")],
    kind: Annotated[str, typer.Option(help="Artifact kind.")] = "file",
    root: RootOption = Path("."),
    note: Annotated[str, typer.Option(help="Artifact note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    aid = new_id("art")
    ts = now_iso()
    with transaction(root) as conn:
        conn.execute(
            """
            INSERT INTO artifacts(id, run_id, kind, uri, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (aid, run_id, kind, uri, note, ts),
        )
    payload = {"id": aid, "run_id": run_id, "kind": kind, "uri": uri}
    append_event(root, "artifact.add", payload)
    _emit(payload, json_output)


@artifact_app.command("list")
def artifact_list(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    with transaction(root.resolve()) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM artifacts
                WHERE run_id = ? AND deleted_at IS NULL
                ORDER BY created_at DESC
                """,
                (run_id,),
            )
        ]
    _emit(rows, json_output)


@artifact_app.command("delete")
def artifact_delete(
    artifact_id: Annotated[str, typer.Argument(help="Artifact id.")],
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    ts = now_iso()
    with transaction(root) as conn:
        conn.execute(
            "UPDATE artifacts SET deleted_at = ? WHERE id = ?",
            (ts, artifact_id),
        )
    payload = {"id": artifact_id, "deleted_at": ts}
    append_event(root, "artifact.delete", payload)
    _emit(payload, json_output)


@sync_app.command("markdown")
def sync_markdown_command(
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    result = sync_markdown(root.resolve())
    _emit(result, json_output)


@app.command()
def validate(
    root: RootOption = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    with transaction(root) as conn:
        counts = {
            "topics": _count_active(conn, "topics"),
            "runs": _count_active(conn, "runs"),
            "artifacts": _count_active(conn, "artifacts"),
        }
    result = {"ok": True, "counts": counts}
    _emit(result, json_output)


@import_app.command("rlgarden")
def import_rlgarden(
    config_path: Annotated[
        Path, typer.Argument(help="rl-garden resolved config.json.")
    ],
    topic: Annotated[str, typer.Option(help="Topic title.")],
    root: RootOption = Path("."),
    status: Annotated[str, typer.Option(help="Initial status.")] = "running",
    purpose: Annotated[str | None, typer.Option(help="Override purpose.")] = None,
    relation: Annotated[str, typer.Option(help="Relation summary.")] = "",
    result: Annotated[str, typer.Option(help="Result summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    fields = run_fields_from_config(load_config(config_path))
    data = _insert_run(
        root,
        run_id=fields["run_id"],
        topic=topic,
        purpose=purpose if purpose is not None else fields["purpose"],
        relation=relation,
        result=result,
        status=status,
        metadata=fields["metadata"],
    )
    append_event(
        root,
        "import.rlgarden",
        {"config_path": str(config_path), "run": data},
    )
    _emit(data, json_output)


_RUN_QUERY_FIELDS = {
    "id": "runs.id",
    "purpose": "runs.purpose",
    "relation": "runs.relation",
    "result": "runs.result",
    "status": "runs.status",
    "started_at": "runs.started_at",
    "updated_at": "runs.updated_at",
    "topic": "topics.title",
    "topic_title": "topics.title",
}
_RUN_QUERY_OPERATORS = {"=", "!=", "<", "<=", ">", ">="}
_RUN_WHERE_RE = re.compile(
    r"""
    \s*
    (?P<field>[A-Za-z_][A-Za-z0-9_]*)
    \s*
    (?P<op>!=|<=|>=|=|<|>)
    \s*
    (?P<value>'(?:''|[^'])*'|[0-9]+)
    \s*
    """,
    re.VERBOSE,
)


def _compile_run_where(where: str) -> tuple[str, list[object]]:
    expression = where.strip()
    if expression == "1 = 1":
        return "1 = 1", []

    parts = re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE)
    clauses = []
    params: list[object] = []
    for part in parts:
        match = _RUN_WHERE_RE.fullmatch(part)
        if match is None:
            raise typer.BadParameter("unsupported query expression")
        field = match.group("field")
        op = match.group("op")
        if field not in _RUN_QUERY_FIELDS or op not in _RUN_QUERY_OPERATORS:
            raise typer.BadParameter("unsupported query expression")
        clauses.append(f"{_RUN_QUERY_FIELDS[field]} {op} ?")
        params.append(_parse_query_literal(match.group("value")))

    return " AND ".join(clauses), params


def _compile_run_order_by(order_by: str) -> str:
    parts = order_by.strip().split()
    if len(parts) == 1:
        field = parts[0]
        direction = "ASC"
    elif len(parts) == 2:
        field, direction = parts
        direction = direction.upper()
    else:
        raise typer.BadParameter("unsupported query expression")

    if field not in _RUN_QUERY_FIELDS or direction not in {"ASC", "DESC"}:
        raise typer.BadParameter("unsupported query expression")
    return f"{_RUN_QUERY_FIELDS[field]} {direction}"


def _parse_query_literal(value: str) -> object:
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return int(value)


def _count_active(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL"
    ).fetchone()
    return int(row[0])


if __name__ == "__main__":
    app()
