from __future__ import annotations

import sys
from pathlib import Path

import pytest

from expnote.research.errors import MissingDependency
from expnote.research.frontmatter import (
    FrontmatterError,
    has_nested_map,
    import_yaml,
    load_frontmatter,
)
from expnote.research.model import parse_note


def test_scalars_stay_strings():
    data, _ = load_frontmatter(
        'id: 007\nflag: no\nd: 2026-10-21\nhex: 0x1f\nrun: "0012"'
    )
    assert data == {
        "id": "007",
        "flag": "no",
        "d": "2026-10-21",
        "hex": "0x1f",
        "run": "0012",
    }


def test_duplicate_key_is_a_typed_error():
    with pytest.raises(FrontmatterError) as caught:
        load_frontmatter("a: 1\nb: 2\na: 3")
    assert caught.value.kind == "duplicate_key"


def test_nested_duplicate_key_also_rejected():
    with pytest.raises(FrontmatterError, match="duplicate key"):
        load_frontmatter("outer:\n  a: 1\n  a: 2")


def test_fullwidth_colon_and_colon_in_value_refused():
    note = parse_note(Path("x.md"), "x.md", "---\nstatus：draft\nk: 结论: A\n---\nbody")
    assert note.fm_error and "YAML parse failure" in note.fm_error
    assert note.fm_error_kind == "parse"


def test_unterminated_frontmatter_refused():
    note = parse_note(Path("x.md"), "x.md", "---\na: 1\nbody")
    assert note.fm_error and "not closed" in note.fm_error


def test_crlf_and_bom_frontmatter():
    note = parse_note(
        Path("x.md"), "x.md", "\ufeff---\r\ntype: plan\r\n---\r\nbody\r\n"
    )
    assert note.fm_error is None and note.type == "plan" and note.body == "body\n"


def test_note_without_frontmatter():
    note = parse_note(Path("x.md"), "x.md", "just text")
    assert note.has_fm is False and note.fm == {} and note.body == "just text"


def test_nested_map_detection_includes_list_of_maps():
    flat, _ = load_frontmatter("a: 1\nb: [x, y]")
    nested, _ = load_frontmatter("a:\n  b: 1")
    listed, _ = load_frontmatter("a:\n  - k: v")
    assert not has_nested_map(flat)
    assert has_nested_map(nested) and has_nested_map(listed)


def test_quoting_style_is_visible_through_nodes():
    from expnote.research.frontmatter import scalar_is_quoted

    _, nodes = load_frontmatter('a: "x"\nb: y')
    assert scalar_is_quoted(nodes["a"]) and not scalar_is_quoted(nodes["b"])


def test_missing_pyyaml_is_an_environment_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(MissingDependency) as caught:
        import_yaml()
    assert caught.value.code == "ENV_MISSING_DEPENDENCY" and caught.value.exit_code == 6
