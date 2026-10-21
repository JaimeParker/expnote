from __future__ import annotations

import pytest

from expnote.research import textkit as T


def test_link_target_variants():
    assert T.link_target("a/b/DEC-005--x\\|DEC-005") == "a/b/DEC-005--x"
    assert T.link_target("Note#Heading|alias") == "Note"
    assert T.link_target("Note^block") == "Note^block"  # block refs are not split
    assert T.link_target("Note.md") == "Note"
    assert T.link_target("o2o_x.png") == "o2o_x.png"


def test_iter_links_ignores_code_and_marks_embeds():
    text = "see [[A]] and `[[B]]`\n```\n[[C]]\n```\n![[o2o_d.png]]"
    assert T.iter_links(text) == [(False, "A"), (True, "o2o_d.png")]


def test_iter_links_comment_stripping_is_opt_in():
    text = "<!-- [[Hidden]] --> [[Shown]]"
    assert [t for _, t in T.iter_links(text)] == ["Hidden", "Shown"]
    assert [t for _, t in T.iter_links(text, comments=True)] == ["Shown"]


def test_link_stem_keeps_last_path_segment():
    assert T.link_stem("plans/归档/PLAN-1") == "PLAN-1"


def test_fold_is_nfc_and_casefold():
    assert T.fold("Cafe\u0301") == T.fold("Caf\u00e9") == "caf\u00e9"
    assert T.fold("ABC") == "abc"


def test_normalize_text_strips_bom_and_crlf():
    assert T.normalize_text("\ufeffa\r\nb\rc") == "a\nb\nc"


def test_split_table_row_handles_escaped_pipes():
    cells = T.split_table_row("| 1 | [[a\\|b]] | x |")
    assert cells == ["1", "[[a\\|b]]", "x"]
    assert T.split_table_row("not a row") is None
    assert T.is_separator_row(T.split_table_row("|---|:--:|"))


def test_markdown_tables_and_rows_by_header():
    text = "intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\ntext\n| x |\n"
    tables = T.markdown_tables(text)
    assert len(tables) == 1
    rows = T.table_rows_by_header(tables[0])
    assert [r.by_header for r in rows] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_sections_stop_at_same_or_higher_level():
    body = "# T\n\n## A\n\nin a\n\n### A1\n\nsub\n\n## B\n\nin b\n"
    assert "sub" in T.section_text(body, lambda t: t == "A")
    assert "in b" not in T.section_text(body, lambda t: t == "A")
    assert T.section_text(body, lambda t: t == "missing") is None


def test_headings_skip_fenced_code():
    assert [h[2] for h in T.headings("# a\n```\n# not\n```\n## b")] == ["a", "b"]


def test_rules_hash_ignores_line_endings_trailing_space_and_blank_edges():
    base = "\n\n- a  \n- b\n\n"
    assert T.hash_section(base) == T.hash_section(base.replace("\n", "\r\n"))
    assert T.hash_section(base) == T.hash_section("- a\n- b")
    assert T.hash_section(base) != T.hash_section("- a\n- c")


def test_reserved_name_chars():
    assert T.reserved_name_chars("EV--x #54 [a].md") == ["#", "[", "]"]
    assert T.reserved_name_chars("plain.md") == []


def test_attachment_name_uses_given_extensions():
    assert T.is_attachment_name("a.PNG", frozenset({"png"}))
    assert not T.is_attachment_name("a.md", frozenset({"png"}))
    assert not T.is_attachment_name("noext", frozenset({"png"}))


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("**/_x/**", "ops/_x/a.md", True),
        ("**/_x/**", "_x/a.md", True),
        ("**/_x/**", "ops/y/a.md", False),
        ("*.md", "a.md", True),
        ("*.md", "d/a.md", False),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert bool(T.glob_to_regex(pattern).match(path)) is expected
