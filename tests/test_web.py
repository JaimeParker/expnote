from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app as cli_app
from expnote.db import transaction
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
    assert runner.invoke(cli_app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(
            cli_app,
            [
                "moc",
                "add",
                "--root",
                str(tmp_path),
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
                "--root",
                str(tmp_path),
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
                "--root",
                str(tmp_path),
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
                "--root",
                str(tmp_path),
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
                "--root",
                str(tmp_path),
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
    assert "window.addEventListener('hashchange', renderRoute)" in _INDEX_HTML
    assert "history.back()" in _INDEX_HTML
    assert "#/topic/" in _INDEX_HTML
    assert "#/run/" in _INDEX_HTML
    assert "#/doc/" in _INDEX_HTML
    assert 'table data-table="topic-runs"' in _INDEX_HTML
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
    doc_run_table_start = _INDEX_HTML.index('table data-table="doc-runs"')
    doc_run_table_end = _INDEX_HTML.index("</table>", doc_run_table_start)
    assert "topic_title" not in _INDEX_HTML[doc_run_table_start:doc_run_table_end]


def test_web_index_formats_run_detail_as_reading_sections():
    assert 'class="run-sections"' in _INDEX_HTML
    assert "detail-grid" not in _INDEX_HTML
    assert '<ul class="metadata-list">' in _INDEX_HTML
    assert "key === 'wandb_url'" in _INDEX_HTML
    assert 'target="_blank" rel="noreferrer"' in _INDEX_HTML
