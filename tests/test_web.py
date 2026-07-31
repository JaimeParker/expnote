from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app as cli_app
from expnote.db import transaction
from expnote.wandb_live import (
    WandbLiveError,
    fetch_wandb_charts,
    group_wandb_history,
    parse_wandb_run_url,
)
from expnote.web import (
    _INDEX_HTML,
    _active_run_ids,
    _doc_runs,
    _docs,
    _runs,
    _topics,
    create_app,
    render_markdown,
)

runner = CliRunner()


def _workspace(tmp_path: Path) -> None:
    assert (
        runner.invoke(
            cli_app, ["init", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli_app,
            [
                "moc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "baseline",
                "--title",
                "Baseline MOC",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli_app,
            [
                "topic",
                "add",
                "CalQL",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "baseline",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "baseline",
                "--topic",
                "CalQL",
                "--run-id",
                "wandb123",
                "--purpose",
                "Train baseline",
                "--result",
                "Running",
                "--analysis",
                "## Finding\n\n```text\nstable\n```",
                "--metadata-json",
                '{"seed":1}',
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli_app,
            [
                "doc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--doc-id",
                "summary",
                "--moc-id",
                "baseline",
                "--title",
                "Summary",
                "--run-id",
                "wandb123",
                "--body",
                "Cross-run note",
            ],
        ).exit_code
        == 0
    )


def test_web_queries_expose_sql_moc_and_run_detail(tmp_path):
    _workspace(tmp_path)
    with transaction(tmp_path) as conn:
        topics = _topics(conn, "baseline")
        docs = _docs(conn, moc_id="baseline")
        runs = _runs(conn, moc_id="baseline")
        doc_runs = _doc_runs(conn, "summary")

    assert topics[0]["title"] == "CalQL"
    assert docs[0]["id"] == "summary"
    assert runs[0]["id"] == "wandb123"
    assert runs[0]["moc_id"] == "baseline"
    assert runs[0]["topic_title"] == "CalQL"
    assert runs[0]["metadata"] == {"seed": 1}
    assert doc_runs[0]["run_id"] == "wandb123"
    assert doc_runs[0]["relation"] == ""


def test_web_markdown_renderer_escapes_raw_html():
    rendered = render_markdown("<script>alert(1)</script>\n\n**ok**")

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>ok</strong>" in rendered


def test_web_markdown_renderer_links_active_run_ids():
    rendered = render_markdown(
        "compare [[run1]] with run2, [[missing]], `run1`, and /tmp/run2",
        {"run1", "run2"},
    )

    assert '<a href="#/run/run1" data-run-link="run1">run1</a>' in rendered
    assert '<a href="#/run/run2" data-run-link="run2">run2</a>' in rendered
    assert "<code>run1</code>" in rendered
    assert "/tmp/run2" in rendered
    assert "[[missing]]" not in rendered
    assert "missing" in rendered


def test_web_queries_include_linked_text_html(tmp_path):
    _workspace(tmp_path)
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "update",
                "wandb123",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--purpose",
                "compare wandb123",
                "--relation",
                "see [[missing]] and wandb123",
                "--analysis",
                "analysis wandb123",
            ],
        ).exit_code
        == 0
    )
    with transaction(tmp_path) as conn:
        active_run_ids = _active_run_ids(conn)
        runs = _runs(conn, moc_id="baseline", active_run_ids=active_run_ids)

    assert 'href="#/run/wandb123"' in runs[0]["purpose_html"]
    assert "missing" in runs[0]["relation_html"]
    assert "[[missing]]" not in runs[0]["relation_html"]


def test_web_index_disables_browser_cache(tmp_path):
    app = create_app(tmp_path)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/")

    response = route.endpoint()

    assert response.headers["Cache-Control"] == "no-store"


