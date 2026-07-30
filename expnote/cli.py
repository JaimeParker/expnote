from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any

import typer

from expnote.adapters.rlgarden import load_config, run_fields_from_config
from expnote.db import (
    append_event,
    init_store,
    new_id,
    now_iso,
    parse_meta,
    parse_meta_json,
    row_to_dict,
    transaction,
)
from expnote.markdown import (
    diff_moc_section,
    ensure_curated_moc_target,
    projection_conflicts,
    sync_markdown,
    sync_moc_section,
)

app = typer.Typer(help="Local-first experiment notes.")
topic_app = typer.Typer(help="Manage experiment topics.")
run_app = typer.Typer(help="Manage experiment runs.")
relation_app = typer.Typer(help="Manage run relations.")
artifact_app = typer.Typer(help="Manage run artifacts.")
moc_app = typer.Typer(help="Manage MOC section tables.")
sync_app = typer.Typer(help="Sync projections.")
import_app = typer.Typer(help="Import external metadata.")

app.add_typer(topic_app, name="topic")
app.add_typer(run_app, name="run")
app.add_typer(relation_app, name="relation")
app.add_typer(artifact_app, name="artifact")
app.add_typer(moc_app, name="moc")
app.add_typer(sync_app, name="sync")
app.add_typer(import_app, name="import")


RootOption = Annotated[
    Path,
    typer.Option("--root", "-r", help="Markdown workspace root."),
]
StateDirOption = Annotated[
    Path | None,
    typer.Option(
        "--state-dir",
        help="Directory for expnote.sqlite, events.jsonl, and config.toml.",
    ),
]


_AGENT_GUIDE = {
    "topic": "agent",
    "principles": [
        "SQLite is the source of truth",
        "Markdown is a projection",
        "Use --json for automation",
        "Pass the same --root and --state-dir on follow-up commands",
        "Edit structured fields through the CLI",
    ],
    "required_flags": ["--root", "--state-dir"],
    "workflows": {
        "create_run": [
            "init",
            "topic add",
            "run add",
            "moc add",
            "sync all",
        ],
        "query_run": [
            "run show <run_id> --json",
            "run show <run_id> --field purpose",
            "run query --where \"metadata.seed = 1\" --json",
            "run query --where \"status = 'running'\" --json",
        ],
        "analysis_import": [
            "sync markdown",
            "sync markdown --pull-analysis",
        ],
        "handoff": [
            "validate --json",
            "moc diff --moc-path <path> --section <heading> --json",
        ],
    },
    "commands": {
        "init": "Create the workspace and configure Markdown projection paths",
        "topic.add": "Create a training or experiment topic",
        "run.add": "Create a SQL-backed run record",
        "run.show": "Read SQL-backed Purpose, Relation, Result, Metadata, Analysis",
        "run.update": "Update structured run fields and metadata",
        "run.query": "Query runs with restricted SQL-like filters and metadata keys",
        "moc.add": "Add a run to a managed MOC section table",
        "moc.add_topic": "Add all active topic runs to a managed MOC section",
        "moc.sections": "List registered sections for a curated MOC",
        "moc.diff": "Compare a managed MOC section table with SQLite",
        "sync.markdown": "Render SQLite records into Markdown",
        "sync.all": "Render run notes, auto index, and registered curated MOCs",
        "sync.markdown.pull_analysis": "Import Obsidian Analysis into SQLite",
        "validate": "Check active record counts before handoff",
    },
    "conflict_policy": {
        "structured_fields": (
            "Edit Purpose, Relation, Result, Metadata through CLI only"
        ),
        "analysis": "Obsidian edits require sync markdown --pull-analysis",
        "moc_tables": "Managed MOC tables should be repaired with moc sync",
        "projection_paths": (
            "auto index defaults to state-dir/index.md; curated MOCs use moc --moc-path"
        ),
    },
    "examples": {
        "init": (
            "expnote init --root <vault> --state-dir <state> "
            "--notes-dir <runs-dir>"
        ),
        "create_run": (
            "expnote run add --root <vault> --state-dir <state> "
            "--topic <topic> --run-id <id> --purpose <text> "
            "--meta-json seed=1 --json"
        ),
        "create_run_by_topic_id": (
            "expnote run create --root <vault> --state-dir <state> "
            "--topic-id <topic_id> --id <id> --purpose <text> --json"
        ),
        "metadata_json": (
            "expnote run update <id> --root <vault> --state-dir <state> "
            "--metadata-json '{\"seed\":1,\"algo\":\"calql\"}'"
        ),
        "unset_metadata": (
            "expnote run update <id> --root <vault> --state-dir <state> "
            "--unset-meta seed"
        ),
        "append_analysis": (
            "expnote run update <id> --root <vault> --state-dir <state> "
            "--append-analysis <text>"
        ),
        "show_run": (
            "expnote run show <id> --root <vault> --state-dir <state> --json"
        ),
        "show_field": (
            "expnote run show <id> --root <vault> --state-dir <state> "
            "--field status"
        ),
        "moc_diff": (
            "expnote moc diff --root <vault> --state-dir <state> "
            "--moc-path <moc.md> --section <heading> --json"
        ),
        "moc_sections": (
            "expnote moc sections --root <vault> --state-dir <state> "
            "--moc-path <moc.md> --json"
        ),
        "moc_add_topic": (
            "expnote moc add-topic --root <vault> --state-dir <state> "
            "--topic <topic> --moc-path <moc.md> --section <heading> --json"
        ),
        "sync_all": "expnote sync all --root <vault> --state-dir <state> --json",
    },
    "common_pitfalls": {
        "run_create": "run create is supported as an alias for run add",
        "run_id": "use --run-id or --id, not a positional id flag",
        "topic_id": "use --topic-id when you have a topic id; use --topic for title",
        "metadata_json": (
            "use --metadata-json '{...}' for a whole object; "
            "--meta-json is key=json"
        ),
        "curated_mocs": (
            "sync markdown does not update curated MOC tables; use sync all "
            "or moc sync/add/add-topic"
        ),
    },
}


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(
            data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        )


