from __future__ import annotations

import json
import os
import re
import sqlite3
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import typer

from expnote.adapters.rlgarden import load_config, run_fields_from_config
from expnote.db import (
    append_event,
    default_docs_dir,
    init_store,
    new_id,
    now_iso,
    parse_meta,
    parse_meta_json,
    readonly_transaction,
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
from expnote.workspace import (
    WorkspaceContext,
    default_workspace_dir,
    list_workspaces,
    resolve_workspace,
    set_active_workspace,
    write_workspace_config,
)

app = typer.Typer(help="Local-first experiment notes.")
topic_app = typer.Typer(help="Manage experiment topics.")
run_app = typer.Typer(help="Manage experiment runs.")
relation_app = typer.Typer(help="Manage run relations.")
artifact_app = typer.Typer(help="Manage run artifacts.")
doc_app = typer.Typer(help="Manage analysis documents.")
moc_app = typer.Typer(help="Manage SQL MOC records.")
moc_topic_app = typer.Typer(help="Manage topics inside a SQL MOC.")
markdown_app = typer.Typer(help="Manage Markdown projections.")
markdown_table_app = typer.Typer(help="Manage Markdown MOC section tables.")
sync_app = typer.Typer(help="Sync projections.")
import_app = typer.Typer(help="Import external metadata.")
workspace_app = typer.Typer(help="Manage expnote workspaces.")

app.add_typer(topic_app, name="topic")
app.add_typer(run_app, name="run")
app.add_typer(relation_app, name="relation")
app.add_typer(artifact_app, name="artifact")
app.add_typer(doc_app, name="doc")
app.add_typer(moc_app, name="moc")
moc_app.add_typer(moc_topic_app, name="topic")
app.add_typer(markdown_app, name="markdown")
markdown_app.add_typer(markdown_table_app, name="table")
app.add_typer(sync_app, name="sync")
app.add_typer(import_app, name="import")
app.add_typer(workspace_app, name="workspace")


WorkspaceOption = Annotated[
    str | None,
    typer.Option("--workspace", "-w", help="Registered workspace name."),
]
WorkspaceDirOption = Annotated[
    Path | None,
    typer.Option(
        "--workspace-dir",
        help="Directory for expnote.sqlite, events.jsonl, config.toml, and cache.",
    ),
]
ObsidianRootOption = Annotated[
    Path | None,
    typer.Option("--obsidian-root", help="Optional Obsidian vault root."),
]


_AGENT_GUIDE = {
    "topic": "agent",
    "principles": [
        "SQLite is the source of truth",
        "Markdown is a projection",
        "Use --json for automation",
        "Use --workspace or expnote workspace use <name> on follow-up commands",
        "Edit structured fields through the CLI",
    ],
    "required_flags": ["--workspace"],
    "workflows": {
        "create_run": [
            "init",
            "moc add",
            "topic add --moc-id",
            "run add",
            "markdown table add",
            "sync all",
        ],
        "query_run": [
            "run show <run_id> --json",
            "run show <run_id> --field purpose",
            "run status running --json",
            "run query --status running --json",
            'run query --where "metadata.seed = 1" --json',
        ],
        "analysis_import": [
            "sync markdown",
            "sync markdown --pull-analysis",
            "sync markdown --pull-docs",
        ],
        "create_doc": [
            "doc add --doc-id <id> --moc-id <moc_id> --title <title>",
            "doc link <doc_id> <run_id>",
            "sync markdown",
        ],
        "handoff": [
            "validate --json",
            "markdown table diff --moc-path <path> --section <heading> --json",
        ],
    },
    "commands": {
        "init": "Create the workspace and configure Markdown projection paths",
        "topic.add": "Create a training or experiment topic",
        "run.add": "Create a SQL-backed run record",
        "run.show": "Read SQL-backed Purpose, Relation, Result, Metadata, Analysis",
        "run.update": "Update structured run fields and metadata",
        "run.query": "Query runs with restricted SQL-like filters and metadata keys",
        "run.status": "List runs with a specific manual status",
        "doc.add": "Create a SQL-backed cross-run analysis document",
        "doc.show": "Read a SQL-backed analysis document and related runs",
        "doc.list": "List SQL-backed analysis documents",
        "doc.update": "Update document title, body, and metadata",
        "doc.link": "Attach a run to a document",
        "doc.unlink": "Detach a run from a document",
        "doc.delete": "Soft-delete an analysis document",
        "moc.add": "Create a SQL-backed first-level MOC record",
        "moc.show": "Read a SQL-backed MOC with topics and docs",
        "moc.topic.add": "Create a topic inside a SQL MOC",
        "markdown.table.add": "Add a run to a Markdown MOC section table",
        "markdown.table.add_topic": "Add all active topic runs to a Markdown table",
        "markdown.table.sections": "List registered Markdown table sections",
        "markdown.table.diff": "Compare a managed Markdown table with SQLite",
        "web": "Start the read-only SQL-backed web UI",
        "sync.markdown": "Render SQLite records into Markdown",
        "sync.all": "Render run notes, analysis documents, auto index, and MOCs",
        "sync.markdown.pull_analysis": "Import Obsidian Analysis into SQLite",
        "sync.markdown.pull_docs": "Import Obsidian document body into SQLite",
        "validate": "Check active record counts before handoff",
    },
    "conflict_policy": {
        "structured_fields": (
            "Edit Purpose, Relation, Result, Metadata through CLI only"
        ),
        "analysis": "Obsidian edits require sync markdown --pull-analysis",
        "documents": "Obsidian document body edits require sync markdown --pull-docs",
        "moc_tables": (
            "Managed Markdown MOC tables should be repaired with markdown table sync"
        ),
        "projection_paths": (
            "auto index defaults to workspace-dir/index.md; Markdown tables use "
            "markdown table --moc-path"
        ),
    },
    "examples": {
        "init": (
            "expnote init --workspace <name> --obsidian-root <vault> "
            "--notes-dir <runs-dir>"
        ),
        "create_moc": (
            "expnote moc add --workspace <name> "
            "--moc-id <moc_id> --title <title> --json"
        ),
        "create_run": (
            "expnote run add --workspace <name> "
            "--moc-id <moc_id> --topic <topic> --run-id <id> --purpose <text> "
            "--meta-json seed=1 --json"
        ),
        "create_run_by_topic_id": (
            "expnote run create --workspace <name> "
            "--topic-id <topic_id> --id <id> --purpose <text> --json"
        ),
        "metadata_json": (
            "expnote run update <id> --workspace <name> "
            '--metadata-json \'{"seed":1,"algo":"calql"}\''
        ),
        "unset_metadata": (
            "expnote run update <id> --workspace <name> --unset-meta seed"
        ),
        "append_analysis": (
            "expnote run update <id> --workspace <name> --append-analysis <text>"
        ),
        "show_run": ("expnote run show <id> --workspace <name> --json"),
        "show_field": ("expnote run show <id> --workspace <name> --field status"),
        "create_doc": (
            "expnote doc add --workspace <name> "
            "--doc-id <id> --moc-id <moc_id> --title <title> "
            "--run-id <run_id> --body <text> --json"
        ),
        "show_doc": ("expnote doc show <id> --workspace <name> --json"),
        "append_doc_body": (
            "expnote doc update <id> --workspace <name> --append-body <text> --json"
        ),
        "moc_diff": (
            "expnote markdown table diff --workspace <name> "
            "--moc-path <moc.md> --section <heading> --json"
        ),
        "moc_sections": (
            "expnote markdown table sections --workspace <name> "
            "--moc-path <moc.md> --json"
        ),
        "moc_add_topic": (
            "expnote markdown table add-topic --workspace <name> "
            "--topic <topic> --moc-path <moc.md> --section <heading> --json"
        ),
        "web": "expnote web --workspace <name> --no-open",
        "sync_all": "expnote sync all --workspace <name> --json",
    },
    "common_pitfalls": {
        "run_create": "run create is supported as an alias for run add",
        "run_id": (
            "use --run-id or --id, not a positional id flag; prefer the wandb "
            "run id when a run is tracked by wandb"
        ),
        "topic_id": "use --topic-id when you have a topic id; use --topic for title",
        "metadata_json": (
            "use --metadata-json '{...}' for a whole object; --meta-json is key=json"
        ),
        "status": (
            "status is manual; update it explicitly with "
            "run update <id> --status finished"
        ),
        "result": (
            "keep Result concise and outcome-only; put analysis in run "
            "Analysis or cross-run docs"
        ),
        "documents": (
            "doc body is stored in SQLite; use sync markdown --pull-docs "
            "to import Obsidian body edits"
        ),
        "curated_mocs": (
            "sync markdown does not update curated Markdown tables; use "
            "sync all or markdown table sync/add/add-topic"
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


def _workspace_context(
    workspace: str | None,
    workspace_dir: Path | None,
    *,
    require_obsidian: bool = False,
) -> WorkspaceContext:
    return resolve_workspace(
        workspace=workspace,
        workspace_dir=workspace_dir,
        require_obsidian=require_obsidian,
    )


def _render_agent_guide() -> str:
    return "\n".join(
        [
            "# expnote agent guide",
            "",
            "Core rules:",
            "- SQLite is the source of truth",
            "- Obsidian Markdown is an optional projection",
            "- Use --json for automation",
            "- Use expnote workspace use <name> before follow-up commands",
            "",
            "Minimal workflow:",
            "init -> moc add -> topic add --moc-id -> run add -> "
            "markdown table add -> sync all",
            "Prefer using the wandb run id as expnote run id when available.",
            "",
            "Read records from SQLite:",
            "expnote run show <run_id> --json",
            "expnote run show <run_id> --field purpose",
            "expnote run status running --json",
            "expnote run query --status running --json",
            'expnote run query --where "metadata.seed = 1" --json',
            "expnote run update <run_id> --append-analysis <text>",
            "expnote run update <run_id> --metadata-json '{\"seed\":1}'",
            "expnote doc show <doc_id> --json",
            "expnote doc add --doc-id <doc_id> --moc-id <moc_id> "
            "--title <title> --run-id <run_id> --body <text> --json",
            "expnote doc update <doc_id> --append-body <text> --json",
            "",
            "MOC workflow:",
            "expnote markdown table sections --moc-path <path> --json",
            "expnote markdown table add-topic --topic <topic> "
            "--moc-path <path> --section <heading> --json",
            "expnote web --no-open",
            "expnote sync all --json",
            "",
            "Obsidian conflict policy:",
            "- Edit Purpose, Relation, Result, Metadata through CLI only",
            "- Import Obsidian Analysis with sync markdown --pull-analysis",
            "- Import Obsidian doc body with sync markdown --pull-docs",
            "- Check managed Markdown tables with expnote markdown table diff --json",
            "- Auto index defaults to workspace-dir/index.md, outside Obsidian",
            "- status is manual; update completed runs with "
            "expnote run update <id> --status finished",
            "- Keep Result concise and outcome-only; put interpretation, "
            "diagnosis, and comparisons in Analysis or docs",
            "",
            "Handoff checks:",
            "expnote validate --json",
            "expnote markdown table diff --moc-path <path> --section <heading> --json",
        ]
    )


def _moc_id_exists(conn: sqlite3.Connection, moc_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM mocs WHERE id = ? AND deleted_at IS NULL", (moc_id,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"MOC not found: {moc_id}")
    return str(row["id"])


def _topic_id(
    conn: sqlite3.Connection,
    title: str,
    moc_id: str | None = None,
) -> str:
    params: list[object] = [title]
    moc_clause = ""
    if moc_id is not None:
        moc_clause = "AND moc_id = ?"
        params.append(moc_id)
    rows = conn.execute(
        f"""
        SELECT id FROM topics
        WHERE title = ? {moc_clause} AND deleted_at IS NULL
        ORDER BY created_at DESC, id DESC
        """,
        params,
    ).fetchall()
    if len(rows) > 1 and moc_id is None:
        raise typer.BadParameter("--moc-id is required for duplicate topic title")
    row = rows[0] if rows else None
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
    moc_id: str | None = None,
) -> str:
    if topic is not None and topic_id is not None:
        raise typer.BadParameter("--topic and --topic-id cannot be used together")
    if topic is None and topic_id is None:
        raise typer.BadParameter("--topic or --topic-id is required")
    if topic_id is not None:
        return _topic_id_exists(conn, topic_id)
    assert topic is not None
    return _topic_id(conn, topic, moc_id=moc_id)


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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    obsidian_root: ObsidianRootOption = None,
    notes_dir: Annotated[
        str, typer.Option(help="Directory for run notes.")
    ] = "notes/runs",
    docs_dir: Annotated[
        str | None,
        typer.Option(help="Directory for analysis documents."),
    ] = None,
    index_path: Annotated[
        str,
        typer.Option(
            "--index-path",
            help="Generated auto-index path relative to workspace-dir.",
        ),
    ] = "index.md",
    moc_path: Annotated[
        str | None,
        typer.Option(
            "--moc-path",
            help="Legacy generated auto-index path relative to obsidian-root.",
        ),
    ] = None,
    project: Annotated[str | None, typer.Option(help="Project name.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Initialize an expnote workspace."""
    workspace_name = workspace or project or Path.cwd().name
    state_dir = (
        workspace_dir.expanduser().resolve()
        if workspace_dir is not None
        else default_workspace_dir(workspace_name).resolve()
    )
    root = (
        obsidian_root.expanduser().resolve() if obsidian_root is not None else state_dir
    )
    output_docs_dir = docs_dir or default_docs_dir(notes_dir)
    init_store(
        root,
        state_dir=state_dir,
        notes_dir=notes_dir,
        docs_dir=docs_dir,
        index_path=index_path,
        moc_path=moc_path,
        project=project or workspace_name,
        obsidian_enabled=obsidian_root is not None,
    )
    write_workspace_config(
        workspace=workspace_name,
        workspace_dir=state_dir,
        set_active=True,
    )
    output_index_path = moc_path or index_path
    append_event(
        root,
        "init",
        {
            "workspace": workspace_name,
            "workspace_dir": str(state_dir),
            "obsidian_root": str(obsidian_root.resolve()) if obsidian_root else None,
            "notes_dir": notes_dir if obsidian_root is not None else None,
            "docs_dir": output_docs_dir if obsidian_root is not None else None,
            "index_path": output_index_path,
            "index_scope": "obsidian_root" if moc_path is not None else "workspace_dir",
        },
        state_dir=state_dir,
    )
    _emit(
        {
            "workspace": workspace_name,
            "workspace_dir": str(state_dir),
            "obsidian_root": str(obsidian_root.resolve()) if obsidian_root else None,
            "notes_dir": notes_dir if obsidian_root is not None else None,
            "docs_dir": output_docs_dir if obsidian_root is not None else None,
            "index_path": output_index_path,
            "index_scope": "obsidian_root" if moc_path is not None else "workspace_dir",
        },
        json_output,
    )


@workspace_app.command("use")
def workspace_use(
    workspace: Annotated[str, typer.Argument(help="Registered workspace name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _emit(set_active_workspace(workspace), json_output)


@workspace_app.command("list")
def workspace_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _emit(list_workspaces(), json_output)


@topic_app.command("add")
def topic_add(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str, typer.Option("--moc-id", help="Parent SQL MOC id.")
    ] = "default",
    summary: Annotated[str, typer.Option(help="Short topic summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    topic_id = new_id("topic")
    with transaction(root, state_dir=state_dir) as conn:
        _moc_id_exists(conn, moc_id)
        conn.execute(
            """
            INSERT INTO topics(id, moc_id, title, summary, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (topic_id, moc_id, title, summary, ts, ts),
        )
    payload = {"id": topic_id, "moc_id": moc_id, "title": title, "summary": summary}
    append_event(root, "topic.add", payload, state_dir=state_dir)
    _emit(payload, json_output)


@topic_app.command("list")
def topic_list(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Filter by SQL MOC id.")
    ] = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    clauses = []
    params: list[object] = []
    if not include_deleted:
        clauses.append("topics.deleted_at IS NULL")
    if moc_id is not None:
        clauses.append("topics.moc_id = ?")
        params.append(moc_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with readonly_transaction(root, state_dir=state_dir) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT topics.*, mocs.title AS moc_title
                FROM topics JOIN mocs ON mocs.id = topics.moc_id
                {where}
                ORDER BY topics.created_at DESC, topics.title DESC
                """,
                params,
            )
        ]
    _emit(rows, json_output)


@topic_app.command("update")
def topic_update(
    title: Annotated[str, typer.Argument(help="Existing topic title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Parent SQL MOC id.")
    ] = None,
    new_title: Annotated[str | None, typer.Option(help="New title.")] = None,
    summary: Annotated[str | None, typer.Option(help="New summary.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        tid = _topic_id(conn, title, moc_id=moc_id)
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Parent SQL MOC id.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        tid = _topic_id(conn, title, moc_id=moc_id)
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
    run_id: Annotated[str, typer.Option("--run-id", "--id", help="Stable run id.")],
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
    moc_id: Annotated[
        str | None,
        typer.Option("--moc-id", help="Parent SQL MOC id when using --topic."),
    ] = None,
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
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
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
        moc_id=moc_id,
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
    moc_id: str | None = None,
    purpose: str,
    relation: str,
    result: str,
    analysis: str,
    status: str,
    metadata: dict[str, object],
) -> dict[str, object]:
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        tid = _resolve_topic_id(conn, topic, topic_id, moc_id=moc_id)
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
        row = conn.execute(
            """
            SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                mocs.title AS moc_title
            FROM runs
            JOIN topics ON runs.topic_id = topics.id
            JOIN mocs ON topics.moc_id = mocs.id
            WHERE runs.id = ?
            """,
            (run_id,),
        ).fetchone()
    data = row_to_dict(row)
    append_event(root, "run.add", data, state_dir=state_dir)
    return data


@run_app.command("list")
def run_list(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    topic: Annotated[str | None, typer.Option(help="Filter by topic title.")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by run status.")] = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Filter by SQL MOC id.")
    ] = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    rows = _list_runs(
        root,
        state_dir=state_dir,
        topic=topic,
        status=status,
        moc_id=moc_id,
        include_deleted=include_deleted,
    )
    _emit(rows, json_output)


def _list_runs(
    root: Path,
    *,
    state_dir: Path | None,
    topic: str | None = None,
    status: str | None = None,
    moc_id: str | None = None,
    include_deleted: bool = False,
) -> list[dict[str, object]]:
    params: list[object] = []
    clauses = []
    if not include_deleted:
        clauses.append("runs.deleted_at IS NULL")
    if topic is not None:
        clauses.append("topics.title = ?")
        params.append(topic)
    if status is not None:
        clauses.append("runs.status = ?")
        params.append(status)
    if moc_id is not None:
        clauses.append("topics.moc_id = ?")
        params.append(moc_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with readonly_transaction(root, state_dir=state_dir) as conn:
        return [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                    mocs.title AS moc_title
                FROM runs
                JOIN topics ON runs.topic_id = topics.id
                JOIN mocs ON topics.moc_id = mocs.id
                {where}
                ORDER BY runs.started_at DESC, runs.id DESC
                """,
                params,
            )
        ]


@run_app.command("status")
def run_status(
    status: Annotated[str, typer.Argument(help="Run status to list.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    topic: Annotated[str | None, typer.Option(help="Filter by topic title.")] = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Filter by SQL MOC id.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    rows = _list_runs(
        root,
        state_dir=state_dir,
        topic=topic,
        status=status,
        moc_id=moc_id,
        include_deleted=False,
    )
    _emit(rows, json_output)


@run_app.command("show")
def run_show(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    field: Annotated[
        str | None, typer.Option("--field", help="Return one public run field.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    with readonly_transaction(root, state_dir=state_dir) as conn:
        row = conn.execute(
            """
            SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                mocs.title AS moc_title
            FROM runs
            JOIN topics ON runs.topic_id = topics.id
            JOIN mocs ON topics.moc_id = mocs.id
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
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
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    status: Annotated[str | None, typer.Option(help="Filter by run status.")] = None,
    where: Annotated[
        str, typer.Option(help="Restricted SQL-like WHERE expression.")
    ] = "1 = 1",
    order_by: Annotated[
        str, typer.Option(help="Restricted SQL-like ORDER BY expression.")
    ] = "started_at DESC",
    limit: Annotated[int, typer.Option(help="Max rows.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    where_sql, where_params = _compile_run_where(where)
    if status is not None:
        where_sql = f"({where_sql}) AND runs.status = ?"
        where_params.append(status)
    order_sql = _compile_run_order_by(order_by)
    with readonly_transaction(root, state_dir=state_dir) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"""
                SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                    mocs.title AS moc_title
                FROM runs
                JOIN topics ON runs.topic_id = topics.id
                JOIN mocs ON topics.moc_id = mocs.id
                WHERE runs.deleted_at IS NULL AND ({where_sql})
                ORDER BY {order_sql}
                LIMIT ?
                """,
                [*where_params, limit],
            )
        ]
    _emit(rows, json_output)


@doc_app.command("add")
def doc_add(
    doc_id: Annotated[str, typer.Option("--doc-id", help="Stable document id.")],
    moc_id: Annotated[str, typer.Option("--moc-id", help="Parent SQL MOC id.")],
    title: Annotated[str, typer.Option("--title", help="Document title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    body: Annotated[str, typer.Option("--body", help="Document body.")] = "",
    run_ids: Annotated[
        list[str] | None,
        typer.Option("--run-id", help="Related run id. Can be used repeatedly."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _moc_id_exists(conn, moc_id)
        conn.execute(
            """
            INSERT INTO docs(
                id, moc_id, title, body, metadata_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, moc_id, title, body, "{}", ts, ts),
        )
        seen_run_ids: set[str] = set()
        position = 1
        for run_id in run_ids or []:
            if run_id in seen_run_ids:
                continue
            _require_run_in_moc(conn, run_id, moc_id)
            seen_run_ids.add(run_id)
            conn.execute(
                """
                INSERT INTO doc_runs(
                    id, doc_id, run_id, position, role, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, '', '', ?, ?)
                """,
                (new_id("docrun"), doc_id, run_id, position, ts, ts),
            )
            position += 1
        data = _doc_data(conn, doc_id)
    append_event(root, "doc.add", data, state_dir=state_dir)
    _emit(data, json_output)


@doc_app.command("show")
def doc_show(
    doc_id: Annotated[str, typer.Argument(help="Document id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    with readonly_transaction(root, state_dir=state_dir) as conn:
        data = _doc_data(conn, doc_id)
    _emit(data, json_output)


@doc_app.command("list")
def doc_list(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Filter by SQL MOC id.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    params: list[object] = []
    moc_clause = ""
    if moc_id is not None:
        moc_clause = "AND docs.moc_id = ?"
        params.append(moc_id)
    with readonly_transaction(root, state_dir=state_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT docs.*, mocs.title AS moc_title
            FROM docs JOIN mocs ON docs.moc_id = mocs.id
            WHERE docs.deleted_at IS NULL {moc_clause}
            ORDER BY docs.updated_at DESC, docs.id DESC
            """,
            params,
        ).fetchall()
        data = [_doc_from_row(conn, row) for row in rows]
    _emit(data, json_output)


@doc_app.command("update")
def doc_update(
    doc_id: Annotated[str, typer.Argument(help="Document id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    title: Annotated[str | None, typer.Option(help="New document title.")] = None,
    body: Annotated[str | None, typer.Option("--body", help="New body.")] = None,
    append_body: Annotated[
        str | None,
        typer.Option(
            "--append-body",
            help="Append to body, separated from existing text by one blank line.",
        ),
    ] = None,
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
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    if body is not None and append_body is not None:
        raise typer.BadParameter("--body and --append-body cannot be used together")
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        row = _require_doc_row(conn, doc_id)
        if append_body is not None:
            current_body = row["body"] or ""
            body = (
                append_body
                if current_body == ""
                else f"{current_body}\n\n{append_body}"
            )
        if title is not None:
            conn.execute(
                "UPDATE docs SET title = ?, updated_at = ? WHERE id = ?",
                (title, ts, doc_id),
            )
        if body is not None:
            conn.execute(
                "UPDATE docs SET body = ?, updated_at = ? WHERE id = ?",
                (body, ts, doc_id),
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
                "UPDATE docs SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), ts, doc_id),
            )
        data = _doc_data(conn, doc_id)
    append_event(root, "doc.update", data, state_dir=state_dir)
    _emit(data, json_output)


@doc_app.command("link")
def doc_link(
    doc_id: Annotated[str, typer.Argument(help="Document id.")],
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    role: Annotated[str, typer.Option(help="Run role in this document.")] = "",
    note: Annotated[str, typer.Option(help="Link note.")] = "",
    position: Annotated[
        int | None, typer.Option(help="Position in related runs.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        doc = _require_doc_row(conn, doc_id)
        _require_run_in_moc(conn, run_id, str(doc["moc_id"]))
        existing = conn.execute(
            "SELECT position, deleted_at FROM doc_runs WHERE doc_id = ? AND run_id = ?",
            (doc_id, run_id),
        ).fetchone()
        if position is None:
            if existing is not None and existing["deleted_at"] is None:
                position = int(existing["position"])
            else:
                position = _next_doc_run_position(conn, doc_id)
        conn.execute(
            """
            INSERT INTO doc_runs(
                id, doc_id, run_id, position, role, note, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, run_id) DO UPDATE SET
                position = excluded.position,
                role = excluded.role,
                note = excluded.note,
                updated_at = excluded.updated_at,
                deleted_at = NULL
            """,
            (new_id("docrun"), doc_id, run_id, position, role, note, ts, ts),
        )
        _normalize_doc_run_positions(conn, doc_id, ts)
        data = _doc_data(conn, doc_id)
    append_event(root, "doc.link", data, state_dir=state_dir)
    _emit(data, json_output)


@doc_app.command("unlink")
def doc_unlink(
    doc_id: Annotated[str, typer.Argument(help="Document id.")],
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _require_doc_row(conn, doc_id)
        conn.execute(
            """
            UPDATE doc_runs
            SET deleted_at = ?, updated_at = ?
            WHERE doc_id = ? AND run_id = ?
            """,
            (ts, ts, doc_id, run_id),
        )
        _normalize_doc_run_positions(conn, doc_id, ts)
        data = _doc_data(conn, doc_id)
    append_event(
        root,
        "doc.unlink",
        {"doc_id": doc_id, "run_id": run_id},
        state_dir=state_dir,
    )
    _emit(data, json_output)


@doc_app.command("delete")
def doc_delete(
    doc_id: Annotated[str, typer.Argument(help="Document id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _require_doc_row(conn, doc_id)
        conn.execute(
            "UPDATE docs SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, doc_id),
        )
        conn.execute(
            """
            UPDATE doc_runs
            SET deleted_at = ?, updated_at = ?
            WHERE doc_id = ? AND deleted_at IS NULL
            """,
            (ts, ts, doc_id),
        )
    payload = {"id": doc_id, "deleted_at": ts}
    append_event(root, "doc.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@relation_app.command("add")
def relation_add(
    src_run_id: Annotated[str, typer.Argument(help="Source run id.")],
    dst_run_id: Annotated[str, typer.Argument(help="Destination run id.")],
    kind: Annotated[str, typer.Option(help="Relation kind.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    note: Annotated[str, typer.Option(help="Relation note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    note: Annotated[str, typer.Option(help="Artifact note.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    with readonly_transaction(root, state_dir=state_dir) as conn:
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
def sql_moc_add(
    moc_id: Annotated[str, typer.Option("--moc-id", help="Stable SQL MOC id.")],
    title: Annotated[str, typer.Option("--title", help="MOC title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    summary: Annotated[str, typer.Option(help="Short MOC summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            """
            INSERT INTO mocs(id, title, summary, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (moc_id, title, summary, ts, ts),
        )
        row = conn.execute("SELECT * FROM mocs WHERE id = ?", (moc_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "moc.add", data, state_dir=state_dir)
    _emit(data, json_output)


@moc_app.command("list")
def sql_moc_list(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    include_deleted: Annotated[
        bool, typer.Option(help="Include soft-deleted rows.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with readonly_transaction(root, state_dir=state_dir) as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                f"SELECT * FROM mocs {where} ORDER BY updated_at DESC, id ASC"
            )
        ]
    _emit(rows, json_output)


@moc_app.command("show")
def sql_moc_show(
    moc_id: Annotated[str, typer.Argument(help="SQL MOC id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    with readonly_transaction(root, state_dir=state_dir) as conn:
        data = _moc_data(conn, moc_id)
    _emit(data, json_output)


@moc_app.command("update")
def sql_moc_update(
    moc_id: Annotated[str, typer.Argument(help="SQL MOC id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    title: Annotated[str | None, typer.Option("--title", help="New title.")] = None,
    summary: Annotated[str | None, typer.Option(help="New summary.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _moc_id_exists(conn, moc_id)
        if title is not None:
            conn.execute(
                "UPDATE mocs SET title = ?, updated_at = ? WHERE id = ?",
                (title, ts, moc_id),
            )
        if summary is not None:
            conn.execute(
                "UPDATE mocs SET summary = ?, updated_at = ? WHERE id = ?",
                (summary, ts, moc_id),
            )
        row = conn.execute("SELECT * FROM mocs WHERE id = ?", (moc_id,)).fetchone()
    data = row_to_dict(row)
    append_event(root, "moc.update", data, state_dir=state_dir)
    _emit(data, json_output)


@moc_app.command("delete")
def sql_moc_delete(
    moc_id: Annotated[str, typer.Argument(help="SQL MOC id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    ts = now_iso()
    with transaction(root, state_dir=state_dir) as conn:
        _moc_id_exists(conn, moc_id)
        conn.execute(
            "UPDATE mocs SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, moc_id),
        )
    payload = {"id": moc_id, "deleted_at": ts}
    append_event(root, "moc.delete", payload, state_dir=state_dir)
    _emit(payload, json_output)


@moc_topic_app.command("add")
def moc_topic_add(
    moc_id: Annotated[str, typer.Argument(help="SQL MOC id.")],
    title: Annotated[str, typer.Option("--title", help="Topic title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    summary: Annotated[str, typer.Option(help="Short topic summary.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    topic_add(
        title=title,
        workspace=workspace,
        workspace_dir=workspace_dir,
        moc_id=moc_id,
        summary=summary,
        json_output=json_output,
    )


@moc_topic_app.command("list")
def moc_topic_list(
    moc_id: Annotated[str, typer.Argument(help="SQL MOC id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    topic_list(
        workspace=workspace,
        workspace_dir=workspace_dir,
        moc_id=moc_id,
        include_deleted=False,
        json_output=json_output,
    )


@markdown_table_app.command("add")
def moc_add(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    append_event(root, "markdown.table.add", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@markdown_table_app.command("remove")
def moc_remove(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    append_event(root, "markdown.table.remove", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@markdown_table_app.command("update")
def moc_update(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    position: Annotated[int, typer.Option(help="New table position.")] = 1,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    append_event(root, "markdown.table.update", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@markdown_table_app.command("list")
def moc_list(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str | None, typer.Option(help="MOC level-two heading.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    if not moc_path:
        raise typer.BadParameter("--moc-path is required")
    params: list[object] = [moc_path]
    section_clause = ""
    if section is not None:
        section_clause = "AND moc_entries.section = ?"
        params.append(section)
    with readonly_transaction(root, state_dir=state_dir) as conn:
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


@markdown_table_app.command("sections")
def moc_sections(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    if not moc_path:
        raise typer.BadParameter("--moc-path is required")
    _emit(_registered_moc_sections(root, state_dir, moc_path=moc_path), json_output)


@markdown_table_app.command("add-topic")
def moc_add_topic(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
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
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
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
    append_event(root, "markdown.table.add_topic", payload, state_dir=state_dir)
    _emit({**payload, "sync": sync_result}, json_output)


@markdown_table_app.command("diff")
def moc_diff(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_path: Annotated[
        str, typer.Option(help="Curated MOC path relative to root.")
    ] = "",
    section: Annotated[str, typer.Option(help="MOC level-two heading.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    result = diff_moc_section(root, state_dir, moc_path, section)
    if json_output:
        _emit(result, True)
    else:
        _emit(_format_moc_diff(result), False)


@markdown_table_app.command("sync")
def moc_sync(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
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
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    if not moc_path or not section:
        raise typer.BadParameter("--moc-path and --section are required")
    _preflight_moc_section_target(root, moc_path)
    if not allow_empty and not _moc_section_seen(root, state_dir, moc_path, section):
        raise typer.BadParameter(
            "no registered MOC entries for section. "
            "Use `expnote markdown table sections`, "
            "`expnote markdown table add`, or "
            "`expnote markdown table add-topic`; pass --allow-empty "
            "to create an empty section."
        )
    result = _sync_moc_section_or_error(root, state_dir, moc_path, section)
    _emit(result, json_output)


@sync_app.command("markdown")
def sync_markdown_command(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    pull_analysis: Annotated[
        bool, typer.Option("--pull-analysis", help="Import run note Analysis first.")
    ] = False,
    pull_docs: Annotated[
        bool, typer.Option("--pull-docs", help="Import document body first.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite changed Analysis or document body."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    try:
        result = sync_markdown(
            root,
            state_dir=state_dir,
            pull_analysis=pull_analysis,
            pull_docs=pull_docs,
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
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    pull_analysis: Annotated[
        bool, typer.Option("--pull-analysis", help="Import run note Analysis first.")
    ] = False,
    pull_docs: Annotated[
        bool, typer.Option("--pull-docs", help="Import document body first.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite changed Analysis or document body."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir, require_obsidian=True)
    root = ctx.root
    state_dir = ctx.workspace_dir
    try:
        result = sync_markdown(
            root,
            state_dir=state_dir,
            pull_analysis=pull_analysis,
            pull_docs=pull_docs,
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


@markdown_app.command("sync")
def markdown_sync_command(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    pull_analysis: Annotated[
        bool, typer.Option("--pull-analysis", help="Import run note Analysis first.")
    ] = False,
    pull_docs: Annotated[
        bool, typer.Option("--pull-docs", help="Import document body first.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite changed Analysis or document body."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    sync_markdown_command(
        workspace=workspace,
        workspace_dir=workspace_dir,
        pull_analysis=pull_analysis,
        pull_docs=pull_docs,
        force=force,
        json_output=json_output,
    )


@app.command()
def validate(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    with readonly_transaction(root, state_dir=state_dir) as conn:
        counts = {
            "topics": _count_active(conn, "topics"),
            "runs": _count_active(conn, "runs"),
            "artifacts": _count_active(conn, "artifacts"),
        }
    conflicts = (
        projection_conflicts(root, state_dir=state_dir)
        if ctx.obsidian_root is not None
        else []
    )
    result = {"ok": not conflicts, "counts": counts, "projection_conflicts": conflicts}
    _emit(result, json_output)


@app.command()
def web(
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    host: Annotated[str, typer.Option(help="Server host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Server port.")] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the web UI in a browser.")
    ] = True,
) -> None:
    """Start the read-only expnote web UI."""
    import uvicorn

    from expnote.web import create_app

    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    app_obj = create_app(root, state_dir=state_dir)
    url_host = "localhost" if host in {"127.0.0.1", "0.0.0.0"} else host
    url = f"http://{url_host}:{port}"
    typer.echo(f"expnote web is read-only: {url}")
    if open_browser and os.environ.get("EXPNOTE_NO_BROWSER") != "1":
        webbrowser.open(url)
    uvicorn.run(app_obj, host=host, port=port, log_level="info")


@import_app.command("rlgarden")
def import_rlgarden(
    config_path: Annotated[
        Path, typer.Argument(help="rl-garden resolved config.json.")
    ],
    topic: Annotated[str, typer.Option(help="Topic title.")],
    workspace: WorkspaceOption = None,
    workspace_dir: WorkspaceDirOption = None,
    moc_id: Annotated[
        str | None, typer.Option("--moc-id", help="Parent SQL MOC id.")
    ] = None,
    status: Annotated[str, typer.Option(help="Initial status.")] = "running",
    purpose: Annotated[str | None, typer.Option(help="Override purpose.")] = None,
    relation: Annotated[str, typer.Option(help="Relation summary.")] = "",
    result: Annotated[str, typer.Option(help="Result summary.")] = "",
    analysis: Annotated[str, typer.Option(help="Initial analysis.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    ctx = _workspace_context(workspace, workspace_dir)
    root = ctx.root
    state_dir = ctx.workspace_dir
    fields = run_fields_from_config(load_config(config_path))
    data = _insert_run(
        root,
        state_dir=state_dir,
        run_id=fields["run_id"],
        topic=topic,
        moc_id=moc_id,
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
    "moc_id": "topics.moc_id",
    "moc_title": "mocs.title",
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
    "moc_id",
    "moc_title",
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


def _doc_data(conn: sqlite3.Connection, doc_id: str) -> dict[str, object]:
    row = _require_doc_row(conn, doc_id)
    return _doc_from_row(conn, row)


def _doc_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    data = row_to_dict(row)
    data["runs"] = _doc_runs(conn, str(data["id"]))
    return data


def _doc_runs(conn: sqlite3.Connection, doc_id: str) -> list[dict[str, object]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT
                doc_runs.id,
                doc_runs.doc_id,
                doc_runs.run_id,
                doc_runs.position,
                doc_runs.role,
                doc_runs.note,
                runs.status,
                runs.purpose,
                runs.result,
                topics.title AS topic_title,
                topics.moc_id
            FROM doc_runs
            JOIN runs ON runs.id = doc_runs.run_id
            JOIN topics ON topics.id = runs.topic_id
            WHERE
                doc_runs.doc_id = ?
                AND doc_runs.deleted_at IS NULL
                AND runs.deleted_at IS NULL
            ORDER BY doc_runs.position ASC, doc_runs.created_at ASC
            """,
            (doc_id,),
        )
    ]


def _require_doc_row(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT docs.*, mocs.title AS moc_title
        FROM docs JOIN mocs ON docs.moc_id = mocs.id
        WHERE docs.id = ? AND docs.deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"doc not found: {doc_id}")
    return row


def _require_run(conn: sqlite3.Connection, run_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM runs WHERE id = ? AND deleted_at IS NULL", (run_id,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"run not found: {run_id}")


def _require_run_in_moc(
    conn: sqlite3.Connection,
    run_id: str,
    moc_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT runs.id
        FROM runs JOIN topics ON topics.id = runs.topic_id
        WHERE runs.id = ?
            AND topics.moc_id = ?
            AND runs.deleted_at IS NULL
            AND topics.deleted_at IS NULL
        """,
        (run_id, moc_id),
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"run not found in MOC {moc_id}: {run_id}")


def _moc_data(conn: sqlite3.Connection, moc_id: str) -> dict[str, object]:
    row = conn.execute(
        "SELECT * FROM mocs WHERE id = ? AND deleted_at IS NULL", (moc_id,)
    ).fetchone()
    if row is None:
        raise typer.BadParameter(f"MOC not found: {moc_id}")
    data = row_to_dict(row)
    data["topics"] = [
        row_to_dict(topic)
        for topic in conn.execute(
            """
            SELECT * FROM topics
            WHERE moc_id = ? AND deleted_at IS NULL
            ORDER BY created_at DESC, title DESC
            """,
            (moc_id,),
        )
    ]
    data["docs"] = [
        row_to_dict(doc)
        for doc in conn.execute(
            """
            SELECT * FROM docs
            WHERE moc_id = ? AND deleted_at IS NULL
            ORDER BY updated_at DESC, id DESC
            """,
            (moc_id,),
        )
    ]
    return data


def _next_doc_run_position(conn: sqlite3.Connection, doc_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(position), 0) + 1
        FROM doc_runs
        WHERE doc_id = ? AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    return int(row[0])


def _normalize_doc_run_positions(
    conn: sqlite3.Connection,
    doc_id: str,
    ts: str,
) -> None:
    entries = [
        row
        for row in conn.execute(
            """
            SELECT id
            FROM doc_runs
            WHERE doc_id = ? AND deleted_at IS NULL
            ORDER BY position ASC, created_at ASC
            """,
            (doc_id,),
        )
    ]
    for position, entry in enumerate(entries, start=1):
        conn.execute(
            "UPDATE doc_runs SET position = ?, updated_at = ? WHERE id = ?",
            (position, ts, entry["id"]),
        )


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
    with readonly_transaction(root, state_dir=state_dir) as conn:
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
    with readonly_transaction(root, state_dir=state_dir) as conn:
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