def test_web_index_has_routes_and_topic_table_without_metadata():
    assert "https://unpkg.com/lucide" in _INDEX_HTML
    assert "window.lucide.createIcons" in _INDEX_HTML
    assert "window.addEventListener('hashchange', renderRoute)" in _INDEX_HTML
    assert "history.back()" in _INDEX_HTML
    assert "#/topic/" in _INDEX_HTML
    assert "#/run/" in _INDEX_HTML
    assert "#/doc/" in _INDEX_HTML
    assert "MOC overview" in _INDEX_HTML
    assert "All runs" in _INDEX_HTML
    assert "All docs" in _INDEX_HTML
    assert 'data-lucide="layout-dashboard"' in _INDEX_HTML
    assert 'data-lucide="arrow-left"' in _INDEX_HTML
    assert 'table data-table="topic-runs"' in _INDEX_HTML
    assert '<col class="col-purpose">' in _INDEX_HTML
    assert '<col class="col-relation">' in _INDEX_HTML
    assert '<col class="col-result">' in _INDEX_HTML
    assert 'data-cell="purpose"' in _INDEX_HTML
    assert 'data-cell="relation"' in _INDEX_HTML
    assert "table-layout: auto" in _INDEX_HTML
    assert "nth-child" not in _INDEX_HTML
    assert "<th>relation</th>" in _INDEX_HTML
    topic_table_start = _INDEX_HTML.index('table data-table="topic-runs"')
    topic_table_end = _INDEX_HTML.index("</table>", topic_table_start)
    assert "metadata" not in _INDEX_HTML[topic_table_start:topic_table_end]
    assert "topic_title" not in _INDEX_HTML[topic_table_start:topic_table_end]
    assert 'table data-table="doc-runs"' in _INDEX_HTML
    assert "r.run_id" in _INDEX_HTML
    assert "r.purpose_html" in _INDEX_HTML
    assert "r.relation_html" in _INDEX_HTML
    assert "r.result_html" in _INDEX_HTML
    doc_run_func_start = _INDEX_HTML.index("function docRunTable")
    doc_run_func_end = _INDEX_HTML.index("function docTable", doc_run_func_start)
    assert "showRole" in _INDEX_HTML[doc_run_func_start:doc_run_func_end]
    assert "showNote" in _INDEX_HTML[doc_run_func_start:doc_run_func_end]
    assert '<col class="col-note">' in _INDEX_HTML[doc_run_func_start:doc_run_func_end]
    assert '<col class="col-role">' in _INDEX_HTML[doc_run_func_start:doc_run_func_end]
    doc_run_table_start = _INDEX_HTML.index('table data-table="doc-runs"')
    doc_run_table_end = _INDEX_HTML.index("</table>", doc_run_table_start)
    assert "topic_title" not in _INDEX_HTML[doc_run_table_start:doc_run_table_end]


def test_web_index_formats_run_detail_as_reading_sections():
    assert 'class="run-sections"' in _INDEX_HTML
    assert "detail-grid" not in _INDEX_HTML
    assert '<ul class="metadata-list">' in _INDEX_HTML
    assert "key === 'wandb_url'" in _INDEX_HTML
    assert 'target="_blank" rel="noreferrer"' in _INDEX_HTML


def test_wandb_run_url_parsing_accepts_run_urls():
    ref = parse_wandb_run_url(
        "https://wandb.ai/entity-name/project-name/runs/abc123?workspace=user"
    )

    assert ref.entity == "entity-name"
    assert ref.project == "project-name"
    assert ref.run_id == "abc123"
    assert ref.path == "entity-name/project-name/abc123"


def test_wandb_history_groups_numeric_non_system_metrics():
    groups = group_wandb_history(
        [
            {
                "_step": 1,
                "_timestamp": 10,
                "eval/return": 12.5,
                "train/loss": 0.4,
                "system/gpu.0.memory": 50,
                "notes": "warmup",
            },
            {
                "_step": 2,
                "eval/return": 18.0,
                "train/loss": 0.25,
                "success_rate": 0.5,
            },
        ]
    )

    by_name = {group["name"]: group for group in groups}
    assert set(by_name) == {"eval", "metrics", "train"}
    assert by_name["eval"]["charts"][0]["metric"] == "eval/return"
    assert by_name["eval"]["charts"][0]["x"] == [1.0, 2.0]
    assert by_name["eval"]["charts"][0]["y"] == [12.5, 18.0]
    assert by_name["metrics"]["charts"][0]["metric"] == "success_rate"
    assert all(
        chart["metric"] != "system/gpu.0.memory"
        for group in groups
        for chart in group["charts"]
    )


def test_web_wandb_endpoint_reports_missing_url(tmp_path):
    _workspace(tmp_path)
    app = create_app(tmp_path)
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/runs/{run_id}/wandb"
    )

    response = route.endpoint("wandb123")

    assert response == {
        "available": False,
        "reason": "missing_wandb_url",
        "message": "This run does not have metadata.wandb_url.",
    }