def _render_agent_guide() -> str:
    return "\n".join(
        [
            "# expnote agent guide",
            "",
            "Core rules:",
            "- SQLite is the source of truth",
            "- Markdown is a projection",
            "- Use --json for automation",
            "- Reuse the same --root and --state-dir on follow-up commands",
            "",
            "Minimal workflow:",
            "init -> topic add -> run add -> moc add -> sync all",
            "",
            "Read records from SQLite:",
            "expnote run show <run_id> --json",
            "expnote run show <run_id> --field purpose",
            "expnote run query --where \"metadata.seed = 1\" --json",
            "expnote run query --where \"status = 'running'\" --json",
            "expnote run update <run_id> --append-analysis <text>",
            "expnote run update <run_id> --metadata-json '{\"seed\":1}'",
            "",
            "MOC workflow:",
            "expnote moc sections --moc-path <path> --json",
            "expnote moc add-topic --topic <topic> "
            "--moc-path <path> --section <heading> --json",
            "expnote sync all --json",
            "",
            "Obsidian conflict policy:",
            "- Edit Purpose, Relation, Result, Metadata through CLI only",
            "- Import Obsidian Analysis with sync markdown --pull-analysis",
            "- Check managed MOC tables with expnote moc diff --json",
            "- Auto index defaults to state-dir/index.md, outside Obsidian",
            "",
            "Handoff checks:",
            "expnote validate --json",
            "expnote moc diff --moc-path <path> --section <heading> --json",
        ]
    )


