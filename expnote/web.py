# ruff: noqa: E501

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any

import markdown as markdown_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from expnote.db import row_to_dict, transaction


def create_app(root: Path, state_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="expnote", docs_url=None, redoc_url=None)
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/mocs")
    def api_mocs() -> list[dict[str, Any]]:
        with transaction(root, state_dir=state_dir) as conn:
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

    @app.get("/api/mocs/{moc_id}")
    def api_moc(moc_id: str) -> dict[str, Any]:
        with transaction(root, state_dir=state_dir) as conn:
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
        with transaction(root, state_dir=state_dir) as conn:
            _require_moc(conn, moc_id)
            return _topics(conn, moc_id)

    @app.get("/api/topics/{topic_id}/runs")
    def api_topic_runs(topic_id: str) -> list[dict[str, Any]]:
        with transaction(root, state_dir=state_dir) as conn:
            return _runs(conn, topic_id=topic_id)

    @app.get("/api/runs")
    def api_runs(
        moc_id: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        with transaction(root, state_dir=state_dir) as conn:
            return _runs(conn, moc_id=moc_id, topic_id=topic_id, status=status, q=q)

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        with transaction(root, state_dir=state_dir) as conn:
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
            data = row_to_dict(row)
            data["analysis_html"] = render_markdown(str(data.get("analysis") or ""))
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

    @app.get("/api/docs")
    def api_docs(
        moc_id: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        with transaction(root, state_dir=state_dir) as conn:
            return _docs(conn, moc_id=moc_id, q=q)

    @app.get("/api/docs/{doc_id}")
    def api_doc(doc_id: str) -> dict[str, Any]:
        with transaction(root, state_dir=state_dir) as conn:
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
            data = row_to_dict(row)
            data["body_html"] = render_markdown(str(data.get("body") or ""))
            data["runs"] = _doc_runs(conn, doc_id)
            return data

    return app


def render_markdown(text: str) -> str:
    escaped = html.escape(text)
    return markdown_lib.markdown(
        escaped,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )


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
    return [
        row_to_dict(row)
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


def _doc_runs(conn: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
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


_INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>expnote</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #182230;
      --muted: #667085;
      --subtle: #98a2b3;
      --line: rgba(102, 112, 133, 0.22);
      --panel: rgba(255, 255, 255, 0.76);
      --panel-strong: rgba(255, 255, 255, 0.94);
      --accent: #3157d5;
      --accent-soft: #eef4ff;
      --green: #067647;
      --amber: #b54708;
      --red: #b42318;
      --shadow: 0 18px 60px rgba(16, 24, 40, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 12% 8%, rgba(61, 90, 254, 0.13), transparent 28%),
        radial-gradient(circle at 88% 14%, rgba(20, 184, 166, 0.12), transparent 30%),
        linear-gradient(135deg, #f8fbff 0%, #f6f7fb 52%, #fffaf4 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(24, 34, 48, 0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(24, 34, 48, 0.03) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,0.55), transparent 70%);
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
      grid-template-columns: 284px minmax(0, 1fr);
      min-height: 100vh;
      position: relative;
      z-index: 1;
    }
    aside {
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 18px;
      border-right: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.56);
      backdrop-filter: blur(18px);
      overflow: auto;
    }
    main { min-width: 0; padding: 18px 22px 34px; }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
    }
    .logo {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: linear-gradient(135deg, #3157d5, #13b5a6 62%, #f5b544);
      box-shadow: 0 10px 24px rgba(49, 87, 213, 0.25);
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
    }
    .nav-item:hover, .nav-item.active {
      background: rgba(49, 87, 213, 0.10);
      color: var(--accent);
    }
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
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      backdrop-filter: blur(18px);
      box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
    }
    .top-button {
      border-radius: 8px;
      padding: 8px 10px;
      font-weight: 600;
      color: #344054;
    }
    .top-button:hover, .top-button.active { background: var(--accent-soft); color: var(--accent); }
    input, select {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 10px;
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      outline: none;
    }
    input:focus, select:focus { border-color: rgba(49, 87, 213, 0.55); box-shadow: 0 0 0 3px rgba(49, 87, 213, 0.10); }
    #search { min-width: 220px; margin-left: auto; }
    .hero {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 22px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.92), rgba(255,255,255,0.68)),
        radial-gradient(circle at 100% 0%, rgba(49,87,213,0.18), transparent 34%);
      box-shadow: var(--shadow);
    }
    h1 { margin: 0; font-size: 28px; line-height: 1.18; letter-spacing: 0; }
    h2 { margin: 24px 0 10px; font-size: 17px; letter-spacing: 0; }
    h3 { margin: 0 0 8px; font-size: 14px; letter-spacing: 0; }
    p { margin: 0; }
    .muted { color: var(--muted); }
    .subtle { color: var(--subtle); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: var(--panel-strong);
      box-shadow: 0 10px 26px rgba(16, 24, 40, 0.06);
    }
    .card-button { width: 100%; transition: transform 140ms ease, border-color 140ms ease; }
    .card-button:hover { transform: translateY(-1px); border-color: rgba(49, 87, 213, 0.45); }
    .stats { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
    .stat { min-width: 118px; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; background: rgba(255,255,255,0.72); }
    .stat strong { display: block; font-size: 20px; }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-strong);
      box-shadow: 0 10px 26px rgba(16, 24, 40, 0.05);
    }
    table { width: 100%; border-collapse: collapse; min-width: 760px; }
    th, td { border-bottom: 1px solid rgba(102, 112, 133, 0.16); padding: 10px 12px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 700; background: rgba(248, 250, 252, 0.78); }
    tbody tr:hover { background: rgba(238, 244, 255, 0.62); }
    tbody tr:last-child td { border-bottom: 0; }
    .link-button { color: var(--accent); font-weight: 700; }
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
    .status-running { color: var(--amber); background: #fff7ed; border-color: rgba(181, 71, 8, 0.20); }
    .status-finished { color: var(--green); background: #ecfdf3; border-color: rgba(6, 118, 71, 0.20); }
    .status-failed { color: var(--red); background: #fef3f2; border-color: rgba(180, 35, 24, 0.20); }
    .pill { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 7px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: #fff; }
    .run-sections { display: grid; gap: 12px; margin-top: 16px; }
    .markdown {
      max-width: 980px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 18px;
      background: var(--panel-strong);
      box-shadow: 0 10px 26px rgba(16, 24, 40, 0.05);
    }
    .markdown p { margin: 0 0 12px; }
    .markdown h1, .markdown h2, .markdown h3 { margin: 18px 0 10px; }
    .markdown pre { overflow: auto; background: #111827; color: #f9fafb; padding: 12px; border-radius: 10px; }
    .metadata-list { margin: 0; padding-left: 18px; }
    .metadata-list li { margin: 4px 0; }
    a { color: var(--accent); font-weight: 700; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .empty {
      border: 1px dashed rgba(102, 112, 133, 0.35);
      border-radius: 12px;
      padding: 18px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.56);
    }
    .error { color: var(--red); }
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
        <button class="nav-item" data-route="#/" onclick="navigate('#/')">MOC overview</button>
        <button class="nav-item" data-route="#/runs" onclick="navigate('#/runs')">All runs</button>
        <button class="nav-item" data-route="#/docs" onclick="navigate('#/docs')">All docs</button>
      </div>
      <div class="nav-section">
        <p class="label">SQL MOCs</p>
        <div id="mocNav"></div>
      </div>
    </aside>
    <main>
      <div class="toolbar">
        <button id="backBtn" class="top-button" type="button">Back</button>
        <button class="top-button" data-route="#/" onclick="navigate('#/')">MOCs</button>
        <button class="top-button" data-route="#/runs" onclick="navigate('#/runs')">Runs</button>
        <button class="top-button" data-route="#/docs" onclick="navigate('#/docs')">Docs</button>
        <input id="search" placeholder="Search runs or docs">
        <select id="status"><option value="">Any status</option><option>running</option><option>finished</option><option>failed</option></select>
      </div>
      <section id="view"></section>
    </main>
  </div>
  <script>
    const state = { mocs: [], selectedMoc: null };
    const $ = (id) => document.getElementById(id);
    const route = () => window.location.hash || '#/';

    async function api(path) {
      const res = await fetch(path);
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
      $('mocNav').innerHTML = state.mocs.map(m => `<button class="nav-item" data-route="#/moc/${encodeURIComponent(m.id)}" onclick="navigate('#/moc/${encodeURIComponent(m.id)}')">${esc(m.title)}<code>${esc(m.id)}</code></button>`).join('') || empty('No MOCs yet');
      setActive();
    }
    function hero(title, subtitle, stats = '') {
      return `<div class="hero"><h1>${title}</h1><p class="muted">${subtitle}</p>${stats}</div>`;
    }
    async function loadHome() {
      await ensureMocs();
      const stats = `<div class="stats"><div class="stat"><span class="muted">MOCs</span><strong>${state.mocs.length}</strong></div></div>`;
      $('view').innerHTML = `${hero('Experiment knowledge base', 'Browse SQL MOCs, topics, runs, and analysis documents from one read-only dashboard.', stats)}<h2>MOCs</h2><div class="grid">${state.mocs.map(m => `<button class="card card-button" onclick="navigate('#/moc/${encodeURIComponent(m.id)}')"><h3>${esc(m.title)}</h3><code>${esc(m.id)}</code><p class="muted">${esc(m.summary || 'No summary recorded.')}</p></button>`).join('')}</div>`;
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
      return `<div class="table-wrap"><table data-table="topic-runs"><thead><tr><th>run</th><th>status</th><th>purpose</th><th>relation</th><th>result</th></tr></thead><tbody>${rows.map(r => `<tr><td><button class="link-button" onclick="navigate('#/run/${encodeURIComponent(idForRun(r))}')"><code>${esc(idForRun(r))}</code></button></td><td>${statusBadge(r.status)}</td><td>${esc(r.purpose)}</td><td>${esc(r.relation)}</td><td>${esc(r.result)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function docRunTable(rows) {
      if (!rows.length) return empty('No related runs linked to this document.');
      return `<div class="table-wrap"><table data-table="doc-runs"><thead><tr><th>run</th><th>role</th><th>note</th><th>status</th><th>purpose</th><th>result</th></tr></thead><tbody>${rows.map(r => `<tr><td><button class="link-button" onclick="navigate('#/run/${encodeURIComponent(r.run_id)}')"><code>${esc(r.run_id)}</code></button></td><td>${esc(r.role || '')}</td><td>${esc(r.note || '')}</td><td>${statusBadge(r.status)}</td><td>${esc(r.purpose)}</td><td>${esc(r.result)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function docTable(rows) {
      if (!rows.length) return empty('No analysis documents yet.');
      return `<div class="table-wrap"><table data-table="docs"><thead><tr><th>doc</th><th>MOC</th><th>updated</th></tr></thead><tbody>${rows.map(d => `<tr><td><button class="link-button" onclick="navigate('#/doc/${encodeURIComponent(d.id)}')"><code>${esc(d.id)}</code></button><br>${esc(d.title)}</td><td>${esc(d.moc_title || '')}</td><td>${esc(d.updated_at || '')}</td></tr>`).join('')}</tbody></table></div>`;
    }
    async function loadRun(id) {
      await ensureMocs();
      const r = await api('/api/runs/' + encodeURIComponent(id));
      $('view').innerHTML = `${hero(`<code>${esc(r.id)}</code>`, `${statusBadge(r.status)} ${esc(r.moc_title)} / ${esc(r.topic_title)}`)}<div class="run-sections"><section class="markdown"><h2>Purpose</h2><p>${esc(r.purpose || 'TBD')}</p></section><section class="markdown"><h2>Relation</h2><p>${esc(r.relation || 'TBD')}</p></section><section class="markdown"><h2>Result</h2><p>${esc(r.result || 'TBD')}</p></section><section class="markdown"><h2>Metadata</h2>${metadata(r.metadata)}</section><section class="markdown"><h2>Analysis</h2>${r.analysis_html || '<p class="muted">No analysis recorded.</p>'}</section></div><h2>Related Docs</h2>${docTable(r.docs)}`;
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
      }
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
