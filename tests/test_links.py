from __future__ import annotations

from expnote.links import render_html_run_links, render_obsidian_run_links


def test_obsidian_links_active_wikilinks_and_bare_ids():
    text = "compare [[run1]] with run2 and missing"

    rendered = render_obsidian_run_links(text, {"run1", "run2"})

    assert rendered == "compare [[run1]] with [[run2]] and missing"


def test_obsidian_does_not_link_deleted_or_missing_wikilinks():
    text = "compare [[deleted]] with active"

    rendered = render_obsidian_run_links(text, {"active"})

    assert rendered == "compare deleted with [[active]]"


def test_run_links_skip_code_urls_and_paths():
    text = (
        "`run1` https://example.test/run1 /tmp/run1 checkpoint-run1.pt "
        "plain run1\n\n```text\nrun1\n```"
    )

    rendered = render_obsidian_run_links(text, {"run1"})

    assert rendered == (
        "`run1` https://example.test/run1 /tmp/run1 checkpoint-run1.pt "
        "plain [[run1]]\n\n```text\nrun1\n```"
    )


def test_bare_links_ignore_short_ids():
    rendered = render_obsidian_run_links("[[a]] a run123", {"a", "run123"})

    assert rendered == "[[a]] a [[run123]]"


def test_html_links_wikilinks_and_bare_ids():
    text = "compare [[run1]] with run2 and [[missing]]"

    rendered = render_html_run_links(text, {"run1", "run2"})

    assert '<a href="#/run/run1" data-run-link="run1">run1</a>' in rendered
    assert '<a href="#/run/run2" data-run-link="run2">run2</a>' in rendered
    assert "[[missing]]" not in rendered
    assert "missing" in rendered