def test_wandb_finished_runs_use_local_cache(tmp_path, monkeypatch):
    calls = 0

    def fake_live(url: str, *, samples: int):
        nonlocal calls
        calls += 1
        return {
            "available": True,
            "cached": False,
            "run_path": "entity/project/run1",
            "samples": samples,
            "groups": [{"name": "eval", "charts": []}],
        }

    monkeypatch.setattr("expnote.wandb_live.fetch_live_wandb_charts", fake_live)
    cache_dir = tmp_path / "cache"

    first = fetch_wandb_charts(
        "https://wandb.ai/entity/project/runs/run1",
        run_id="run1",
        status="finished",
        cache_dir=cache_dir,
    )
    second = fetch_wandb_charts(
        "https://wandb.ai/entity/project/runs/run1",
        run_id="run1",
        status="finished",
        cache_dir=cache_dir,
    )

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls == 1
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_wandb_running_runs_do_not_use_local_cache(tmp_path, monkeypatch):
    calls = 0

    def fake_live(url: str, *, samples: int):
        nonlocal calls
        calls += 1
        return {
            "available": True,
            "cached": False,
            "run_path": f"entity/project/run{calls}",
            "samples": samples,
            "groups": [],
        }

    monkeypatch.setattr("expnote.wandb_live.fetch_live_wandb_charts", fake_live)
    cache_dir = tmp_path / "cache"

    first = fetch_wandb_charts(
        "https://wandb.ai/entity/project/runs/run1",
        run_id="run1",
        status="running",
        cache_dir=cache_dir,
    )
    second = fetch_wandb_charts(
        "https://wandb.ai/entity/project/runs/run1",
        run_id="run1",
        status="running",
        cache_dir=cache_dir,
    )

    assert first["cached"] is False
    assert second["cached"] is False
    assert calls == 2
    assert not cache_dir.exists()


def test_web_wandb_endpoint_returns_live_charts(tmp_path, monkeypatch):
    _workspace(tmp_path)
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "update",
                "wandb123",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--metadata-json",
                '{"wandb_url":"https://wandb.ai/entity/project/runs/wandb123"}',
            ],
        ).exit_code
        == 0
    )

    def fake_fetch(
        url: str, *, run_id: str, status: str, cache_dir: Path, samples: int
    ):
        return {
            "available": True,
            "cached": status == "finished",
            "run_path": "entity/project/wandb123",
            "samples": samples,
            "groups": [{"name": "eval", "charts": []}],
        }

    monkeypatch.setattr("expnote.web.fetch_wandb_charts", fake_fetch)
    app = create_app(tmp_path)
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/runs/{run_id}/wandb"
    )

    response = route.endpoint("wandb123")

    assert response["available"] is True
    assert response["samples"] == 1000
    assert response["cached"] is False
    assert response["groups"][0]["name"] == "eval"


def test_web_wandb_endpoint_returns_api_error(tmp_path, monkeypatch):
    _workspace(tmp_path)
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "update",
                "wandb123",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--metadata-json",
                '{"wandb_url":"https://wandb.ai/entity/project/runs/wandb123"}',
            ],
        ).exit_code
        == 0
    )

    def fake_fetch(
        url: str, *, run_id: str, status: str, cache_dir: Path, samples: int
    ):
        raise WandbLiveError("wandb_api_error", "permission denied")

    monkeypatch.setattr("expnote.web.fetch_wandb_charts", fake_fetch)
    app = create_app(tmp_path)
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/runs/{run_id}/wandb"
    )

    response = route.endpoint("wandb123")

    assert response == {
        "available": False,
        "reason": "wandb_api_error",
        "message": "permission denied",
    }


def test_web_wandb_compare_endpoint_skips_missing_urls(tmp_path):
    _workspace(tmp_path)
    app = create_app(tmp_path)
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/wandb/compare"
    )

    response = route.endpoint(["wandb123", "missing"])

    assert response["runs"] == []
    assert response["skipped"][0]["run_id"] == "wandb123"
    assert response["skipped"][0]["reason"] == "missing_wandb_url"
    assert response["errors"][0]["run_id"] == "missing"
    assert response["errors"][0]["reason"] == "run_not_found"