def _topic_id(conn: sqlite3.Connection, title: str) -> str:
    row = conn.execute(
        "SELECT id FROM topics WHERE title = ? AND deleted_at IS NULL", (title,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"topic not found: {title}")
    return str(row["id"])


def _topic_id_exists(conn: sqlite3.Connection, topic_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM topics WHERE id = ? AND deleted_at IS NULL", (topic_id,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"topic not found: {topic_id}")
    return str(row["id"])


def _resolve_topic_id(
    conn: sqlite3.Connection,
    topic: str | None,
    topic_id: str | None,
) -> str:
    if topic is not None and topic_id is not None:
        raise typer.BadParameter("--topic and --topic-id cannot be used together")
    if topic is None and topic_id is None:
        raise typer.BadParameter("--topic or --topic-id is required")
    if topic_id is not None:
        return _topic_id_exists(conn, topic_id)
    assert topic is not None
    return _topic_id(conn, topic)


@app.command("guide")
def guide(
    topic: Annotated[str, typer.Argument(help="Guide topic.")] = "agent",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show a built-in guide."""
    if topic != "agent":
        raise typer.BadParameter("supported guide topic: agent")
    _emit(_AGENT_GUIDE if json_output else _render_agent_guide(), json_output)


@app.command()
def init(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    notes_dir: Annotated[
        str, typer.Option(help="Directory for run notes.")
    ] = "notes/runs",
    index_path: Annotated[
        str,
        typer.Option(
            "--index-path",
            help="Generated auto-index path relative to state-dir.",
        ),
    ] = "index.md",
    moc_path: Annotated[
        str | None,
        typer.Option(
            "--moc-path",
            help="Legacy generated auto-index path relative to root.",
        ),
    ] = None,
    project: Annotated[str | None, typer.Option(help="Project name.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Initialize an expnote workspace."""
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    init_store(
        root,
        state_dir=state_dir,
        notes_dir=notes_dir,
        index_path=index_path,
        moc_path=moc_path,
        project=project,
    )
    output_index_path = moc_path or index_path
    append_event(
        root,
        "init",
        {
            "root": str(root),
            "state_dir": str(state_dir or root / ".expnote"),
            "notes_dir": notes_dir,
            "index_path": output_index_path,
            "index_scope": "root" if moc_path is not None else "state_dir",
        },
        state_dir=state_dir,
    )
    _emit(
        {
            "root": str(root),
            "state_dir": str(state_dir or root / ".expnote"),
            "notes_dir": notes_dir,
            "index_path": output_index_path,
            "index_scope": "root" if moc_path is not None else "state_dir",
        },
        json_output,
    )


@topic_app.command("add")
def topic_add(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    summary: Annotated[str, typer.Option(help="Short topic summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    topic_id = new_id("topic")
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            """
            INSERT INTO topics(id, title, summary, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (topic_id, title, summary, ts, ts),
        )
    payload = {"id": topic_id, "title": title, "summary": summary}
    append_event(root, "topic.add", payload, state_dir=state_dir)
    _emit(payload, json_output)


@topic_app.command("list")
def topic_list(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with transaction(root, state_dir=state_dir) as conn:
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
    state_dir: StateDirOption = None,
    new_title: Annotated[str | None, typer.Option(help="New title.")] = None,
    summary: Annotated[str | None, typer.Option(help="New summary.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
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
    append_event(root, "topic.update", data, state_dir=state_dir)
    _emit(data, json_output)


@topic_app.command("delete")
def topic_delete(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        tid = _topic_id(conn, title)
        conn.execute(
            "UPDATE topics SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, tid),
        )
    payload = {"title": title, "deleted_at": ts}
    append_event(root, "topic.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@run_app.command("add")
@run_app.command("create")
def run_add(
    run_id: Annotated[
        str, typer.Option("--run-id", "--id", help="Stable run id.")
    ],
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            help="Topic title. Mutually exclusive with --topic-id.",
        ),
    ] = None,
    topic_id: Annotated[
        str | None,
        typer.Option("--topic-id", help="Topic id. Mutually exclusive with --topic."),
    ] = None,
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    purpose: Annotated[str, typer.Option(help="Short run purpose.")] = "",
    relation: Annotated[str, typer.Option(help="Relation summary.")] = "",
    result: Annotated[str, typer.Option(help="Result summary.")] = "",
    analysis: Annotated[str, typer.Option(help="Run analysis.")] = "",
    status: Annotated[str, typer.Option(help="Run status.")] = "running",
    meta: Annotated[
        list[str] | None, typer.Option("--meta", help="Metadata key=value.")
    ] = None,
    meta_json: Annotated[
        list[str] | None,
        typer.Option("--meta-json", help="Typed metadata key=json."),
    ] = None,
    metadata_json: Annotated[
        str | None,
        typer.Option("--metadata-json", help="Metadata JSON object to merge."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    try:
        metadata = _merge_metadata_options(meta, meta_json, metadata_json)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    data = _insert_run(
        root,
        state_dir=state_dir,
        run_id=run_id,
        topic=topic,
        topic_id=topic_id,
        purpose=purpose,
        relation=relation,
        result=result,
        analysis=analysis,
        status=status,
        metadata=metadata,
    )
    _emit(data, json_output)


def _insert_run(
    root: Path,
    *,
    state_dir: Path | None = None,
    run_id: str,
    topic: str | None = None,
    topic_id: str | None = None,
    purpose: str,
    relation: str,
    result: str,
    analysis: str,
    status: str,
    metadata: dict[str, object],
) -> dict[str, object]:
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        tid = _resolve_topic_id(conn, topic, topic_id)
        conn.execute(
            """
            INSERT INTO runs(
                id, topic_id, purpose, relation, result, status,
                started_at, updated_at, analysis, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                analysis,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "run.add", data, state_dir=state_dir)
    return data


@run_app.command("list")
def run_list(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    topic: Annotated[
        str | None, typer.Option(help="Filter by topic title.")
    ] = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    params: list[object] = []
    clauses = []
    if not include_deleted:
        clauses.append("runs.deleted_at IS NULL")
    if topic is not None:
        clauses.append("topics.title = ?")
        params.append(topic)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with transaction(root, state_dir=state_dir) as conn:
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
    state_dir: StateDirOption = None,
    field: Annotated[
        str | None, typer.Option("--field", help="Return one public run field.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    with transaction(root, state_dir=state_dir) as conn:
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
    data = row_to_dict(row)
    if field is not None:
        _emit(_run_show_field(data, field), json_output)
    else:
        _emit(data, json_output)


@run_app.command("update")
def run_update(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    purpose: Annotated[str | None, typer.Option(help="New purpose.")] = None,
    relation: Annotated[str | None, typer.Option(help="New relation.")] = None,
    result: Annotated[str | None, typer.Option(help="New result.")] = None,
    analysis: Annotated[str | None, typer.Option(help="New analysis.")] = None,
    append_analysis: Annotated[
        str | None,
        typer.Option(
            "--append-analysis",
            help="Append to Analysis, separated from existing text by one blank line.",
        ),
    ] = None,
    status: Annotated[str | None, typer.Option(help="New status.")] = None,
    meta: Annotated[
        list[str] | None, typer.Option("--meta", help="Metadata key=value to merge.")
    ] = None,
    meta_json: Annotated[
        list[str] | None,
        typer.Option("--meta-json", help="Typed metadata key=json to merge."),
    ] = None,
    metadata_json: Annotated[
        str | None,
        typer.Option("--metadata-json", help="Metadata JSON object to merge."),
    ] = None,
    unset_meta: Annotated[
        list[str] | None,
        typer.Option("--unset-meta", help="Metadata key to delete."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if analysis is not None and append_analysis is not None:
        raise typer.BadParameter(
            "--analysis and --append-analysis cannot be used together"
        )
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise typer.BadParameter(f"run not found: {run_id}")
        if append_analysis is not None:
            current_analysis = row["analysis"] or ""
            analysis = (
                append_analysis
                if current_analysis == ""
                else f"{current_analysis}\n\n{append_analysis}"
            )
        updates = {
            "purpose": purpose,
            "relation": relation,
            "result": result,
            "analysis": analysis,
            "status": status,
        }
        for key, value in updates.items():
            if value is not None:
                conn.execute(
                    f"UPDATE runs SET {key} = ?, updated_at = ? WHERE id = ?",
                    (value, ts, run_id),
                )
        if meta or meta_json or metadata_json or unset_meta:
            metadata = json.loads(row["metadata_json"] or "{}")
            try:
                updates = _merge_metadata_options(meta, meta_json, metadata_json)
                _check_metadata_unset_conflicts(updates, unset_meta or [])
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            for key in unset_meta or []:
                metadata.pop(key, None)
            metadata.update(updates)
            conn.execute(
                "UPDATE runs SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), ts, run_id),
            )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "run.update", data, state_dir=state_dir)
    _emit(data, json_output)


@run_app.command("delete")
def run_delete(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            "UPDATE runs SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, run_id),
        )
    payload = {"id": run_id, "deleted_at": ts}
    append_event(root, "run.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@run_app.command("query")
def run_query(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    where: Annotated[
        str, typer.Option(help="Restricted SQL-like WHERE expression.")
    ] = "1 = 1",
    order_by: Annotated[
        str, typer.Option(help="Restricted SQL-like ORDER BY expression.")
    ] = "started_at DESC",
    limit: Annotated[int, typer.Option(help="Max rows.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    where_sql, where_params = _compile_run_where(where)
    order_sql = _compile_run_order_by(order_by)
    with transaction(root, state_dir=state_dir) as conn:
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
    state_dir: StateDirOption = None,
    note: Annotated[str, typer.Option(help="Relation note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    rid = new_id("rel")
    with transaction(root, state_dir=state_dir) as conn:
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
    append_event(root, "relation.add", payload, state_dir=state_dir)
    _emit(payload, json_output)


@relation_app.command("delete")
def relation_delete(
    relation_id: Annotated[str, typer.Argument(help="Relation id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            "UPDATE relations SET deleted_at = ? WHERE id = ?",
            (ts, relation_id),
        )
    payload = {"id": relation_id, "deleted_at": ts}
    append_event(root, "relation.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@artifact_app.command("add")
def artifact_add(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    uri: Annotated[str, typer.Argument(help="Artifact URI or path.")],
    kind: Annotated[str, typer.Option(help="Artifact kind.")] = "file",
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    note: Annotated[str, typer.Option(help="Artifact note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    aid = new_id("art")
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            """
            INSERT INTO artifacts(id, run_id, kind, uri, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (aid, run_id, kind, uri, note, ts),
        )
    payload = {"id": aid, "run_id": run_id, "kind": kind, "uri": uri}
    append_event(root, "artifact.add", payload, state_dir=state_dir)
    _emit(payload, json_output)


@artifact_app.command("list")
def artifact_list(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    with transaction(root, state_dir=state_dir) as conn:
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
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            "UPDATE artifacts SET deleted_at = ? WHERE id = ?",
            (ts, artifact_id),
        )
    payload = {"id": artifact_id, "deleted_at": ts}
    append_event(root, "artifact.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@moc_app.command("add")
def moc_add(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _require_run(conn, run_id)
        position = _next_moc_position(conn, moc_path, section)
        entry_id = new_id("moc")
        conn.execute(
            """
            INSERT INTO moc_entries(
                id, moc_path, section, run_id, position,
                created_at, updated_at, deleted_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(moc_path, section, run_id) DO UPDATE SET
                position = excluded.position,
                updated_at = excluded.updated_at,
                deleted_at = NULL
            """,
            (entry_id, moc_path, section, run_id, position, ts, ts),
        )
        _normalize_moc_positions(conn, moc_path, section, ts)
    sync_result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    payload = {
        "moc_path": moc_path,
        "section": section,
        "run_id": run_id,
        "position": position,
    }
    append_event(root, "moc.add", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@moc_app.command("remove")
def moc_remove(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            """
            UPDATE moc_entries
            SET deleted_at = ?, updated_at = ?
            WHERE moc_path = ? AND section = ? AND run_id = ?
            """,
            (ts, ts, moc_path, section, run_id),
        )
        _normalize_moc_positions(conn, moc_path, section, ts)
    sync_result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    payload = {"moc_path": moc_path, "section": section, "run_id": run_id}
    append_event(root, "moc.remove", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@moc_app.command("update")
def moc_update(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    position: Annotated[int, typer.Option(help="New table position.")] = 1,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _move_moc_entry(conn, moc_path, section, run_id, position, ts)
    sync_result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    payload = {
        "moc_path": moc_path,
        "section": section,
        "run_id": run_id,
        "position": position,
    }
    append_event(root, "moc.update", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@moc_app.command("list")
def moc_list(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[
        str | None, typer.Option(help="MOC level-two heading.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path:
        raise typer.BadParameter("--moc-path is required")
    params: list[object] = [moc_path]
    section_clause = ""
    if section is not None:
        section_clause = "AND moc_entries.section = ?"
        params.append(section)
    with transaction(root, state_dir=state_dir) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    moc_entries.*,
                    runs.purpose,
                    runs.relation,
                    runs.result,
                    runs.status
                FROM moc_entries
                JOIN runs ON runs.id = moc_entries.run_id
                WHERE
                    moc_entries.moc_path = ?
                    {section_clause}
                    AND moc_entries.deleted_at IS NULL
                    AND runs.deleted_at IS NULL
                ORDER BY
                    moc_entries.section ASC,
                    moc_entries.position ASC,
                    moc_entries.created_at ASC
                """,
                params,
            )
        ]
    _emit(rows, json_output)


@moc_app.command("sections")
def moc_sections(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path:
        raise typer.BadParameter("--moc-path is required")
    _emit(_registered_moc_sections(root, state_dir, moc_path=moc_path), json_output)


@moc_app.command("add-topic")
def moc_add_topic(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            help="Topic title. Mutually exclusive with --topic-id.",
        ),
    ] = None,
    topic_id: Annotated[
        str | None,
        typer.Option("--topic-id", help="Topic id. Mutually exclusive with --topic."),
    ] = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    ts = now_iso()
    added: list[str] = []
    skipped: list[str] = []
    with transaction(root, state_dir=state_dir) as conn:
        resolved_topic_id = _resolve_topic_id(conn, topic, topic_id)
        active_entries = {
            str(row["run_id"]) for row in _active_moc_entries(conn, moc_path, section)
        }
        position = _next_moc_position(conn, moc_path, section)
        rows = conn.execute(
            """
            SELECT id FROM runs
            WHERE topic_id = ? AND deleted_at IS NULL
            ORDER BY started_at ASC, id ASC
            """,
            (resolved_topic_id,),
        ).fetchall()
        for row in rows:
            run_id = str(row["id"])
            if run_id in active_entries:
                skipped.append(run_id)
                continue
            conn.execute(
                """
                INSERT INTO moc_entries(
                    id, moc_path, section, run_id, position,
                    created_at, updated_at, deleted_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(moc_path, section, run_id) DO UPDATE SET
                    position = excluded.position,
                    updated_at = excluded.updated_at,
                    deleted_at = NULL
                """,
                (new_id("moc"), moc_path, section, run_id, position, ts, ts),
            )
            added.append(run_id)
            position += 1
        _normalize_moc_positions(conn, moc_path, section, ts)
    sync_result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    payload = {
        "moc_path": moc_path,
        "section": section,
        "topic": topic,
        "topic_id": topic_id,
        "added": added,
        "skipped": skipped,
    }
    append_event(root, "moc.add_topic", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@moc_app.command("diff")
def moc_diff(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    result = diff_moc_section(root, state_dir, moc_path, section)
    if json_output:
        _emit(result, True)
    else:
        _emit(_format_moc_diff(result), False)


@moc_app.command("sync")
def moc_sync(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    allow_empty: Annotated[
        bool,
        typer.Option(
            "--allow-empty",
            help="Allow creating or syncing a section with no registered rows.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    if not allow_empty and not _moc_section_seen(root, state_dir, moc_path, section):
        raise typer.BadParameter(
            "no registered MOC entries for section. "
            "Use `expnote moc sections`, `expnote moc add`, or "
            "`expnote moc add-topic`; pass --allow-empty to create an empty section."
        )
    result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    _emit(result, json_output)


@sync_app.command("markdown")
def sync_markdown_command(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    pull_analysis: Annotated[
        bool, typer.Option("--pull-analysis", help="Import run note Analysis first.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite changed run note Analysis.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    try:
        result = sync_markdown(
            root,
            state_dir=state_dir,
            pull_analysis=pull_analysis,
            force=force,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    registered = _registered_moc_sections(root, state_dir)
    result["curated_moc_sections"] = {
        "registered": len(registered),
        "synced": 0,
        "hint": (
            "sync markdown does not update curated MOCs; use expnote sync all"
            if registered
            else ""
        ),
    }
    _emit(result, json_output)


@sync_app.command("all")
def sync_all_command(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    pull_analysis: Annotated[
        bool, typer.Option("--pull-analysis", help="Import run note Analysis first.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite changed run note Analysis.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    try:
        result = sync_markdown(
            root,
            state_dir=state_dir,
            pull_analysis=pull_analysis,
            force=force,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    sections = _registered_moc_sections(root, state_dir)
    synced = [
        _sync_moc_section_or_error(
            root, state_dir, str(item["moc_path"]), str(item["section"])
        )
        for item in sections
    ]
    result["curated_moc_sections"] = {
        "registered": len(sections),
        "synced": len(synced),
        "sections": synced,
    }
    _emit(result, json_output)


def _sync_moc_section_or_error(
    root: Path,
    state_dir: Path | None,
    moc_path: str,
    section: str,
) -> dict[str, Any]:
    try:
        return sync_moc_section(root, state_dir, moc_path, section)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _preflight_moc_section_target(root: Path, moc_path: str) -> None:
    try:
        ensure_curated_moc_target(root / moc_path)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def validate(
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    with transaction(root, state_dir=state_dir) as conn:
        counts = {
            "topics": _count_active(conn, "topics"),
            "runs": _count_active(conn, "runs"),
            "artifacts": _count_active(conn, "artifacts"),
        }
    conflicts = projection_conflicts(root, state_dir=state_dir)
    result = {"ok": not conflicts, "counts": counts, "projection_conflicts": conflicts}
    _emit(result, json_output)


@import_app.command("rlgarden")
def import_rlgarden(
    config_path: Annotated[
        Path, typer.Argument(help="rl-garden resolved config.json.")
    ],
    topic: Annotated[str, typer.Option(help="Topic title.")],
    root: RootOption = Path("."),
    state_dir: StateDirOption = None,
    status: Annotated[str, typer.Option(help="Initial status.")] = "running",
    purpose: Annotated[str | None, typer.Option(help="Override purpose.")] = None,
    relation: Annotated[str, typer.Option(help="Relation summary.")] = "",
    result: Annotated[str, typer.Option(help="Result summary.")] = "",
    analysis: Annotated[str, typer.Option(help="Initial analysis.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    fields = run_fields_from_config(load_config(config_path))
    data = _insert_run(
        root,
        state_dir=state_dir,
        run_id=fields["run_id"],
        topic=topic,
        purpose=purpose if purpose is not None else fields["purpose"],
        relation=relation,
        result=result,
        analysis=analysis,
        status=status,
        metadata=fields["metadata"],
    )
    append_event(
        root,
        "import.rlgarden",
        {"config_path": str(config_path), "run": data},
        state_dir=state_dir,
    )
    _emit(data, json_output)


_RUN_QUERY_FIELDS = {
    "id": "runs.id",
    "purpose": "runs.purpose",
    "relation": "runs.relation",
    "result": "runs.result",
    "analysis": "runs.analysis",
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
    (?P<field>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)?)
    \s*
    (?P<op>!=|<=|>=|=|<|>)
    \s*
    (?P<value>'(?:''|[^'])*'|-?[0-9]+(?:\.[0-9]+)?|true|false|null)
    \s*
    """,
    re.VERBOSE,
)
_RUN_SHOW_FIELDS = {
    "id",
    "topic_id",
    "topic_title",
    "topic",
    "purpose",
    "relation",
    "result",
    "analysis",
    "status",
    "metadata",
    "started_at",
    "updated_at",
    "deleted_at",
}


def _run_show_field(data: dict[str, object], field: str) -> object:
    if field == "topic":
        return data["topic_title"]
    if field not in _RUN_SHOW_FIELDS:
        fields = ", ".join(sorted(_RUN_SHOW_FIELDS))
        raise typer.BadParameter(f"supported fields: {fields}")
    return data[field]


def _merge_metadata_options(
    meta: list[str] | None,
    meta_json: list[str] | None,
    metadata_json: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    metadata.update(parse_meta(meta or []))
    metadata.update(parse_meta_json(meta_json or []))
    if metadata_json is not None:
        parsed = json.loads(metadata_json)
        if not isinstance(parsed, dict):
            raise ValueError("--metadata-json must be a JSON object")
        metadata.update(parsed)
    return metadata


def _check_metadata_unset_conflicts(
    updates: dict[str, object],
    unset_keys: list[str],
) -> None:
    conflicts = sorted(set(updates).intersection(unset_keys))
    if conflicts:
        joined = ", ".join(conflicts)
        raise ValueError(f"metadata key cannot be set and unset: {joined}")


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
        if op not in _RUN_QUERY_OPERATORS:
            raise typer.BadParameter("unsupported query expression")
        sql_field = _compile_run_query_field(field)
        value = _parse_query_literal(match.group("value"))
        if value is None and op in {"=", "!="}:
            clauses.append(f"{sql_field} {'IS' if op == '=' else 'IS NOT'} NULL")
        else:
            clauses.append(f"{sql_field} {op} ?")
            params.append(value)

    return " AND ".join(clauses), params


def _compile_run_query_field(field: str) -> str:
    if field in _RUN_QUERY_FIELDS:
        return _RUN_QUERY_FIELDS[field]
    metadata_prefix = "metadata."
    if field.startswith(metadata_prefix):
        key = field.removeprefix(metadata_prefix)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            return f"json_extract(runs.metadata_json, '$.\"{key}\"')"
    raise typer.BadParameter("unsupported query expression")


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
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if "." in value:
        return float(value)
    return int(value)


def _require_run(conn: sqlite3.Connection, run_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM runs WHERE id = ? AND deleted_at IS NULL", (run_id,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"run not found: {run_id}")


def _next_moc_position(conn: sqlite3.Connection, moc_path: str, section: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(position), 0) + 1
        FROM moc_entries
        WHERE moc_path = ? AND section = ? AND deleted_at IS NULL
        """,
        (moc_path, section),
    ).fetchone()
    return int(row[0])


def _move_moc_entry(
    conn: sqlite3.Connection,
    moc_path: str,
    section: str,
    run_id: str,
    position: int,
    ts: str,
) -> None:
    entries = _active_moc_entries(conn, moc_path, section)
    target = next((entry for entry in entries if entry["run_id"] == run_id), None)
    if target is None:
        raise typer.BadParameter(f"MOC entry not found: {run_id}")

    entries = [entry for entry in entries if entry["run_id"] != run_id]
    insert_at = min(max(position, 1), len(entries) + 1) - 1
    entries.insert(insert_at, target)
    _write_moc_positions(conn, entries, ts)


def _normalize_moc_positions(
    conn: sqlite3.Connection,
    moc_path: str,
    section: str,
    ts: str,
) -> None:
    _write_moc_positions(conn, _active_moc_entries(conn, moc_path, section), ts)


def _active_moc_entries(
    conn: sqlite3.Connection,
    moc_path: str,
    section: str,
) -> list[sqlite3.Row]:
    return [
        row
        for row in conn.execute(
            """
            SELECT id, run_id
            FROM moc_entries
            WHERE moc_path = ? AND section = ? AND deleted_at IS NULL
            ORDER BY position ASC, created_at ASC
            """,
            (moc_path, section),
        )
    ]


def _write_moc_positions(
    conn: sqlite3.Connection,
    entries: list[sqlite3.Row],
    ts: str,
) -> None:
    for index, entry in enumerate(entries, start=1):
        conn.execute(
            "UPDATE moc_entries SET position = ?, updated_at = ? WHERE id = ?",
            (index, ts, entry["id"]),
        )


def _registered_moc_sections(
    root: Path,
    state_dir: Path | None,
    *,
    moc_path: str | None = None,
) -> list[dict[str, object]]:
    params: list[object] = []
    moc_clause = ""
    if moc_path is not None:
        moc_clause = "AND moc_path = ?"
        params.append(moc_path)
    with transaction(root, state_dir=state_dir) as conn:
        return [
            {
                "moc_path": str(row["moc_path"]),
                "section": str(row["section"]),
                "rows": int(row["rows"]),
            }
            for row in conn.execute(
                f"""
                SELECT moc_path, section, COUNT(*) AS rows
                FROM moc_entries
                WHERE deleted_at IS NULL {moc_clause}
                GROUP BY moc_path, section
                ORDER BY moc_path ASC, section ASC
                """,
                params,
            )
        ]


def _moc_section_seen(
    root: Path,
    state_dir: Path | None,
    moc_path: str,
    section: str,
) -> bool:
    with transaction(root, state_dir=state_dir) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM moc_entries
            WHERE moc_path = ? AND section = ?
            LIMIT 1
            """,
            (moc_path, section),
        ).fetchone()
    return row is not None


def _format_moc_diff(result: dict[str, object]) -> str:
    lines = [
        f"MOC: {result['moc_path']}",
        f"Section: {result['section']}",
        f"Changed: {result['changed']}",
    ]
    for key in ["missing", "stale", "expected", "observed"]:
        values = result[key]
        lines.append(f"{key}: {', '.join(values) if values else '-'}")
    return "\n".join(lines)


def _count_active(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL"
    ).fetchone()
    return int(row[0])


if __name__ == "__main__":
    app()
