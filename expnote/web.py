# ruff: noqa: E501

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Annotated, Any

import markdown as markdown_lib
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from expnote.db import readonly_transaction, row_to_dict
from expnote.links import render_html_run_links
from expnote.wandb_live import (
    WandbLiveError,
    clear_wandb_cache,
    fetch_wandb_charts,
    wandb_cache_stats,
)


def create_app(root: Path, state_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="expnote", docs_url=None, redoc_url=None)
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    cache_dir = (state_dir or root / ".expnote") / "wandb-cache"

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML, headers={"Cache-Control": "no-store"})

    @app.get("/assets/plotly.min.js")
    def plotly_asset() -> Response:
        try:
            from plotly.offline.offline import get_plotlyjs
        except ImportError as exc:
            raise HTTPException(status_code=404, detail="Plotly is not installed") from exc
        return Response(
            get_plotlyjs(),
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/mocs")
    def api_mocs() -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            return [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM mocs
                    WHERE deleted_at IS NULL
                    ORDER BY updated_at DESC, id ASC
                    """
                )
            ]

    @app.get("/api/stats")
    def api_stats() -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            by_status = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT status, COUNT(*) AS count FROM runs
                    WHERE deleted_at IS NULL
                    GROUP BY status
                    ORDER BY count DESC
                    """
                )
            ]
            by_week = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT strftime('%Y-%W', started_at) AS week, COUNT(*) AS count
                    FROM runs
                    WHERE deleted_at IS NULL
                    GROUP BY week
                    ORDER BY week ASC
                    """
                )
            ]
            return {"by_status": by_status, "by_week": by_week}

    @app.get("/api/mocs/{moc_id}")
    def api_moc(moc_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                "SELECT * FROM mocs WHERE id = ? AND deleted_at IS NULL",
                (moc_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="MOC not found")
            data = row_to_dict(row)
            data["topics"] = _topics(conn, moc_id)
            data["docs"] = _docs(conn, moc_id=moc_id)
            return data

    @app.get("/api/mocs/{moc_id}/topics")
    def api_moc_topics(moc_id: str) -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            _require_moc(conn, moc_id)
            return _topics(conn, moc_id)

    @app.get("/api/topics/{topic_id}/runs")
    def api_topic_runs(topic_id: str) -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            return _runs(conn, topic_id=topic_id, active_run_ids=_active_run_ids(conn))

    @app.get("/api/runs")
    def api_runs(
        moc_id: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            return _runs(
                conn,
                moc_id=moc_id,
                topic_id=topic_id,
                status=status,
                q=q,
                active_run_ids=_active_run_ids(conn),
            )

    @app.get("/api/wandb/cache")
    def api_wandb_cache() -> dict[str, int]:
        return wandb_cache_stats(cache_dir)

    @app.delete("/api/wandb/cache")
    def api_clear_wandb_cache() -> dict[str, int]:
        return clear_wandb_cache(cache_dir)

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                """
                SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                    mocs.title AS moc_title
                FROM runs
                JOIN topics ON topics.id = runs.topic_id
                JOIN mocs ON mocs.id = topics.moc_id
                WHERE runs.id = ? AND runs.deleted_at IS NULL
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Run not found")
            active_run_ids = _active_run_ids(conn)
            data = _render_run_text_fields(row_to_dict(row), active_run_ids)
            data["analysis_html"] = render_markdown(
                str(data.get("analysis") or ""), active_run_ids
            )
            data["artifacts"] = [
                row_to_dict(artifact)
                for artifact in conn.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE run_id = ? AND deleted_at IS NULL
                    ORDER BY created_at DESC, id DESC
                    """,
                    (run_id,),
                )
            ]
            data["relations"] = [
                row_to_dict(rel)
                for rel in conn.execute(
                    """
                    SELECT * FROM relations
                    WHERE src_run_id = ? AND deleted_at IS NULL
                    ORDER BY created_at DESC, id DESC
                    """,
                    (run_id,),
                )
            ]
            data["docs"] = _docs(conn, run_id=run_id)
            return data

    @app.get("/api/runs/{run_id}/wandb")
    def api_run_wandb(run_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                """
                SELECT id, status, metadata_json
                FROM runs
                WHERE id = ? AND deleted_at IS NULL
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Run not found")
            data = row_to_dict(row)
        url = (data.get("metadata") or {}).get("wandb_url")
        if not url:
            return {
                "available": False,
                "reason": "missing_wandb_url",
                "message": "This run does not have metadata.wandb_url.",
            }
        try:
            return fetch_wandb_charts(
                str(url),
                run_id=run_id,
                status=str(data.get("status") or ""),
                cache_dir=cache_dir,
                samples=1000,
            )
        except WandbLiveError as exc:
            return {
                "available": False,
                "reason": exc.reason,
                "message": exc.message,
            }

    @app.get("/api/wandb/compare")
    def api_wandb_compare(
        run_id: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        seen: set[str] = set()
        run_ids = [
            item for item in (run_id or []) if item and not (item in seen or seen.add(item))
        ]
        if not run_ids:
            return {"runs": [], "skipped": [], "errors": []}

        with readonly_transaction(root, state_dir=state_dir) as conn:
            rows = {
                str(row["id"]): row_to_dict(row)
                for row in conn.execute(
                    f"""
                    SELECT id, purpose, status, metadata_json
                    FROM runs
                    WHERE deleted_at IS NULL
                        AND id IN ({",".join("?" for _ in run_ids)})
                    """,
                    run_ids,
                )
            }

        runs: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for item in run_ids:
            row = rows.get(item)
            if row is None:
                errors.append(
                    {
                        "run_id": item,
                        "reason": "run_not_found",
                        "message": "Run not found or deleted.",
                    }
                )
                continue
            url = (row.get("metadata") or {}).get("wandb_url")
            if not url:
                skipped.append(
                    {
                        "run_id": item,
                        "reason": "missing_wandb_url",
                        "message": "This run does not have metadata.wandb_url.",
                    }
                )
                continue
            try:
                data = fetch_wandb_charts(
                    str(url),
                    run_id=item,
                    status=str(row.get("status") or ""),
                    cache_dir=cache_dir,
                    samples=1000,
                )
            except WandbLiveError as exc:
                errors.append(
                    {
                        "run_id": item,
                        "reason": exc.reason,
                        "message": exc.message,
                    }
                )
                continue
            runs.append(
                {
                    "id": item,
                    "purpose": row.get("purpose") or "",
                    "run_path": data["run_path"],
                    "cached": data.get("cached", False),
                    "groups": data["groups"],
                }
            )
        return {"runs": runs, "skipped": skipped, "errors": errors}

    @app.get("/api/docs")
    def api_docs(
        moc_id: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            return _docs(conn, moc_id=moc_id, q=q)

    @app.get("/api/docs/{doc_id}")
    def api_doc(doc_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                """
                SELECT docs.*, mocs.title AS moc_title
                FROM docs JOIN mocs ON mocs.id = docs.moc_id
                WHERE docs.id = ? AND docs.deleted_at IS NULL
                """,
                (doc_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Doc not found")
            active_run_ids = _active_run_ids(conn)
            data = row_to_dict(row)
            data["body_html"] = render_markdown(
                str(data.get("body") or ""), active_run_ids
            )
            data["runs"] = _doc_runs(conn, doc_id, active_run_ids=active_run_ids)
            return data

    return app


def render_markdown(text: str, active_run_ids: set[str] | None = None) -> str:
    escaped = html.escape(text)
    escaped = render_html_run_links(escaped, active_run_ids or set())
    return markdown_lib.markdown(
        escaped,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )


def render_inline_text(text: str, active_run_ids: set[str] | None = None) -> str:
    escaped = html.escape(text or "")
    linked = render_html_run_links(escaped, active_run_ids or set())
    return linked.replace("\n", "<br>")


def _require_moc(conn: sqlite3.Connection, moc_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM mocs WHERE id = ? AND deleted_at IS NULL", (moc_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="MOC not found")


def _topics(conn: sqlite3.Connection, moc_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT topics.*, mocs.title AS moc_title
            FROM topics JOIN mocs ON mocs.id = topics.moc_id
            WHERE topics.moc_id = ?
                AND topics.deleted_at IS NULL
                AND mocs.deleted_at IS NULL
            ORDER BY topics.created_at DESC, topics.title DESC
            """,
            (moc_id,),
        )
    ]


def _runs(
    conn: sqlite3.Connection,
    *,
    moc_id: str | None = None,
    topic_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    active_run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    clauses = ["runs.deleted_at IS NULL", "topics.deleted_at IS NULL"]
    params: list[Any] = []
    if moc_id is not None:
        clauses.append("topics.moc_id = ?")
        params.append(moc_id)
    if topic_id is not None:
        clauses.append("runs.topic_id = ?")
        params.append(topic_id)
    if status is not None:
        clauses.append("runs.status = ?")
        params.append(status)
    if q:
        clauses.append(
            "(runs.id LIKE ? OR runs.purpose LIKE ? OR runs.result LIKE ? "
            "OR runs.analysis LIKE ? OR runs.metadata_json LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    where = " AND ".join(clauses)
    active_run_ids = active_run_ids or set()
    return [
        _render_run_text_fields(row_to_dict(row), active_run_ids)
        for row in conn.execute(
            f"""
            SELECT runs.*, topics.title AS topic_title, topics.moc_id,
                mocs.title AS moc_title
            FROM runs
            JOIN topics ON topics.id = runs.topic_id
            JOIN mocs ON mocs.id = topics.moc_id
            WHERE {where}
            ORDER BY runs.started_at DESC, runs.id DESC
            LIMIT 500
            """,
            params,
        )
    ]


def _docs(
    conn: sqlite3.Connection,
    *,
    moc_id: str | None = None,
    run_id: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["docs.deleted_at IS NULL", "mocs.deleted_at IS NULL"]
    params: list[Any] = []
    joins = "JOIN mocs ON mocs.id = docs.moc_id"
    if moc_id is not None:
        clauses.append("docs.moc_id = ?")
        params.append(moc_id)
    if run_id is not None:
        joins += " JOIN doc_runs ON doc_runs.doc_id = docs.id"
        clauses.append("doc_runs.run_id = ?")
        clauses.append("doc_runs.deleted_at IS NULL")
        params.append(run_id)
    if q:
        clauses.append("(docs.id LIKE ? OR docs.title LIKE ? OR docs.body LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where = " AND ".join(clauses)
    return [
        row_to_dict(row)
        for row in conn.execute(
            f"""
            SELECT docs.*, mocs.title AS moc_title
            FROM docs {joins}
            WHERE {where}
            ORDER BY docs.updated_at DESC, docs.id DESC
            LIMIT 500
            """,
            params,
        )
    ]


def _doc_runs(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    active_run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    active_run_ids = active_run_ids or set()
    return [
        _render_doc_run_text_fields(row_to_dict(row), active_run_ids)
        for row in conn.execute(
            """
            SELECT doc_runs.*, runs.status, runs.purpose, runs.relation, runs.result,
                topics.title AS topic_title, topics.moc_id
            FROM doc_runs
            JOIN runs ON runs.id = doc_runs.run_id
            JOIN topics ON topics.id = runs.topic_id
            WHERE doc_runs.doc_id = ?
                AND doc_runs.deleted_at IS NULL
                AND runs.deleted_at IS NULL
            ORDER BY doc_runs.position ASC, doc_runs.created_at ASC
            """,
            (doc_id,),
        )
    ]


def _active_run_ids(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["id"])
        for row in conn.execute("SELECT id FROM runs WHERE deleted_at IS NULL")
    }


def _render_run_text_fields(
    row: dict[str, Any], active_run_ids: set[str]
) -> dict[str, Any]:
    for field in ("purpose", "relation", "result"):
        row[f"{field}_html"] = render_inline_text(str(row.get(field) or ""), active_run_ids)
    return row


def _render_doc_run_text_fields(
    row: dict[str, Any], active_run_ids: set[str]
) -> dict[str, Any]:
    for field in ("role", "note", "purpose", "result"):
        row[f"{field}_html"] = render_inline_text(str(row.get(field) or ""), active_run_ids)
    return row


_INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>expnote</title>
  <script src="/assets/plotly.min.js"></script>
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
  <style>
    :root {
      color-scheme: light;
      --ink: #111827;
      --muted: #5f6b7a;
      --subtle: #8993a1;
      --line: rgba(31, 41, 55, 0.12);
      --line-strong: rgba(31, 41, 55, 0.20);
      --panel: rgba(255, 255, 255, 0.78);
      --panel-strong: rgba(255, 255, 255, 0.96);
      --accent: #2f5bd8;
      --accent-strong: #2447ad;
      --accent-soft: #edf3ff;
      --teal: #0f9f8f;
      --green: #067647;
      --amber: #b54708;
      --red: #b42318;
      --shadow: 0 18px 50px rgba(17, 24, 39, 0.10);
      --shadow-soft: 0 8px 24px rgba(17, 24, 39, 0.07);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 16% 4%, rgba(47, 91, 216, 0.10), transparent 28%),
        radial-gradient(circle at 86% 10%, rgba(15, 159, 143, 0.09), transparent 28%),
        linear-gradient(135deg, #f7f9fc 0%, #f3f6fb 48%, #f8faf7 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(17, 24, 39, 0.026) 1px, transparent 1px), linear-gradient(90deg, rgba(17, 24, 39, 0.022) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,0.40), transparent 70%);
    }
    button, input, select { font: inherit; }
    button {
      border: 0;
      color: inherit;
      background: transparent;
      cursor: pointer;
      text-align: left;
    }
    .shell {
      display: grid;
      grid-template-columns: 292px minmax(0, 1fr);
      min-height: 100vh;
      position: relative;
      z-index: 1;
    }
    aside {
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 18px 16px;
      border-right: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.66);
      backdrop-filter: blur(18px);
      overflow: auto;
    }
    main { min-width: 0; padding: 18px 24px 36px; }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 20px;
    }
    .logo {
      width: 36px;
      height: 36px;
      border-radius: 9px;
      background: linear-gradient(135deg, var(--accent), var(--teal) 62%, #f2b84b);
      box-shadow: 0 12px 26px rgba(47, 91, 216, 0.25);
      position: relative;
    }
    .logo::after {
      content: "";
      position: absolute;
      inset: 9px;
      border: 1px solid rgba(255,255,255,0.72);
      border-radius: 5px;
    }
    .brand strong { display: block; font-size: 16px; letter-spacing: 0; }
    .brand span { color: var(--muted); font-size: 12px; }
    .nav-section { margin-top: 18px; }
    .label {
      color: var(--subtle);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin: 0 0 8px;
    }
    .nav-item {
      width: 100%;
      border-radius: 8px;
      padding: 9px 10px;
      margin: 2px 0;
      color: #344054;
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      transition: background 140ms ease, color 140ms ease, transform 140ms ease;
    }
    .nav-item:hover, .nav-item.active {
      background: rgba(47, 91, 216, 0.10);
      color: var(--accent);
    }
    .nav-item:hover { transform: translateX(1px); }
    .nav-item i, .top-button i { width: 16px; height: 16px; stroke-width: 2.2; }
    .nav-text { min-width: 0; }
    .nav-item code {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
      background: transparent;
      padding: 0;
    }
    .toolbar {
      position: sticky;
      top: 18px;
      z-index: 2;
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
      padding: 9px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      backdrop-filter: blur(18px);
      box-shadow: var(--shadow-soft);
    }
    .toolbar-group {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px;
      border: 1px solid rgba(31, 41, 55, 0.08);
      border-radius: 9px;
      background: rgba(255, 255, 255, 0.62);
    }
    .top-button {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 7px;
      padding: 7px 9px;
      font-weight: 600;
      color: #344054;
    }
    .top-button:hover, .top-button.active { background: var(--accent-soft); color: var(--accent); }
    [hidden] { display: none !important; }
    input, select {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 10px;
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      outline: none;
    }
    input:focus, select:focus { border-color: rgba(47, 91, 216, 0.55); box-shadow: 0 0 0 3px rgba(47, 91, 216, 0.10); }
    #search { min-width: 260px; margin-left: auto; }
    .hero {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 22px 24px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.94), rgba(255,255,255,0.74)),
        radial-gradient(circle at 100% 0%, rgba(47,91,216,0.14), transparent 32%);
      box-shadow: var(--shadow);
    }
    h1 { margin: 0; font-size: 28px; line-height: 1.18; letter-spacing: 0; }
    h2 { margin: 24px 0 10px; font-size: 16px; letter-spacing: 0; }
    h3 { margin: 0 0 8px; font-size: 14px; letter-spacing: 0; }
    p { margin: 0; }
    .muted { color: var(--muted); }
    .subtle { color: var(--subtle); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 14px;
      background: var(--panel-strong);
      box-shadow: var(--shadow-soft);
    }
    .card-button { width: 100%; transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease; }
    .card-button:hover { transform: translateY(-1px); border-color: rgba(47, 91, 216, 0.42); box-shadow: 0 14px 30px rgba(17, 24, 39, 0.09); }
    .stats { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
    .stat { min-width: 118px; border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: rgba(255,255,255,0.78); }
    .stat strong { display: block; font-size: 20px; }
    .stat-bars { margin-top: 16px; display: flex; flex-direction: column; gap: 6px; max-width: 480px; }
    .stat-bar { display: grid; grid-template-columns: 64px 1fr 28px; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
    .stat-bar-track { background: var(--line); border-radius: 6px; height: 10px; overflow: hidden; }
    .stat-bar-fill { display: block; height: 100%; background: var(--accent); border-radius: 6px; }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-strong);
      box-shadow: var(--shadow-soft);
    }
    table { width: 100%; border-collapse: separate; border-spacing: 0; table-layout: auto; }
    table[data-table="topic-runs"], table[data-table="doc-runs"] { min-width: 1080px; }
    table[data-table="docs"] { min-width: 720px; }
    col.col-run { width: 1%; }
    col.col-status { width: 1%; }
    col.col-role { width: 1%; }
    col.col-updated { width: 170px; }
    col.col-moc { width: 220px; }
    col.col-purpose { width: 28%; }
    col.col-relation { width: 34%; }
    col.col-result { width: 30%; }
    col.col-note { width: 22%; }
    col.col-doc { width: auto; }
    th, td { border-bottom: 1px solid rgba(31, 41, 55, 0.10); padding: 11px 12px; text-align: left; vertical-align: top; }
    th {
      position: sticky;
      top: 0;
      z-index: 1;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: rgba(248, 250, 252, 0.96);
      box-shadow: inset 0 -1px 0 rgba(31, 41, 55, 0.08);
    }
    td { overflow-wrap: anywhere; }
    td[data-cell="run"], td[data-cell="status"], td[data-cell="role"], td[data-cell="updated"] { white-space: nowrap; }
    td[data-cell="purpose"], td[data-cell="relation"], td[data-cell="result"], td[data-cell="note"] { line-height: 1.45; }
    tbody tr:hover { background: rgba(238, 244, 255, 0.62); }
    tbody tr:last-child td { border-bottom: 0; }
    .link-button { color: var(--accent); font-weight: 700; display: inline-flex; align-items: center; gap: 5px; }
    .link-button code, .badge { white-space: nowrap; }
    code {
      border: 1px solid rgba(102, 112, 133, 0.18);
      border-radius: 6px;
      background: #f2f4f7;
      padding: 1px 5px;
      color: #344054;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid rgba(102, 112, 133, 0.18);
      background: #f2f4f7;
      color: #344054;
    }
    .badge::before {
      content: "";
      width: 6px;
      height: 6px;
      margin-right: 6px;
      border-radius: 999px;
      background: currentColor;
      opacity: 0.74;
    }
    .status-running { color: var(--amber); background: #fff7ed; border-color: rgba(181, 71, 8, 0.20); }
    .status-finished { color: var(--green); background: #ecfdf3; border-color: rgba(6, 118, 71, 0.20); }
    .status-failed { color: var(--red); background: #fef3f2; border-color: rgba(180, 35, 24, 0.20); }
    .pill { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 7px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: #fff; }
    .run-sections { display: grid; gap: 12px; margin-top: 16px; }
    .markdown {
      max-width: 1040px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px 20px;
      background: var(--panel-strong);
      box-shadow: var(--shadow-soft);
    }
    .markdown p { margin: 0 0 12px; }
    .markdown h1, .markdown h2, .markdown h3 { margin: 18px 0 10px; line-height: 1.25; }
    .markdown pre { overflow: auto; background: #111827; color: #f9fafb; padding: 13px 14px; border-radius: 8px; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.07); }
    .markdown blockquote { margin: 12px 0; padding: 8px 12px; border-left: 3px solid var(--accent); background: rgba(237, 243, 255, 0.64); color: #344054; }
    .markdown table { min-width: 0; table-layout: auto; }
    .metadata-list { margin: 0; padding-left: 18px; }
    .metadata-list li { margin: 4px 0; }
    .wandb-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
    .wandb-button {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border-radius: 8px;
      padding: 8px 11px;
      font-weight: 700;
      color: #fff;
      background: linear-gradient(135deg, var(--accent), var(--teal));
      box-shadow: 0 10px 22px rgba(47, 91, 216, 0.18);
    }
    .wandb-button:disabled { opacity: 0.64; cursor: wait; }
    .wandb-status { color: var(--muted); }
    .wandb-group { margin-top: 16px; }
    .wandb-chart-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 12px;
    }
    .wandb-chart-card {
      border: 1px solid rgba(31, 41, 55, 0.10);
      border-radius: 10px;
      background: #fff;
      overflow: hidden;
    }
    .wandb-chart-title {
      padding: 10px 12px 0;
      color: #344054;
      font-size: 12px;
      font-weight: 800;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .wandb-chart {
      min-height: 360px;
      border: 1px solid rgba(31, 41, 55, 0.10);
      border-radius: 10px;
      background: #fff;
    }
    .wandb-chart-grid .wandb-chart {
      min-height: 280px;
      border: 0;
      border-radius: 0;
    }
    .compare-panel { margin-top: 18px; }
    .compare-summary { margin: 0 0 10px; color: var(--muted); }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: rgba(17, 24, 39, 0.34);
      backdrop-filter: blur(8px);
    }
    .modal {
      width: min(760px, 100%);
      max-height: min(720px, calc(100vh - 36px));
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-strong);
      box-shadow: 0 24px 70px rgba(17, 24, 39, 0.24);
      overflow: hidden;
    }
    .modal-header, .modal-footer { padding: 14px 16px; }
    .modal-header { border-bottom: 1px solid var(--line); }
    .modal-footer { border-top: 1px solid var(--line); }
    .modal-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .modal-title h3 { margin: 0; font-size: 16px; }
    .modal-body { overflow: auto; padding: 8px 16px; }
    .compare-row {
      display: grid;
      grid-template-columns: 28px minmax(150px, 0.34fr) minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      padding: 10px 0;
      border-bottom: 1px solid rgba(31, 41, 55, 0.08);
    }
    .compare-row:last-child { border-bottom: 0; }
    .compare-row input { width: 16px; height: 16px; margin-top: 3px; }
    .compare-run { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
    .compare-purpose { color: var(--muted); overflow-wrap: anywhere; }
    .star { color: #d97706; font-weight: 900; }
    a { color: var(--accent); font-weight: 700; text-decoration: none; text-underline-offset: 3px; }
    a:hover { color: var(--accent-strong); text-decoration: underline; }
    .empty {
      border: 1px dashed rgba(102, 112, 133, 0.35);
      border-radius: 10px;
      padding: 18px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.56);
    }
    .error { color: var(--red); }
    @media (prefers-reduced-motion: no-preference) {
      .top-button, .link-button, a { transition: color 140ms ease, background 140ms ease, transform 140ms ease; }
    }
    @media (max-width: 860px) {
      .shell { grid-template-columns: 1fr; }
      aside { position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      main { padding: 12px; }
      .toolbar { top: 8px; flex-wrap: wrap; }
      #search { width: 100%; min-width: 0; margin-left: 0; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <div class="logo"></div>
        <div><strong>expnote</strong><span>SQL-backed research notes</span></div>
      </div>
      <div class="nav-section">
        <p class="label">Workspace</p>
        <button class="nav-item" data-route="#/" onclick="navigate('#/')"><i data-lucide="layout-dashboard"></i><span class="nav-text">MOC overview</span></button>
        <button class="nav-item" data-route="#/runs" onclick="navigate('#/runs')"><i data-lucide="activity"></i><span class="nav-text">All runs</span></button>
        <button class="nav-item" data-route="#/docs" onclick="navigate('#/docs')"><i data-lucide="file-text"></i><span class="nav-text">All docs</span></button>
      </div>
      <div class="nav-section">
        <p class="label">SQL MOCs</p>
        <div id="mocNav"></div>
      </div>
    </aside>
    <main>
      <div class="toolbar">
        <div class="toolbar-group">
          <button id="backBtn" class="top-button" type="button"><i data-lucide="arrow-left"></i>Back</button>
        </div>
        <div class="toolbar-group">
          <button class="top-button" data-route="#/" onclick="navigate('#/')"><i data-lucide="library"></i>MOCs</button>
          <button class="top-button" data-route="#/runs" onclick="navigate('#/runs')"><i data-lucide="list-filter"></i>Runs</button>
          <button class="top-button" data-route="#/docs" onclick="navigate('#/docs')"><i data-lucide="book-open"></i>Docs</button>
        </div>
        <input id="search" placeholder="Search runs or docs">
        <select id="status"><option value="">Any status</option><option>running</option><option>finished</option><option>failed</option></select>
      </div>
      <section id="view"></section>
    </main>
  </div>
  <script>
    const state = { mocs: [], selectedMoc: null, currentRun: null, wandbChartMode: 'combined', wandbChartData: null, wandbCompareRuns: [], wandbCompareData: null, wandbCompareMode: 'intersection' };
    const $ = (id) => document.getElementById(id);
    const route = () => window.location.hash || '#/';

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function idForRun(row) { return row.id || row.run_id; }
    function statusBadge(status) {
      const value = esc(status || 'unknown');
      return `<span class="badge status-${value.toLowerCase()}">${value}</span>`;
    }
    function metadata(meta) {
      const entries = Object.entries(meta || {});
      if (!entries.length) return '<span class="muted">No metadata</span>';
      return `<ul class="metadata-list">${entries.map(([k,v]) => `<li><code>${esc(k)}</code>: ${metadataValue(k, v)}</li>`).join('')}</ul>`;
    }
    function metadataValue(key, value) {
      if (key === 'wandb_url' && value) {
        const href = esc(String(value));
        return `<a href="${href}" target="_blank" rel="noreferrer">${href}</a>`;
      }
      if (typeof value === 'string') return esc(value);
      return esc(JSON.stringify(value));
    }
    function hasWandbUrl(meta) {
      return Boolean(meta && meta.wandb_url);
    }
    function formatBytes(bytes) {
      const value = Number(bytes || 0);
      if (value < 1024) return `${value} B`;
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }
    function wandbPanel(r) {
      if (!hasWandbUrl(r.metadata)) return '';
      const id = esc(r.id);
      return `<section class="markdown" id="wandb-panel"><h2>W&B Charts</h2><div class="wandb-actions"><button id="wandbFetch" class="wandb-button" type="button" data-run-id="${id}" onclick="loadWandbCharts(this.dataset.runId)"><i data-lucide="line-chart"></i>Fetch live W&B charts</button><button id="wandbModeToggle" class="top-button" type="button" onclick="toggleWandbChartMode()" disabled>Split metrics</button><button id="wandbCompare" class="top-button" type="button" onclick="openWandbCompareModal()" hidden disabled>Compare runs</button><span id="wandbStatus" class="wandb-status">Uses metadata.wandb_url and does not write to SQL.</span></div><div id="wandbCharts"></div><div id="wandbCompareResults"></div></section>`;
    }
    function reviveWandbPanel() {
      if (!state.wandbChartData || !hasWandbUrl(state.currentRun && state.currentRun.metadata)) return;
      updateWandbModeToggle();
      updateWandbCompareButton();
      const data = state.wandbChartData;
      $('wandbStatus').textContent = `${data.run_path}: ${data.groups.length} metric groups from ${data.samples} sampled history rows (${data.cached ? 'cached' : 'live'}).`;
      if (state.wandbCompareData) {
        renderWandbComparison();
      } else {
        renderWandbCharts(data);
      }
    }
    async function loadWandbCharts(runId) {
      const button = $('wandbFetch');
      const status = $('wandbStatus');
      const target = $('wandbCharts');
      if (!window.Plotly) {
        status.innerHTML = '<span class="error">Plotly is not available from the expnote server.</span>';
        return;
      }
      button.disabled = true;
      state.wandbChartData = null;
      state.wandbCompareData = null;
      updateWandbModeToggle();
      updateWandbCompareButton();
      status.textContent = 'Fetching live W&B history...';
      target.innerHTML = '';
      $('wandbCompareResults').innerHTML = '';
      try {
        const data = await api('/api/runs/' + encodeURIComponent(runId) + '/wandb');
        if (!data.available) {
          status.innerHTML = `<span class="error">${esc(data.reason)}: ${esc(data.message)}</span>`;
          return;
        }
        state.wandbChartData = data;
        renderWandbCharts(data);
        updateWandbModeToggle();
        updateWandbCompareButton();
        status.textContent = `${data.run_path}: ${data.groups.length} metric groups from ${data.samples} sampled history rows (${data.cached ? 'cached' : 'live'}).`;
      } catch (err) {
        status.innerHTML = `<span class="error">${esc(err.message || err)}</span>`;
      } finally {
        button.disabled = false;
      }
    }
    function toggleWandbChartMode() {
      if (!state.wandbChartData) return;
      state.wandbChartMode = state.wandbChartMode === 'combined' ? 'split' : 'combined';
      state.wandbCompareData = null;
      $('wandbCompareResults').innerHTML = '';
      renderWandbCharts(state.wandbChartData);
      updateWandbModeToggle();
      updateWandbCompareButton();
    }
    function updateWandbModeToggle() {
      const button = $('wandbModeToggle');
      if (!button) return;
      button.disabled = !state.wandbChartData;
      button.textContent = state.wandbChartMode === 'combined' ? 'Split metrics' : 'Combine group';
    }
    function updateWandbCompareButton() {
      const button = $('wandbCompare');
      if (!button) return;
      const visible = Boolean(state.wandbChartData && state.wandbChartMode === 'split');
      button.hidden = !visible;
      button.disabled = !visible;
    }
    function renderWandbCharts(data) {
      const target = $('wandbCharts');
      const groups = data.groups || [];
      if (!groups.length) {
        target.innerHTML = empty('No numeric non-system W&B metrics were found.');
        return;
      }
      if (state.wandbChartMode === 'split') {
        renderSplitWandbCharts(target, groups);
        return;
      }
      renderCombinedWandbCharts(target, groups);
    }
    function wandbTrace(chart) {
      return {
          x: chart.x || [],
          y: chart.y || [],
          mode: 'lines',
          type: 'scatter',
          name: chart.metric,
          hovertemplate: '%{x}<br>%{y}<extra>%{fullData.name}</extra>'
      };
    }
    function wandbLayout(title, showLegend) {
      return {
          margin: { l: 54, r: 18, t: 12, b: 42 },
          title: title ? { text: title, font: { size: 13 } } : undefined,
          xaxis: { title: '_step', gridcolor: 'rgba(31,41,55,0.10)', zeroline: false },
          yaxis: { gridcolor: 'rgba(31,41,55,0.10)', zeroline: false },
          showlegend: showLegend,
          legend: { orientation: 'h', y: -0.22 },
          paper_bgcolor: '#fff',
          plot_bgcolor: '#fff'
      };
    }
    function wandbPlotOptions() {
      return {
          responsive: true,
          displaylogo: false
      };
    }
    function renderCombinedWandbCharts(target, groups) {
      target.innerHTML = groups.map((group, groupIndex) => `<div class="wandb-group"><h3>${esc(group.name)}</h3><div class="wandb-chart" id="wandb-chart-combined-${groupIndex}"></div></div>`).join('');
      groups.forEach((group, groupIndex) => {
        const traces = (group.charts || []).map(chart => wandbTrace(chart));
        Plotly.newPlot(`wandb-chart-combined-${groupIndex}`, traces, wandbLayout('', true), wandbPlotOptions());
      });
    }
    function renderSplitWandbCharts(target, groups) {
      target.innerHTML = groups.map((group, groupIndex) => `<div class="wandb-group"><h3>${esc(group.name)}</h3><div class="wandb-chart-grid">${(group.charts || []).map((chart, chartIndex) => `<div class="wandb-chart-card"><div class="wandb-chart-title">${esc(chart.metric)}</div><div class="wandb-chart" id="wandb-chart-split-${groupIndex}-${chartIndex}"></div></div>`).join('')}</div></div>`).join('');
      groups.forEach((group, groupIndex) => {
        (group.charts || []).forEach((chart, chartIndex) => {
          Plotly.newPlot(`wandb-chart-split-${groupIndex}-${chartIndex}`, [wandbTrace(chart)], wandbLayout('', false), wandbPlotOptions());
        });
      });
    }
    function relationRecommendedRunIds(run) {
      const text = String((run && run.relation) || '');
      const ids = new Set();
      for (const candidate of state.wandbCompareRuns) {
        const id = String(candidate.id || '');
        if (!id || id === run.id) continue;
        if (text.includes(`[[${id}]]`) || new RegExp(`(^|[^A-Za-z0-9_-])${escapeRegExp(id)}([^A-Za-z0-9_-]|$)`).test(text)) {
          ids.add(id);
        }
      }
      return ids;
    }
    function escapeRegExp(value) {
      const slash = String.fromCharCode(92);
      const specials = new Set(['.', '*', '+', '?', '^', '$', '{', '}', '(', ')', '|', '[', ']', slash]);
      return String(value).split('').map(char => specials.has(char) ? slash + char : char).join('');
    }
    async function openWandbCompareModal() {
      if (!(state.currentRun && state.wandbChartData && state.wandbChartMode === 'split')) return;
      state.wandbCompareRuns = await api('/api/runs?moc_id=' + encodeURIComponent(state.currentRun.moc_id));
      const recommended = relationRecommendedRunIds(state.currentRun);
      const rows = state.wandbCompareRuns.map(run => {
        const id = idForRun(run);
        const checked = id === state.currentRun.id ? 'checked' : '';
        const current = id === state.currentRun.id ? '<span class="pill">Current</span>' : '';
        const star = recommended.has(id) ? '<span class="star" title="relation recommended">*</span>' : '';
        return `<label class="compare-row"><input type="checkbox" value="${esc(id)}" ${checked}><div class="compare-run"><code>${esc(id)}</code>${current}${star}</div><div class="compare-purpose">${esc(run.purpose || 'No purpose recorded.')}</div></label>`;
      }).join('');
      document.body.insertAdjacentHTML('beforeend', `<div class="modal-backdrop" id="wandbCompareModal"><div class="modal"><div class="modal-header"><div class="modal-title"><h3>Compare runs</h3><button class="top-button" type="button" onclick="closeWandbCompareModal()">Close</button></div></div><div class="modal-body">${rows || empty('No runs in this MOC.')}</div><div class="modal-footer"><p class="subtle">* 星标为relation中记录的推荐对比实验</p><div class="wandb-actions"><button class="wandb-button" type="button" onclick="loadWandbComparison()">Show comparison</button></div></div></div></div>`);
    }
    function closeWandbCompareModal() {
      const modal = $('wandbCompareModal');
      if (modal) modal.remove();
    }
    async function loadWandbComparison() {
      const modal = $('wandbCompareModal');
      if (!modal) return;
      const selected = Array.from(modal.querySelectorAll('input[type="checkbox"]:checked')).map(input => input.value);
      closeWandbCompareModal();
      const target = $('wandbCompareResults');
      if (!selected.length) {
        target.innerHTML = `<div class="compare-panel">${empty('No comparison runs selected.')}</div>`;
        return;
      }
      target.innerHTML = '<div class="compare-panel"><p class="compare-summary">Fetching comparison W&B histories...</p></div>';
      $('wandbCharts').innerHTML = '';
      const query = selected.map(id => 'run_id=' + encodeURIComponent(id)).join('&');
      const data = await api('/api/wandb/compare?' + query);
      state.wandbCompareData = data;
      state.wandbCompareMode = 'intersection';
      renderWandbComparison();
    }
    function toggleWandbCompareMetricMode() {
      if (!state.wandbCompareData) return;
      state.wandbCompareMode = state.wandbCompareMode === 'intersection' ? 'union' : 'intersection';
      renderWandbComparison();
    }
    function closeWandbComparison() {
      state.wandbCompareData = null;
      $('wandbCompareResults').innerHTML = '';
      if (state.wandbChartData) {
        renderWandbCharts(state.wandbChartData);
      }
      updateWandbModeToggle();
      updateWandbCompareButton();
    }
    function groupComparisonMetrics(metrics) {
      const byGroup = new Map();
      metrics.forEach((metric, index) => {
        const key = metric.group;
        if (!byGroup.has(key)) byGroup.set(key, []);
        byGroup.get(key).push({ ...metric, index });
      });
      return Array.from(byGroup.keys()).sort().map(name => ({ name, items: byGroup.get(name) }));
    }
    function renderWandbComparison() {
      const target = $('wandbCompareResults');
      const data = state.wandbCompareData || { runs: [], skipped: [], errors: [] };
      const runs = data.runs || [];
      if (!runs.length) {
        target.innerHTML = `<div class="compare-panel">${empty('No selected runs with usable W&B charts.')}${compareMessages(data)}</div>`;
        return;
      }
      const metrics = comparisonMetrics(runs, state.wandbCompareMode);
      const metricGroups = groupComparisonMetrics(metrics);
      const toggleLabel = state.wandbCompareMode === 'intersection' ? 'Show all metrics' : 'Show common metrics';
      target.innerHTML = `<div class="compare-panel"><div class="wandb-actions"><button class="top-button" type="button" onclick="closeWandbComparison()">Close comparison</button><button class="top-button" type="button" onclick="toggleWandbCompareMetricMode()">${toggleLabel}</button><span class="compare-summary">${runs.length} runs, ${metrics.length} ${state.wandbCompareMode === 'intersection' ? 'common' : 'total'} metrics.</span></div>${compareMessages(data)}${metricGroups.map(group => `<div class="wandb-group"><h3>${esc(group.name)}</h3><div class="wandb-chart-grid">${group.items.map(metric => `<div class="wandb-chart-card"><div class="wandb-chart-title">${esc(metric.name)}</div><div class="wandb-chart" id="wandb-compare-chart-${metric.index}"></div></div>`).join('')}</div></div>`).join('') || empty('No metrics match the current comparison mode.')}</div>`;
      metrics.forEach((metric, index) => {
        const traces = runs.flatMap(run => {
          const chart = findRunChart(run, metric.name);
          if (!chart) return [];
          const trace = wandbTrace(chart);
          trace.name = run.id;
          trace.line = { color: colorForRun(run.id), width: 2 };
          return [trace];
        });
        Plotly.newPlot(`wandb-compare-chart-${index}`, traces, wandbLayout('', true), wandbPlotOptions());
      });
    }
    function compareMessages(data) {
      const skipped = (data.skipped || []).map(item => `<span class="pill">${esc(item.run_id)}: ${esc(item.reason)}</span>`).join('');
      const errors = (data.errors || []).map(item => `<span class="pill error">${esc(item.run_id)}: ${esc(item.reason)}</span>`).join('');
      if (!skipped && !errors) return '';
      return `<p class="compare-summary">${skipped}${errors}</p>`;
    }
    function comparisonMetrics(runs, mode) {
      const counts = new Map();
      const groups = new Map();
      runs.forEach(run => {
        const names = new Set();
        (run.groups || []).forEach(group => (group.charts || []).forEach(chart => {
          names.add(chart.metric);
          groups.set(chart.metric, group.name);
        }));
        names.forEach(name => counts.set(name, (counts.get(name) || 0) + 1));
      });
      return Array.from(counts.keys()).filter(name => mode === 'union' || counts.get(name) === runs.length).sort().map(name => ({ name, group: groups.get(name) || 'metrics' }));
    }
    function findRunChart(run, metricName) {
      for (const group of run.groups || []) {
        for (const chart of group.charts || []) {
          if (chart.metric === metricName) return chart;
        }
      }
      return null;
    }
    function colorForRun(runId) {
      const colors = ['#2f5bd8', '#0f9f8f', '#b54708', '#b42318', '#7c3aed', '#0284c7', '#16a34a', '#db2777', '#ca8a04', '#475569'];
      let hash = 0;
      for (const char of String(runId)) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
      return colors[Math.abs(hash) % colors.length];
    }
    function empty(label) {
      return `<div class="empty">${esc(label)}</div>`;
    }
    function navigate(hash) {
      if (route() === hash) {
        renderRoute();
        return;
      }
      window.location.hash = hash;
    }
    async function ensureMocs() {
      if (!state.mocs.length) state.mocs = await api('/api/mocs');
      renderNav();
    }
    function setActive() {
      const current = route();
      document.querySelectorAll('[data-route]').forEach(el => {
        el.classList.toggle('active', el.getAttribute('data-route') === current);
      });
    }
    function renderNav() {
      $('mocNav').innerHTML = state.mocs.map(m => `<button class="nav-item" data-route="#/moc/${encodeURIComponent(m.id)}" onclick="navigate('#/moc/${encodeURIComponent(m.id)}')"><i data-lucide="database"></i><span class="nav-text">${esc(m.title)}<code>${esc(m.id)}</code></span></button>`).join('') || empty('No MOCs yet');
      setActive();
      refreshIcons();
    }
    function hero(title, subtitle, stats = '') {
      return `<div class="hero"><h1>${title}</h1><p class="muted">${subtitle}</p>${stats}</div>`;
    }
    async function loadHome() {
      await ensureMocs();
      const cache = await api('/api/wandb/cache');
      const runStats = await api('/api/stats');
      const statusStats = runStats.by_status.map(s => `<div class="stat"><span class="muted">${esc(s.status || 'unknown')}</span><strong>${s.count}</strong></div>`).join('');
      const stats = `<div class="stats"><div class="stat"><span class="muted">MOCs</span><strong>${state.mocs.length}</strong></div>${statusStats}<div class="stat"><span class="muted">W&B cache</span><strong id="wandbCacheSize">${formatBytes(cache.bytes)}</strong><button class="top-button" type="button" onclick="clearWandbCache()">Clear W&B cache</button></div></div>`;
      $('view').innerHTML = `${hero('Experiment knowledge base', 'Browse SQL MOCs, topics, runs, and analysis documents from one read-only dashboard.', stats)}${weeklyRunBars(runStats.by_week)}<h2>MOCs</h2><div class="grid">${state.mocs.map(m => `<button class="card card-button" onclick="navigate('#/moc/${encodeURIComponent(m.id)}')"><h3>${esc(m.title)}</h3><code>${esc(m.id)}</code><p class="muted">${esc(m.summary || 'No summary recorded.')}</p></button>`).join('')}</div>`;
    }
    function weeklyRunBars(byWeek) {
      if (!byWeek || !byWeek.length) return '';
      const maxCount = Math.max(...byWeek.map(w => w.count));
      const rows = byWeek.map(w => `<div class="stat-bar"><span>${esc(w.week)}</span><span class="stat-bar-track"><span class="stat-bar-fill" style="width:${maxCount ? Math.round((w.count / maxCount) * 100) : 0}%"></span></span><span>${w.count}</span></div>`).join('');
      return `<h2>New runs per week</h2><div class="stat-bars">${rows}</div>`;
    }
    async function clearWandbCache() {
      const cache = await api('/api/wandb/cache', { method: 'DELETE' });
      const target = $('wandbCacheSize');
      if (target) target.textContent = formatBytes(cache.bytes);
    }
    async function loadMoc(id) {
      await ensureMocs();
      state.selectedMoc = id;
      const moc = await api('/api/mocs/' + encodeURIComponent(id));
      const stats = `<div class="stats"><div class="stat"><span class="muted">Topics</span><strong>${moc.topics.length}</strong></div><div class="stat"><span class="muted">Docs</span><strong>${moc.docs.length}</strong></div></div>`;
      $('view').innerHTML = `${hero(esc(moc.title), `<code>${esc(moc.id)}</code> ${esc(moc.summary || '')}`, stats)}<h2>Topics</h2><div class="grid">${moc.topics.map(t => `<button class="card card-button" onclick="navigate('#/topic/${encodeURIComponent(t.id)}')"><h3>${esc(t.title)}</h3><p class="muted">${esc(t.summary || 'No summary recorded.')}</p></button>`).join('') || empty('No topics in this MOC.')}</div><h2>Docs</h2>${docTable(moc.docs)}`;
    }
    async function loadTopic(id) {
      await ensureMocs();
      const runs = await api('/api/topics/' + encodeURIComponent(id) + '/runs');
      const title = runs[0] ? esc(runs[0].topic_title) : 'Topic';
      const subtitle = runs[0] ? `${esc(runs[0].moc_title)} / <code>${esc(runs[0].moc_id)}</code>` : 'No runs registered for this topic yet.';
      $('view').innerHTML = `${hero(title, subtitle, `<div class="stats"><div class="stat"><span class="muted">Runs</span><strong>${runs.length}</strong></div></div>`)}<h2>Runs</h2>${topicRunTable(runs)}`;
    }
    async function loadRuns() {
      await ensureMocs();
      const p = new URLSearchParams();
      if ($('search').value) p.set('q', $('search').value);
      if ($('status').value) p.set('status', $('status').value);
      const runs = await api('/api/runs?' + p.toString());
      $('view').innerHTML = `${hero('All runs', 'Search and filter every active SQL-backed training record.', `<div class="stats"><div class="stat"><span class="muted">Rows</span><strong>${runs.length}</strong></div></div>`)}${topicRunTable(runs)}`;
    }
    async function loadDocs() {
      await ensureMocs();
      const p = new URLSearchParams();
      if ($('search').value) p.set('q', $('search').value);
      const docs = await api('/api/docs?' + p.toString());
      $('view').innerHTML = `${hero('All docs', 'Read MOC-level analysis documents and jump to related runs.', `<div class="stats"><div class="stat"><span class="muted">Docs</span><strong>${docs.length}</strong></div></div>`)}${docTable(docs)}`;
    }
    function topicRunTable(rows) {
      if (!rows.length) return empty('No runs to display.');
      const cols = '<colgroup><col class="col-run"><col class="col-status"><col class="col-purpose"><col class="col-relation"><col class="col-result"></colgroup>';
      return `<div class="table-wrap"><table data-table="topic-runs">${cols}<thead><tr><th>run</th><th>status</th><th>purpose</th><th>relation</th><th>result</th></tr></thead><tbody>${rows.map(r => `<tr><td data-cell="run"><button class="link-button" onclick="navigate('#/run/${encodeURIComponent(idForRun(r))}')"><code>${esc(idForRun(r))}</code></button></td><td data-cell="status">${statusBadge(r.status)}</td><td data-cell="purpose">${r.purpose_html || esc(r.purpose)}</td><td data-cell="relation">${r.relation_html || esc(r.relation)}</td><td data-cell="result">${r.result_html || esc(r.result)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function docRunTable(rows) {
      if (!rows.length) return empty('No related runs linked to this document.');
      const showRole = rows.some(r => String(r.role || '').trim());
      const showNote = rows.some(r => String(r.note || '').trim());
      const headers = ['<th>run</th>', showRole ? '<th>role</th>' : '', showNote ? '<th>note</th>' : '', '<th>status</th>', '<th>purpose</th>', '<th>result</th>'].join('');
      const cols = ['<col class="col-run">', showRole ? '<col class="col-role">' : '', showNote ? '<col class="col-note">' : '', '<col class="col-status">', '<col class="col-purpose">', '<col class="col-result">'].join('');
      const body = rows.map(r => `<tr><td data-cell="run"><button class="link-button" onclick="navigate('#/run/${encodeURIComponent(r.run_id)}')"><code>${esc(r.run_id)}</code></button></td>${showRole ? `<td data-cell="role">${r.role_html || esc(r.role || '')}</td>` : ''}${showNote ? `<td data-cell="note">${r.note_html || esc(r.note || '')}</td>` : ''}<td data-cell="status">${statusBadge(r.status)}</td><td data-cell="purpose">${r.purpose_html || esc(r.purpose)}</td><td data-cell="result">${r.result_html || esc(r.result)}</td></tr>`).join('');
      return `<div class="table-wrap"><table data-table="doc-runs"><colgroup>${cols}</colgroup><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function docTable(rows) {
      if (!rows.length) return empty('No analysis documents yet.');
      return `<div class="table-wrap"><table data-table="docs"><colgroup><col class="col-doc"><col class="col-moc"><col class="col-updated"></colgroup><thead><tr><th>doc</th><th>MOC</th><th>updated</th></tr></thead><tbody>${rows.map(d => `<tr><td data-cell="doc"><button class="link-button" onclick="navigate('#/doc/${encodeURIComponent(d.id)}')"><code>${esc(d.id)}</code></button><br>${esc(d.title)}</td><td data-cell="moc">${esc(d.moc_title || '')}</td><td data-cell="updated">${esc(d.updated_at || '')}</td></tr>`).join('')}</tbody></table></div>`;
    }
    async function loadRun(id) {
      await ensureMocs();
      const r = await api('/api/runs/' + encodeURIComponent(id));
      const sameRun = Boolean(state.currentRun && state.currentRun.id === r.id);
      if (!sameRun) {
        state.wandbChartMode = 'combined';
        state.wandbChartData = null;
        state.wandbCompareData = null;
        state.wandbCompareMode = 'intersection';
      }
      state.currentRun = r;
      $('view').innerHTML = `${hero(`<code>${esc(r.id)}</code>`, `${statusBadge(r.status)} ${esc(r.moc_title)} / ${esc(r.topic_title)}`)}<div class="run-sections"><section class="markdown"><h2>Purpose</h2><p>${r.purpose_html || esc(r.purpose || 'TBD')}</p></section><section class="markdown"><h2>Relation</h2><p>${r.relation_html || esc(r.relation || 'TBD')}</p></section><section class="markdown"><h2>Result</h2><p>${r.result_html || esc(r.result || 'TBD')}</p></section><section class="markdown"><h2>Metadata</h2>${metadata(r.metadata)}</section>${wandbPanel(r)}<section class="markdown"><h2>Analysis</h2>${r.analysis_html || '<p class="muted">No analysis recorded.</p>'}</section></div><h2>Related Docs</h2>${docTable(r.docs)}`;
      if (sameRun) reviveWandbPanel();
    }
    async function loadDoc(id) {
      await ensureMocs();
      const d = await api('/api/docs/' + encodeURIComponent(id));
      const body = d.body_html && d.body_html.trim() ? d.body_html : '<p class="muted">No document body recorded.</p>';
      $('view').innerHTML = `${hero(esc(d.title), `<code>${esc(d.id)}</code> ${esc(d.moc_title || '')}`)}<h2>Related Runs</h2>${docRunTable(d.runs || [])}<h2>Body</h2><div class="markdown">${body}</div>`;
    }
    async function renderRoute() {
      setActive();
      $('view').innerHTML = empty('Loading...');
      try {
        const parts = route().replace(/^#\\/?/, '').split('/').filter(Boolean);
        if (!parts.length) return await loadHome();
        if (parts[0] === 'moc' && parts[1]) return await loadMoc(decodeURIComponent(parts[1]));
        if (parts[0] === 'topic' && parts[1]) return await loadTopic(decodeURIComponent(parts[1]));
        if (parts[0] === 'run' && parts[1]) return await loadRun(decodeURIComponent(parts[1]));
        if (parts[0] === 'doc' && parts[1]) return await loadDoc(decodeURIComponent(parts[1]));
        if (parts[0] === 'runs') return await loadRuns();
        if (parts[0] === 'docs') return await loadDocs();
        $('view').innerHTML = empty('Unknown route.');
      } catch (err) {
        $('view').innerHTML = `<div class="empty error">${esc(err.message || err)}</div>`;
      } finally {
        refreshIcons();
      }
    }
    function refreshIcons() {
      if (window.lucide) window.lucide.createIcons();
    }
    $('backBtn').onclick = () => history.back();
    $('search').onchange = () => route() === '#/docs' ? loadDocs() : loadRuns();
    $('status').onchange = loadRuns;
    window.addEventListener('hashchange', renderRoute);
    window.addEventListener('popstate', renderRoute);
    if (!window.location.hash) window.location.hash = '#/';
    renderRoute();
  </script>
</body>
</html>
"""
