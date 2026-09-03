# ruff: noqa: E501

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any

import markdown as markdown_lib
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from expnote.db import (
    append_event,
    now_iso,
    readonly_transaction,
    row_to_dict,
    transaction,
)
from expnote.doc_charts import (
    DocChartError,
    chart_summary,
    doc_chart_context,
    render_chart,
    resolve_asset,
)
from expnote.links import render_html_run_links
from expnote.tensorboard_live import (
    TensorboardLiveError,
    fetch_tensorboard_charts,
    load_cached_tensorboard_chart,
)
from expnote.wandb_live import (
    WandbLiveError,
    clear_wandb_cache,
    fetch_wandb_charts,
    load_cached_wandb_chart,
    wandb_cache_stats,
)


def create_app(root: Path, state_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="expnote", docs_url=None, redoc_url=None)
    root = root.resolve()
    state_dir = state_dir.resolve() if state_dir is not None else None
    state_root = state_dir or root / ".expnote"
    cache_dir = state_root / "wandb-cache"
    tensorboard_cache_dir = state_root / "tensorboard-cache"

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
            return _run_detail(conn, run_id)

    @app.patch("/api/runs/{run_id}")
    def api_update_run(
        run_id: str,
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        updates = _editable_updates(
            payload,
            allowed_fields={"purpose", "relation", "result", "analysis"},
        )
        expected_updated_at = _expected_updated_at(payload)
        ts = now_iso()
        with transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE id = ? AND deleted_at IS NULL
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Run not found")
            _check_expected_updated_at(row["updated_at"], expected_updated_at)
            fields = sorted(updates)
            assignments = ", ".join(f"{field} = ?" for field in fields)
            values = [updates[field] for field in fields]
            conn.execute(
                f"UPDATE runs SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, ts, run_id),
            )
            event_row = conn.execute(
                "SELECT * FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            data = _run_detail(conn, run_id)
        append_event(root, "run.update", row_to_dict(event_row), state_dir=state_dir)
        return data

    @app.get("/api/runs/{run_id}/wandb")
    def api_run_wandb(
        run_id: str,
        refresh: bool = False,
        cached_only: bool = False,
    ) -> dict[str, Any]:
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
        if cached_only:
            cached = load_cached_wandb_chart(cache_dir, run_id)
            if cached is None:
                return {
                    "available": False,
                    "reason": "no_cache",
                    "message": "No cached W&B chart data for this run yet.",
                }
            return cached
        try:
            return fetch_wandb_charts(
                str(url),
                run_id=run_id,
                status=str(data.get("status") or ""),
                cache_dir=cache_dir,
                samples=1000,
                force=refresh,
            )
        except WandbLiveError as exc:
            return {
                "available": False,
                "reason": exc.reason,
                "message": exc.message,
            }

    @app.get("/api/runs/{run_id}/tensorboard")
    def api_run_tensorboard(
        run_id: str,
        samples: Annotated[int, Query(ge=0)] = 0,
        refresh: bool = False,
        cached_only: bool = False,
    ) -> dict[str, Any]:
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
        path = (data.get("metadata") or {}).get("tensorboard_dir")
        if not path:
            return {
                "available": False,
                "reason": "missing_tensorboard_dir",
                "message": "This run does not have metadata.tensorboard_dir.",
            }
        if cached_only:
            cached = load_cached_tensorboard_chart(tensorboard_cache_dir, run_id)
            if cached is None:
                return {
                    "available": False,
                    "reason": "no_cache",
                    "message": "No cached TensorBoard chart data for this run yet.",
                }
            return cached
        try:
            return fetch_tensorboard_charts(
                str(path),
                samples=samples,
                run_id=run_id,
                status=str(data.get("status") or ""),
                cache_dir=tensorboard_cache_dir,
                force=refresh,
            )
        except TensorboardLiveError as exc:
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
            return _doc_detail(conn, doc_id)

    @app.patch("/api/docs/{doc_id}")
    def api_update_doc(
        doc_id: str,
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        updates = _editable_updates(payload, allowed_fields={"title", "body"})
        expected_updated_at = _expected_updated_at(payload)
        ts = now_iso()
        with transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                """
                SELECT * FROM docs
                WHERE id = ? AND deleted_at IS NULL
                """,
                (doc_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Doc not found")
            _check_expected_updated_at(row["updated_at"], expected_updated_at)
            fields = sorted(updates)
            assignments = ", ".join(f"{field} = ?" for field in fields)
            values = [updates[field] for field in fields]
            conn.execute(
                f"UPDATE docs SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, ts, doc_id),
            )
            event_row = conn.execute(
                "SELECT * FROM docs WHERE id = ?",
                (doc_id,),
            ).fetchone()
            data = _doc_detail(conn, doc_id)
        append_event(root, "doc.update", row_to_dict(event_row), state_dir=state_dir)
        return data

    @app.get("/api/docs/{doc_id}/charts")
    def api_doc_charts(doc_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            _require_doc(conn, doc_id)
        return chart_summary(doc_chart_context(state_root, doc_id))

    @app.get("/api/docs/{doc_id}/charts/{chart_id}")
    def api_doc_chart(doc_id: str, chart_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            _require_doc(conn, doc_id)
        return render_chart(doc_chart_context(state_root, doc_id), chart_id)

    @app.post("/api/docs/{doc_id}/charts/{chart_id}/refresh")
    def api_refresh_doc_chart(doc_id: str, chart_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            _require_doc(conn, doc_id)
        return render_chart(doc_chart_context(state_root, doc_id), chart_id, refresh=True)

    @app.get("/api/docs/{doc_id}/assets/{asset_path:path}")
    def api_doc_asset(doc_id: str, asset_path: str) -> FileResponse:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            _require_doc(conn, doc_id)
        try:
            path = resolve_asset(doc_chart_context(state_root, doc_id), asset_path)
        except DocChartError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(path)

    @app.get("/api/benchmarks")
    def api_benchmarks() -> list[dict[str, Any]]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            return [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM benchmarks
                    WHERE deleted_at IS NULL
                    ORDER BY updated_at DESC, id DESC
                    """
                )
            ]

    @app.get("/api/benchmarks/{benchmark_id}")
    def api_benchmark(benchmark_id: str) -> dict[str, Any]:
        with readonly_transaction(root, state_dir=state_dir) as conn:
            row = conn.execute(
                "SELECT * FROM benchmarks WHERE id = ? AND deleted_at IS NULL",
                (benchmark_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Benchmark not found")
            data = row_to_dict(row)
            data["tasks"] = [
                row_to_dict(task_row)
                for task_row in conn.execute(
                    """
                    SELECT * FROM benchmark_tasks
                    WHERE benchmark_id = ? AND deleted_at IS NULL
                    ORDER BY position ASC, created_at ASC
                    """,
                    (benchmark_id,),
                )
            ]
            data["algos"] = [
                row_to_dict(algo_row)
                for algo_row in conn.execute(
                    """
                    SELECT * FROM benchmark_algos
                    WHERE benchmark_id = ? AND deleted_at IS NULL
                    ORDER BY position ASC, created_at ASC
                    """,
                    (benchmark_id,),
                )
            ]
            active_run_ids = _active_run_ids(conn)
            data["cells"] = [
                _render_doc_run_text_fields(row_to_dict(cell_row), active_run_ids)
                for cell_row in conn.execute(
                    """
                    SELECT
                        benchmark_cells.task_id,
                        benchmark_cells.algo_id,
                        benchmark_cells.run_id,
                        runs.status,
                        runs.result,
                        runs.purpose,
                        topics.title AS topic_title,
                        mocs.title AS moc_title
                    FROM benchmark_cells
                    JOIN runs ON runs.id = benchmark_cells.run_id
                    JOIN topics ON topics.id = runs.topic_id
                    JOIN mocs ON mocs.id = topics.moc_id
                    WHERE benchmark_cells.benchmark_id = ?
                        AND benchmark_cells.deleted_at IS NULL
                        AND runs.deleted_at IS NULL
                    """,
                    (benchmark_id,),
                )
            ]
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


_CHART_PLACEHOLDER_RE = re.compile(r"\{\{\s*chart:([A-Za-z0-9_.-]+)\s*\}\}")


def render_doc_markdown(text: str, active_run_ids: set[str] | None = None) -> str:
    parts: list[str] = []
    pos = 0
    for match in _CHART_PLACEHOLDER_RE.finditer(text):
        if match.start() > pos:
            rendered = render_markdown(text[pos : match.start()], active_run_ids)
            if rendered:
                parts.append(rendered)
        chart_id = html.escape(match.group(1), quote=True)
        parts.append(
            f'<section class="doc-chart" data-chart-id="{chart_id}">'
            f'<div class="doc-chart-title"><code>{chart_id}</code></div>'
            '<div class="doc-chart-body">Loading chart...</div>'
            "</section>"
        )
        pos = match.end()
    if pos < len(text):
        rendered = render_markdown(text[pos:], active_run_ids)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def render_inline_text(text: str, active_run_ids: set[str] | None = None) -> str:
    escaped = html.escape(text or "")
    linked = render_html_run_links(escaped, active_run_ids or set())
    return linked.replace("\n", "<br>")


def _run_detail(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
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


def _doc_detail(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any]:
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
    data["body_html"] = render_doc_markdown(str(data.get("body") or ""), active_run_ids)
    data["runs"] = _doc_runs(conn, doc_id, active_run_ids=active_run_ids)
    return data


def _editable_updates(
    payload: dict[str, Any], *, allowed_fields: set[str]
) -> dict[str, str]:
    allowed_keys = allowed_fields | {"expected_updated_at"}
    extra = sorted(set(payload) - allowed_keys)
    if extra:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported fields: {', '.join(extra)}",
        )
    updates = {field: payload[field] for field in allowed_fields if field in payload}
    if not updates:
        raise HTTPException(status_code=400, detail="No editable fields provided")
    invalid = sorted(field for field, value in updates.items() if not isinstance(value, str))
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Editable fields must be strings: {', '.join(invalid)}",
        )
    return updates


def _expected_updated_at(payload: dict[str, Any]) -> str:
    value = payload.get("expected_updated_at")
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=400,
            detail="expected_updated_at is required",
        )
    return value


def _check_expected_updated_at(current: str, expected: str) -> None:
    if current != expected:
        raise HTTPException(
            status_code=409,
            detail="Record changed since it was loaded. Refresh before saving.",
        )


def _require_moc(conn: sqlite3.Connection, moc_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM mocs WHERE id = ? AND deleted_at IS NULL", (moc_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="MOC not found")


def _require_doc(conn: sqlite3.Connection, doc_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM docs WHERE id = ? AND deleted_at IS NULL", (doc_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Doc not found")


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
    button, input, select, textarea { font: inherit; }
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
    textarea {
      width: 100%;
      min-height: 92px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 11px;
      background: rgba(255, 255, 255, 0.88);
      color: var(--ink);
      outline: none;
      resize: vertical;
    }
    textarea:focus { border-color: rgba(47, 91, 216, 0.55); box-shadow: 0 0 0 3px rgba(47, 91, 216, 0.10); }
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
    .grid-stack { grid-template-columns: 1fr; }
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
    table[data-table="topic-runs"] { min-width: 1080px; table-layout: fixed; }
    table[data-table="doc-runs"] { min-width: 100%; table-layout: auto; }
    table[data-table="docs"] { min-width: 720px; }
    table[data-table="benchmark-matrix"] { width: auto; }
    table[data-table="benchmark-matrix"] th, table[data-table="benchmark-matrix"] td { white-space: nowrap; }
    col.col-run { width: 190px; }
    col.col-status { width: 100px; }
    col.col-role { width: 90px; }
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
    td[data-cell="status"], td[data-cell="role"], td[data-cell="updated"] { white-space: nowrap; }
    td[data-cell="run"] .link-button { max-width: 100%; white-space: normal; }
    td[data-cell="run"] .link-button code { min-width: 0; overflow-wrap: anywhere; white-space: normal; }
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
    .record-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    .edit-form {
      max-width: 1040px;
      display: grid;
      gap: 12px;
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      background: var(--panel-strong);
      box-shadow: var(--shadow-soft);
    }
    .edit-field { display: grid; gap: 6px; }
    .edit-field label { color: #344054; font-size: 12px; font-weight: 800; }
    .edit-title { width: min(100%, 680px); }
    .edit-status { color: var(--muted); }
    .edit-status.error { color: var(--red); }
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
    .doc-chart {
      margin: 18px 0;
      border: 1px solid rgba(31, 41, 55, 0.10);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }
    .doc-chart-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(31, 41, 55, 0.10);
    }
    .doc-chart-title {
      color: #344054;
      font-size: 13px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .doc-chart-body { min-height: 220px; padding: 12px; }
    .doc-chart-plot { min-height: 360px; }
    .doc-chart-image {
      max-width: 100%;
      height: auto;
      border: 1px solid rgba(31, 41, 55, 0.10);
      border-radius: 6px;
    }
    .doc-chart-error { color: var(--red); white-space: pre-wrap; }
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
        <button class="nav-item" data-route="#/benchmarks" onclick="navigate('#/benchmarks')"><i data-lucide="table"></i><span class="nav-text">All benchmarks</span></button>
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
          <button class="top-button" data-route="#/benchmarks" onclick="navigate('#/benchmarks')"><i data-lucide="table"></i>Benchmarks</button>
        </div>
        <input id="search" placeholder="Search runs or docs">
        <select id="status"><option value="">Any status</option><option>running</option><option>finished</option><option>failed</option></select>
      </div>
      <section id="view"></section>
    </main>
  </div>
  <script>
    const state = { mocs: [], selectedMoc: null, currentRun: null, currentDoc: null, wandbChartData: null, wandbCompareRuns: [], wandbCompareData: null, wandbCompareMode: 'intersection', tensorboardChartData: null };
    const $ = (id) => document.getElementById(id);
    const route = () => window.location.hash || '#/';

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    async function patchJson(path, payload) {
      const res = await fetch(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) {
        let detail = await res.text();
        try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
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
    function hasTensorboardDir(meta) {
      return Boolean(meta && meta.tensorboard_dir);
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
      return `<section class="markdown" id="wandb-panel"><h2>W&B Charts</h2><div class="wandb-actions"><button id="wandbFetch" class="wandb-button" type="button" data-run-id="${id}" onclick="loadWandbCharts(this.dataset.runId)"><i data-lucide="line-chart"></i>Fetch live W&B charts</button><button id="wandbCompare" class="top-button" type="button" onclick="openWandbCompareModal()" hidden disabled>Compare runs</button><span id="wandbStatus" class="wandb-status">Uses metadata.wandb_url and does not write to SQL.</span></div><div id="wandbCharts"></div><div id="wandbCompareResults"></div></section>`;
    }
    function reviveWandbPanel() {
      if (!state.wandbChartData || !hasWandbUrl(state.currentRun && state.currentRun.metadata)) return;
      updateWandbCompareButton();
      const data = state.wandbChartData;
      $('wandbStatus').textContent = `${data.run_path}: ${data.groups.length} metric groups from ${data.samples} sampled history rows (${data.cached ? 'cached' : 'live'}).`;
      if (state.wandbCompareData) {
        renderWandbComparison();
      } else {
        renderWandbCharts(data);
      }
    }
    async function loadCachedWandbCharts(r) {
      if (!hasWandbUrl(r.metadata)) return;
      try {
        const data = await api('/api/runs/' + encodeURIComponent(r.id) + '/wandb?cached_only=true');
        if (!data.available || state.currentRun !== r) return;
        state.wandbChartData = data;
        updateWandbCompareButton();
        const status = $('wandbStatus');
        if (status) {
          status.textContent = `${data.run_path}: ${data.groups.length} metric groups from ${data.samples} sampled history rows (cached).`;
        }
        renderWandbCharts(data);
      } catch (err) {
        // No local cache yet; leave the manual fetch button in its default state.
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
      updateWandbCompareButton();
      status.textContent = 'Fetching live W&B history...';
      target.innerHTML = '';
      $('wandbCompareResults').innerHTML = '';
      try {
        const data = await api('/api/runs/' + encodeURIComponent(runId) + '/wandb?refresh=true');
        if (!data.available) {
          status.innerHTML = `<span class="error">${esc(data.reason)}: ${esc(data.message)}</span>`;
          return;
        }
        state.wandbChartData = data;
        renderWandbCharts(data);
        updateWandbCompareButton();
        status.textContent = `${data.run_path}: ${data.groups.length} metric groups from ${data.samples} sampled history rows (${data.cached ? 'cached' : 'live'}).`;
      } catch (err) {
        status.innerHTML = `<span class="error">${esc(err.message || err)}</span>`;
      } finally {
        button.disabled = false;
      }
    }
    function updateWandbCompareButton() {
      const button = $('wandbCompare');
      if (!button) return;
      const visible = Boolean(state.wandbChartData);
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
      renderSplitMetricCharts(target, groups, 'wandb');
    }
    function tensorboardPanel(r) {
      if (!hasTensorboardDir(r.metadata)) return '';
      const id = esc(r.id);
      return `<section class="markdown" id="tensorboard-panel"><h2>TensorBoard Charts</h2><div class="wandb-actions"><button id="tensorboardFetch" class="wandb-button" type="button" data-run-id="${id}" onclick="loadTensorboardCharts(this.dataset.runId)"><i data-lucide="line-chart"></i>Fetch TensorBoard charts</button><span id="tensorboardStatus" class="wandb-status">Uses metadata.tensorboard_dir and reads local event files; does not write to SQL.</span></div><div id="tensorboardCharts"></div></section>`;
    }
    function reviveTensorboardPanel() {
      if (!state.tensorboardChartData || !hasTensorboardDir(state.currentRun && state.currentRun.metadata)) return;
      const data = state.tensorboardChartData;
      $('tensorboardStatus').textContent = `${data.source}: ${data.groups.length} metric groups (local TensorBoard log, ${data.cached ? 'cached' : 'live'}).`;
      renderTensorboardCharts(data);
    }
    async function loadCachedTensorboardCharts(r) {
      if (!hasTensorboardDir(r.metadata)) return;
      try {
        const data = await api('/api/runs/' + encodeURIComponent(r.id) + '/tensorboard?cached_only=true');
        if (!data.available || state.currentRun !== r) return;
        state.tensorboardChartData = data;
        const status = $('tensorboardStatus');
        if (status) {
          status.textContent = `${data.source}: ${data.groups.length} metric groups (local TensorBoard log, cached).`;
        }
        renderTensorboardCharts(data);
      } catch (err) {
        // No local cache yet; leave the manual fetch button in its default state.
      }
    }
    async function loadTensorboardCharts(runId) {
      const button = $('tensorboardFetch');
      const status = $('tensorboardStatus');
      const target = $('tensorboardCharts');
      if (!window.Plotly) {
        status.innerHTML = '<span class="error">Plotly is not available from the expnote server.</span>';
        return;
      }
      button.disabled = true;
      state.tensorboardChartData = null;
      status.textContent = 'Reading local TensorBoard logs...';
      target.innerHTML = '';
      try {
        const data = await api('/api/runs/' + encodeURIComponent(runId) + '/tensorboard?samples=0&refresh=true');
        if (!data.available) {
          status.innerHTML = `<span class="error">${esc(data.reason)}: ${esc(data.message)}</span>`;
          return;
        }
        state.tensorboardChartData = data;
        renderTensorboardCharts(data);
        status.textContent = `${data.source}: ${data.groups.length} metric groups (local TensorBoard log, ${data.cached ? 'cached' : 'live'}).`;
      } catch (err) {
        status.innerHTML = `<span class="error">${esc(err.message || err)}</span>`;
      } finally {
        button.disabled = false;
      }
    }
    function renderTensorboardCharts(data) {
      const target = $('tensorboardCharts');
      const groups = data.groups || [];
      if (!groups.length) {
        target.innerHTML = empty('No numeric metrics were found in this TensorBoard log.');
        return;
      }
      renderSplitMetricCharts(target, groups, 'tensorboard');
    }
    function metricTrace(chart) {
      return {
          x: chart.x || [],
          y: chart.y || [],
          mode: 'lines',
          type: 'scatter',
          name: chart.metric,
          hovertemplate: '%{x}<br>%{y}<extra>%{fullData.name}</extra>'
      };
    }
    function metricLayout(title, showLegend) {
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
    function metricPlotOptions() {
      return {
          responsive: true,
          displaylogo: false
      };
    }
    function renderSplitMetricCharts(target, groups, idPrefix) {
      target.innerHTML = groups.map((group, groupIndex) => `<div class="wandb-group"><h3>${esc(group.name)}</h3><div class="wandb-chart-grid">${(group.charts || []).map((chart, chartIndex) => `<div class="wandb-chart-card"><div class="wandb-chart-title">${esc(chart.metric)}</div><div class="wandb-chart" id="${idPrefix}-chart-split-${groupIndex}-${chartIndex}"></div></div>`).join('')}</div></div>`).join('');
      groups.forEach((group, groupIndex) => {
        (group.charts || []).forEach((chart, chartIndex) => {
          Plotly.newPlot(`${idPrefix}-chart-split-${groupIndex}-${chartIndex}`, [metricTrace(chart)], metricLayout('', false), metricPlotOptions());
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
      if (!(state.currentRun && state.wandbChartData)) return;
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
          const trace = metricTrace(chart);
          trace.name = run.id;
          trace.line = { color: colorForRun(run.id), width: 2 };
          return [trace];
        });
        Plotly.newPlot(`wandb-compare-chart-${index}`, traces, metricLayout('', true), metricPlotOptions());
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
      const benchmarks = await api('/api/benchmarks');
      const statusStats = runStats.by_status.map(s => `<div class="stat"><span class="muted">${esc(s.status || 'unknown')}</span><strong>${s.count}</strong></div>`).join('');
      const stats = `<div class="stats"><div class="stat"><span class="muted">MOCs</span><strong>${state.mocs.length}</strong></div><div class="stat"><span class="muted">Benchmarks</span><strong>${benchmarks.length}</strong></div>${statusStats}<div class="stat"><span class="muted">W&B cache</span><strong id="wandbCacheSize">${formatBytes(cache.bytes)}</strong><button class="top-button" type="button" onclick="clearWandbCache()">Clear W&B cache</button></div></div>`;
      const benchmarksSection = benchmarks.length ? `<h2>Benchmarks</h2><div class="grid">${benchmarks.map(b => `<button class="card card-button" onclick="navigate('#/benchmark/${encodeURIComponent(b.id)}')"><h3>${esc(b.title)}</h3><code>${esc(b.id)}</code><p class="muted">${esc(b.summary || 'No summary recorded.')}</p></button>`).join('')}</div>` : '';
      $('view').innerHTML = `${hero('Experiment knowledge base', 'Browse SQL MOCs, topics, runs, and analysis documents.', stats)}${weeklyRunBars(runStats.by_week)}<h2>MOCs</h2><div class="grid">${state.mocs.map(m => `<button class="card card-button" onclick="navigate('#/moc/${encodeURIComponent(m.id)}')"><h3>${esc(m.title)}</h3><code>${esc(m.id)}</code><p class="muted">${esc(m.summary || 'No summary recorded.')}</p></button>`).join('')}</div>${benchmarksSection}`;
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
      $('view').innerHTML = `${hero(esc(moc.title), `<code>${esc(moc.id)}</code> ${esc(moc.summary || '')}`, stats)}<h2>Topics</h2><div class="grid grid-stack">${moc.topics.map(t => `<button class="card card-button" onclick="navigate('#/topic/${encodeURIComponent(t.id)}')"><h3>${esc(t.title)}</h3><p class="muted">${esc(t.summary || 'No summary recorded.')}</p></button>`).join('') || empty('No topics in this MOC.')}</div><h2>Docs</h2>${docTable(moc.docs)}`;
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
      return `<div class="table-wrap"><table data-table="topic-runs">${cols}<thead><tr><th>run</th><th>status</th><th>purpose</th><th>relation</th><th>result</th></tr></thead><tbody>${rows.map(r => `<tr><td data-cell="run"><button class="link-button" title="${esc(idForRun(r))}" onclick="navigate('#/run/${encodeURIComponent(idForRun(r))}')"><code>${esc(idForRun(r))}</code></button></td><td data-cell="status">${statusBadge(r.status)}</td><td data-cell="purpose">${r.purpose_html || esc(r.purpose)}</td><td data-cell="relation">${r.relation_html || esc(r.relation)}</td><td data-cell="result">${r.result_html || esc(r.result)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function docRunTable(rows) {
      if (!rows.length) return empty('No related runs linked to this document.');
      const showRole = rows.some(r => String(r.role || '').trim());
      const showNote = rows.some(r => String(r.note || '').trim());
      const headers = ['<th>run</th>', showRole ? '<th>role</th>' : '', showNote ? '<th>note</th>' : '', '<th>status</th>', '<th>purpose</th>', '<th>result</th>'].join('');
      const body = rows.map(r => `<tr><td data-cell="run"><button class="link-button" title="${esc(r.run_id)}" onclick="navigate('#/run/${encodeURIComponent(r.run_id)}')"><code>${esc(r.run_id)}</code></button></td>${showRole ? `<td data-cell="role">${r.role_html || esc(r.role || '')}</td>` : ''}${showNote ? `<td data-cell="note">${r.note_html || esc(r.note || '')}</td>` : ''}<td data-cell="status">${statusBadge(r.status)}</td><td data-cell="purpose">${r.purpose_html || esc(r.purpose)}</td><td data-cell="result">${r.result_html || esc(r.result)}</td></tr>`).join('');
      return `<div class="table-wrap"><table data-table="doc-runs"><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function docTable(rows) {
      if (!rows.length) return empty('No analysis documents yet.');
      return `<div class="table-wrap"><table data-table="docs"><colgroup><col class="col-doc"><col class="col-moc"><col class="col-updated"></colgroup><thead><tr><th>doc</th><th>MOC</th><th>updated</th></tr></thead><tbody>${rows.map(d => `<tr><td data-cell="doc"><button class="link-button" onclick="navigate('#/doc/${encodeURIComponent(d.id)}')"><code>${esc(d.id)}</code></button><br>${esc(d.title)}</td><td data-cell="moc">${esc(d.moc_title || '')}</td><td data-cell="updated">${esc(d.updated_at || '')}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function runReadOnly(r) {
      return `<div class="record-actions"><button class="top-button" type="button" onclick="renderRunEditForm()"><i data-lucide="edit-3"></i>Edit</button><span class="edit-status" id="runEditStatus"></span></div><div class="run-sections"><section class="markdown"><h2>Purpose</h2><p>${r.purpose_html || esc(r.purpose || 'TBD')}</p></section><section class="markdown"><h2>Relation</h2><p>${r.relation_html || esc(r.relation || 'TBD')}</p></section><section class="markdown"><h2>Result</h2><p>${r.result_html || esc(r.result || 'TBD')}</p></section><section class="markdown"><h2>Metadata</h2>${metadata(r.metadata)}</section>${wandbPanel(r)}${tensorboardPanel(r)}<section class="markdown"><h2>Analysis</h2>${r.analysis_html || '<p class="muted">No analysis recorded.</p>'}</section></div><h2>Related Docs</h2>${docTable(r.docs)}`;
    }
    function renderRun(r, preserveCharts = false) {
      $('view').innerHTML = `${hero(`<code>${esc(r.id)}</code>`, `${statusBadge(r.status)} ${esc(r.moc_title)} / ${esc(r.topic_title)}`)}${runReadOnly(r)}`;
      if (preserveCharts) {
        reviveWandbPanel();
        reviveTensorboardPanel();
      } else {
        loadCachedWandbCharts(r);
        loadCachedTensorboardCharts(r);
      }
      if (window.lucide) window.lucide.createIcons();
    }
    function renderRunEditForm() {
      const r = state.currentRun;
      if (!r) return;
      $('view').innerHTML = `${hero(`<code>${esc(r.id)}</code>`, `${statusBadge(r.status)} ${esc(r.moc_title)} / ${esc(r.topic_title)}`)}<form class="edit-form" onsubmit="saveRunEdit(event)"><input type="hidden" id="runExpectedUpdatedAt" value="${esc(r.updated_at || '')}"><div class="edit-field"><label for="runPurpose">Purpose</label><textarea id="runPurpose" rows="4">${esc(r.purpose || '')}</textarea></div><div class="edit-field"><label for="runRelation">Relation</label><textarea id="runRelation" rows="4">${esc(r.relation || '')}</textarea></div><div class="edit-field"><label for="runResult">Result</label><textarea id="runResult" rows="3">${esc(r.result || '')}</textarea></div><div class="edit-field"><label for="runAnalysis">Analysis</label><textarea id="runAnalysis" rows="12">${esc(r.analysis || '')}</textarea></div><div class="record-actions"><button class="wandb-button" type="submit"><i data-lucide="save"></i>Save</button><button class="top-button" type="button" onclick="renderRun(state.currentRun)"><i data-lucide="x"></i>Cancel</button><span class="edit-status" id="runEditStatus"></span></div></form>`;
      if (window.lucide) window.lucide.createIcons();
    }
    async function saveRunEdit(event) {
      event.preventDefault();
      const status = $('runEditStatus');
      status.className = 'edit-status';
      status.textContent = 'Saving...';
      try {
        const payload = {
          expected_updated_at: $('runExpectedUpdatedAt').value,
          purpose: $('runPurpose').value,
          relation: $('runRelation').value,
          result: $('runResult').value,
          analysis: $('runAnalysis').value
        };
        const data = await patchJson('/api/runs/' + encodeURIComponent(state.currentRun.id), payload);
        state.currentRun = data;
        state.wandbChartData = null;
        state.wandbCompareData = null;
        state.tensorboardChartData = null;
        renderRun(data);
      } catch (err) {
        status.className = 'edit-status error';
        status.textContent = err.message || String(err);
      }
    }
    function docReadOnly(d) {
      const body = d.body_html && d.body_html.trim() ? d.body_html : '<p class="muted">No document body recorded.</p>';
      return `<div class="record-actions"><button class="top-button" type="button" onclick="renderDocEditForm()"><i data-lucide="edit-3"></i>Edit</button><span class="edit-status" id="docEditStatus"></span></div><h2>Related Runs</h2>${docRunTable(d.runs || [])}<h2>Body</h2><div class="markdown">${body}</div>`;
    }
    function renderDoc(d) {
      $('view').innerHTML = `${hero(esc(d.title), `<code>${esc(d.id)}</code> ${esc(d.moc_title || '')}`)}${docReadOnly(d)}`;
      loadDocCharts(d.id);
      if (window.lucide) window.lucide.createIcons();
    }
    function renderDocEditForm() {
      const d = state.currentDoc;
      if (!d) return;
      $('view').innerHTML = `${hero(esc(d.title), `<code>${esc(d.id)}</code> ${esc(d.moc_title || '')}`)}<form class="edit-form" onsubmit="saveDocEdit(event)"><input type="hidden" id="docExpectedUpdatedAt" value="${esc(d.updated_at || '')}"><div class="edit-field"><label for="docTitle">Title</label><input class="edit-title" id="docTitle" value="${esc(d.title || '')}"></div><div class="edit-field"><label for="docBody">Body</label><textarea id="docBody" rows="18">${esc(d.body || '')}</textarea></div><div class="record-actions"><button class="wandb-button" type="submit"><i data-lucide="save"></i>Save</button><button class="top-button" type="button" onclick="renderDoc(state.currentDoc)"><i data-lucide="x"></i>Cancel</button><span class="edit-status" id="docEditStatus"></span></div></form>`;
      if (window.lucide) window.lucide.createIcons();
    }
    async function saveDocEdit(event) {
      event.preventDefault();
      const status = $('docEditStatus');
      status.className = 'edit-status';
      status.textContent = 'Saving...';
      try {
        const payload = {
          expected_updated_at: $('docExpectedUpdatedAt').value,
          title: $('docTitle').value,
          body: $('docBody').value
        };
        const data = await patchJson('/api/docs/' + encodeURIComponent(state.currentDoc.id), payload);
        state.currentDoc = data;
        renderDoc(data);
      } catch (err) {
        status.className = 'edit-status error';
        status.textContent = err.message || String(err);
      }
    }
    async function loadRun(id) {
      await ensureMocs();
      const r = await api('/api/runs/' + encodeURIComponent(id));
      const sameRun = Boolean(state.currentRun && state.currentRun.id === r.id);
      if (!sameRun) {
        state.wandbChartData = null;
        state.wandbCompareData = null;
        state.wandbCompareMode = 'intersection';
        state.tensorboardChartData = null;
      }
      state.currentRun = r;
      state.currentDoc = null;
      renderRun(r, sameRun);
    }
    async function loadDoc(id) {
      await ensureMocs();
      const d = await api('/api/docs/' + encodeURIComponent(id));
      state.currentDoc = d;
      state.currentRun = null;
      renderDoc(d);
    }
    async function loadDocCharts(docId) {
      const charts = Array.from(document.querySelectorAll('.doc-chart[data-chart-id]'));
      for (const chart of charts) {
        await renderDocChart(docId, chart, false);
      }
    }
    async function renderDocChart(docId, chart, refresh) {
      const chartId = chart.dataset.chartId;
      const body = chart.querySelector('.doc-chart-body') || chart;
      const method = refresh ? 'POST' : 'GET';
      const suffix = refresh ? '/refresh' : '';
      body.innerHTML = '<span class="muted">Loading chart...</span>';
      try {
        const data = await api('/api/docs/' + encodeURIComponent(docId) + '/charts/' + encodeURIComponent(chartId) + suffix, { method });
        renderDocChartPayload(docId, chart, data);
      } catch (err) {
        body.innerHTML = `<div class="doc-chart-error">${esc(err.message || err)}</div>`;
      }
    }
    function renderDocChartPayload(docId, chart, data) {
      const chartId = chart.dataset.chartId;
      const title = esc(data.title || chartId);
      const refresh = data.type === 'python' ? `<button class="top-button" type="button" data-doc-id="${esc(docId)}" onclick="renderDocChart(this.dataset.docId, this.closest('.doc-chart'), true)">Refresh</button>` : '';
      chart.innerHTML = `<div class="doc-chart-header"><div class="doc-chart-title">${title}</div><div>${refresh}</div></div><div class="doc-chart-body"></div>`;
      const body = chart.querySelector('.doc-chart-body');
      if (!data.available) {
        const details = data.details ? `\n${data.details}` : '';
        body.innerHTML = `<div class="doc-chart-error">${esc(data.reason)}: ${esc(data.message)}${esc(details)}</div>`;
        return;
      }
      if (data.plotly && window.Plotly) {
        const targetId = 'doc-chart-' + chartId.replace(/[^A-Za-z0-9_-]/g, '-') + '-' + Math.random().toString(16).slice(2);
        body.innerHTML = `<div class="doc-chart-plot" id="${targetId}"></div>${docChartMeta(data)}`;
        const layout = Object.assign({}, data.plotly.layout || {});
        const config = Object.assign({ responsive: true, displaylogo: false }, data.plotly.config || {});
        Plotly.newPlot(targetId, data.plotly.data || [], layout, config);
        return;
      }
      if (data.png_url) {
        const plotlyError = data.plotly_error ? `<p class="doc-chart-error">${esc(data.plotly_error.reason)}: ${esc(data.plotly_error.message)}</p>` : '';
        body.innerHTML = `${plotlyError}<img class="doc-chart-image" src="${esc(data.png_url)}" alt="${title}">${docChartMeta(data)}`;
        return;
      }
      body.innerHTML = '<div class="doc-chart-error">No renderable chart output.</div>';
    }
    function docChartMeta(data) {
      if (data.original_points === undefined) return '';
      return `<p class="muted">${esc(data.returned_points)} of ${esc(data.original_points)} points shown.</p>`;
    }
    async function loadBenchmarks() {
      const benchmarks = await api('/api/benchmarks');
      $('view').innerHTML = `${hero('All benchmarks', 'Task x algo reproduction matrices with linked, conclusive runs.', `<div class="stats"><div class="stat"><span class="muted">Benchmarks</span><strong>${benchmarks.length}</strong></div></div>`)}${benchmarkTable(benchmarks)}`;
    }
    function benchmarkTable(rows) {
      if (!rows.length) return empty('No benchmarks yet.');
      return `<div class="table-wrap"><table data-table="benchmarks"><colgroup><col class="col-doc"><col class="col-updated"></colgroup><thead><tr><th>benchmark</th><th>updated</th></tr></thead><tbody>${rows.map(b => `<tr><td data-cell="doc"><button class="link-button" onclick="navigate('#/benchmark/${encodeURIComponent(b.id)}')"><code>${esc(b.id)}</code></button><br>${esc(b.title)}</td><td data-cell="updated">${esc(b.updated_at || '')}</td></tr>`).join('')}</tbody></table></div>`;
    }
    async function loadBenchmark(id) {
      const b = await api('/api/benchmarks/' + encodeURIComponent(id));
      $('view').innerHTML = `${hero(esc(b.title), `<code>${esc(b.id)}</code>`)}${renderBenchmarkMatrix(b)}`;
    }
    function renderBenchmarkMatrix(b) {
      const tasks = b.tasks || [], algos = b.algos || [];
      if (!tasks.length || !algos.length) return empty('No tasks/algos recorded yet.');
      const cellMap = {};
      (b.cells || []).forEach(c => { cellMap[c.task_id + '::' + c.algo_id] = c; });
      const header = '<th></th>' + algos.map(a => `<th>${esc(a.title)}</th>`).join('');
      const rows = tasks.map(t => {
        const cells = algos.map(a => {
          const c = cellMap[t.id + '::' + a.id];
          if (!c) return `<td data-cell="matrix"><span class="muted">—</span></td>`;
          return `<td data-cell="matrix"><button class="link-button" onclick="navigate('#/run/${encodeURIComponent(c.run_id)}')"><code>${esc(c.run_id)}</code></button></td>`;
        }).join('');
        return `<tr><th data-cell="task">${esc(t.title)}</th>${cells}</tr>`;
      }).join('');
      return `<div class="table-wrap"><table data-table="benchmark-matrix"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
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
        if (parts[0] === 'benchmark' && parts[1]) return await loadBenchmark(decodeURIComponent(parts[1]));
        if (parts[0] === 'benchmarks') return await loadBenchmarks();
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
    $('search').onchange = () => {
      const r = route();
      if (r === '#/docs') return loadDocs();
      if (r === '#/benchmarks' || r.startsWith('#/benchmark/')) return;
      return loadRuns();
    };
    $('status').onchange = loadRuns;
    window.addEventListener('hashchange', renderRoute);
    window.addEventListener('popstate', renderRoute);
    if (!window.location.hash) window.location.hash = '#/';
    renderRoute();
  </script>
</body>
</html>
"""