def test_web_wandb_compare_endpoint_returns_successes_and_errors(tmp_path, monkeypatch):
    _workspace(tmp_path)
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "update",
                "wandb123",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--metadata-json",
                '{"wandb_url":"https://wandb.ai/entity/project/runs/wandb123"}',
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli_app,
            [
                "run",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "baseline",
                "--topic",
                "CalQL",
                "--run-id",
                "badwandb",
                "--purpose",
                "Bad W&B",
                "--metadata-json",
                '{"wandb_url":"https://wandb.ai/entity/project/runs/badwandb"}',
            ],
        ).exit_code
        == 0
    )

    def fake_fetch(
        url: str, *, run_id: str, status: str, cache_dir: Path, samples: int
    ):
        if "badwandb" in url:
            raise WandbLiveError("wandb_api_error", "permission denied")
        return {
            "available": True,
            "cached": False,
            "run_path": "entity/project/wandb123",
            "samples": samples,
            "groups": [{"name": "eval", "charts": [{"metric": "eval/return"}]}],
        }

    monkeypatch.setattr("expnote.web.fetch_wandb_charts", fake_fetch)
    app = create_app(tmp_path)
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/wandb/compare"
    )

    response = route.endpoint(["wandb123", "badwandb"])

    assert response["runs"][0]["id"] == "wandb123"
    assert response["runs"][0]["purpose"] == "Train baseline"
    assert response["runs"][0]["cached"] is False
    assert response["runs"][0]["groups"][0]["name"] == "eval"
    assert response["skipped"] == []
    assert response["errors"][0]["run_id"] == "badwandb"
    assert response["errors"][0]["reason"] == "wandb_api_error"


def test_web_wandb_cache_stats_and_clear(tmp_path):
    state_dir = tmp_path / "state"
    cache_dir = state_dir / "wandb-cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "one.json").write_text("1234", encoding="utf-8")
    app = create_app(tmp_path, state_dir=state_dir)
    stats_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/wandb/cache"
        and "GET" in getattr(route, "methods", set())
    )
    clear_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/wandb/cache"
        and "DELETE" in getattr(route, "methods", set())
    )

    stats = stats_route.endpoint()
    cleared = clear_route.endpoint()

    assert stats == {"files": 1, "bytes": 4}
    assert cleared == {"files": 0, "bytes": 0}
    assert not cache_dir.exists()


def test_web_index_has_wandb_live_chart_controls():
    assert '<script src="/assets/plotly.min.js"></script>' in _INDEX_HTML
    assert "Fetch live W&B charts" in _INDEX_HTML
    assert "Split metrics" in _INDEX_HTML
    assert "Combine group" in _INDEX_HTML
    assert "Compare runs" in _INDEX_HTML
    assert "Show comparison" in _INDEX_HTML
    assert "* 星标为relation中记录的推荐对比实验" in _INDEX_HTML
    assert "[hidden] { display: none !important; }" in _INDEX_HTML
    assert "hidden disabled>Compare runs" in _INDEX_HTML
    assert "$('wandbCharts').innerHTML = ''" in _INDEX_HTML
    assert "data.cached ? 'cached' : 'live'" in _INDEX_HTML
    assert "W&B cache" in _INDEX_HTML
    assert "Clear W&B cache" in _INDEX_HTML
    assert "/api/wandb/cache" in _INDEX_HTML
    assert "method: 'DELETE'" in _INDEX_HTML
    assert "/api/runs/" in _INDEX_HTML
    assert "/wandb" in _INDEX_HTML
    assert "/api/wandb/compare?" in _INDEX_HTML
    assert "Plotly.newPlot" in _INDEX_HTML
    assert "loadWandbCharts" in _INDEX_HTML
    assert "state.wandbChartMode = 'combined'" in _INDEX_HTML
    assert "state.wandbChartData = data" in _INDEX_HTML
    assert "state.wandbCompareMode = 'intersection'" in _INDEX_HTML
    assert "toggleWandbChartMode" in _INDEX_HTML
    assert "openWandbCompareModal" in _INDEX_HTML
    assert "toggleWandbCompareMetricMode" in _INDEX_HTML
    assert "comparisonMetrics" in _INDEX_HTML
    assert "colorForRun" in _INDEX_HTML
    assert "renderCombinedWandbCharts" in _INDEX_HTML
    assert "renderSplitWandbCharts" in _INDEX_HTML
    assert "wandb-chart-grid" in _INDEX_HTML
    assert "modal-backdrop" in _INDEX_HTML
    assert "checkbox" in _INDEX_HTML
    assert "Current" in _INDEX_HTML
    assert "wandb-chart-card" in _INDEX_HTML
    assert "wandb-chart-title" in _INDEX_HTML
    assert "repeat(auto-fit, minmax(320px, 1fr))" in _INDEX_HTML
    assert "wandb-chart-split-${groupIndex}-${chartIndex}" in _INDEX_HTML
    assert "wandb-compare-chart-${index}" in _INDEX_HTML
    assert "state.wandbChartData) return" in _INDEX_HTML
    assert "wandbLayout(chart.metric, false)" not in _INDEX_HTML
