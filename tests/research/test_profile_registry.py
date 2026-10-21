from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from minivault import PROFILE_FIXTURE

from expnote.research.errors import ResearchError
from expnote.research.profile import parse_profile_text, read_profile_note
from expnote.research.registry import (
    ProjectEntry,
    Registry,
    convert_drive_path,
    dump_registry,
    is_inside,
    load_registry,
    rebase,
    registry_path,
    revalidate,
    save_registry,
)


def fixture_text() -> str:
    return PROFILE_FIXTURE.read_text(encoding="utf-8")


def test_fixture_profile_parses_with_every_key():
    note = read_profile_note(PROFILE_FIXTURE)
    assert note.problems == [] and note.parsed
    profile = note.profile
    assert profile.project_id == "off2on" and profile.schema == "3"
    assert profile.decision_strict_from == 123
    assert profile.label_value_columns == (1,)
    assert len(profile.legacy_status_rules) == 6
    assert profile.legacy_status_rules[0].review
    assert profile.deviation_columns["found"] == "发现时点"
    assert profile.unknown_keys == ()
    assert "templates" in profile.template_dirs


def test_profile_missing_required_keys_lists_them():
    text = fixture_text().replace('  rules: "规则摘要（须确认）"\n', "")
    note = parse_profile_text(text)
    assert not note.parsed
    assert any("headings.rules" in p for p in note.problems)


def test_profile_wrong_type_and_bad_regex():
    text = fixture_text().replace('strict_from: "123"', "strict_from: abc")
    assert any("decisions.strict_from" in p for p in parse_profile_text(text).problems)
    text = fixture_text().replace("id_regex: 'D-\\d+'", "id_regex: '('")
    assert any("invalid regex" in p for p in parse_profile_text(text).problems)


def test_profile_wrong_note_type_and_no_frontmatter():
    assert parse_profile_text("plain").problems
    assert any(
        "type must be" in p
        for p in parse_profile_text(
            fixture_text().replace("type: project-profile", "type: plan")
        ).problems
    )


def test_profile_reports_unknown_keys_without_failing():
    text = fixture_text().replace("prose:", "extra_section:\n  x: y\nprose:")
    note = parse_profile_text(text)
    assert note.parsed and "extra_section.x" in note.profile.unknown_keys


def test_profile_missing_file(tmp_path):
    note = read_profile_note(tmp_path / "nope.md")
    assert not note.found and not note.parsed


# ---- registry -----------------------------------------------------------------


def entry(root: Path, **overrides) -> ProjectEntry:
    values = dict(
        id="off2on",
        name="New Off2On RL",
        vault_id="vid",
        vault_root=str(root),
        project_dir=str(root / "10 Projects" / "P"),
        images_dir=str(root / "40 Resources" / "img"),
        profile_note=str(root / "10 Projects" / "P" / "expnote 项目配置.md"),
    )
    values.update(overrides)
    return ProjectEntry(**values)


def make_vault(root: Path) -> None:
    (root / ".obsidian").mkdir(parents=True)
    project = root / "10 Projects" / "P"
    project.mkdir(parents=True)
    shutil.copy(PROFILE_FIXTURE, project / "expnote 项目配置.md")


def test_registry_round_trip_is_deterministic(tmp_path):
    registry = Registry("off2on")
    registry.projects["off2on"] = entry(
        tmp_path, workspaces=["w1", "w2"], vault_root_alternates=["D:/x"]
    )
    registry.projects["a"] = entry(tmp_path, id="a", name='quote " and \\ slash')
    first = dump_registry(registry)
    path = save_registry(registry)
    assert path == registry_path() and path.read_text(encoding="utf-8") == first
    loaded = load_registry()
    assert loaded.active_project == "off2on"
    assert loaded.projects["a"].name == 'quote " and \\ slash'
    assert loaded.projects["off2on"].workspaces == ["w1", "w2"]
    assert dump_registry(loaded) == first
    assert first.index('[projects."a"]') < first.index('[projects."off2on"]')


def test_registry_path_respects_test_isolation(tmp_path):
    assert str(registry_path()).startswith(str(tmp_path))


def test_registry_invalid_file_is_environment_error(tmp_path):
    registry_path().parent.mkdir(parents=True)
    registry_path().write_text("not = [valid", encoding="utf-8")
    with pytest.raises(ResearchError) as caught:
        load_registry()
    assert caught.value.code == "REGISTRY_INVALID" and caught.value.exit_code == 6


def test_empty_registry_when_file_missing():
    assert load_registry().projects == {}


@pytest.mark.parametrize(
    "path,expected",
    [
        ("D:/Documents/Cyber Brain", "/mnt/d/Documents/Cyber Brain"),
        ("D:\\Documents\\Cyber Brain", "/mnt/d/Documents/Cyber Brain"),
        ("/mnt/d/Documents/Cyber Brain", "D:/Documents/Cyber Brain"),
        ("/mnt/d", "D:/"),
        ("/home/user/vault", None),
        ("/mnt/data/vault", None),
    ],
)
def test_drive_path_conversion(path, expected):
    assert convert_drive_path(path) == expected


def test_rebase_rewrites_prefix_and_rejects_outsiders():
    assert rebase("/a/b/c", "/a/b", "/x") == "/x/c"
    assert rebase("/a/b", "/a/b", "/x") == "/x"
    with pytest.raises(ResearchError) as caught:
        rebase("/other/c", "/a/b", "/x")
    assert caught.value.code == "USAGE_PATH_OUTSIDE_VAULT"


def test_is_inside_does_not_confuse_prefix_siblings(tmp_path):
    assert is_inside(str(tmp_path / "v" / "p"), str(tmp_path / "v"))
    assert not is_inside(str(tmp_path / "vault2"), str(tmp_path / "vault"))


def test_revalidate_ok_and_unavailable(tmp_path):
    root = tmp_path / "vault"
    make_vault(root)
    resolved = revalidate(entry(root))
    assert resolved.path_note is None and resolved.project_dir.is_dir()
    gone = entry(tmp_path / "missing")
    with pytest.raises(ResearchError) as caught:
        revalidate(gone)
    assert caught.value.code == "VAULT_UNAVAILABLE" and caught.value.exit_code == 6
    assert "project rebind off2on" in caught.value.remediation


def test_revalidate_falls_back_to_alternate_root(tmp_path):
    root = tmp_path / "vault"
    make_vault(root)
    stale = tmp_path / "stale"
    moved = entry(
        stale,
        project_dir=str(stale / "10 Projects" / "P"),
        images_dir=str(stale / "40 Resources" / "img"),
        profile_note=str(stale / "10 Projects" / "P" / "expnote 项目配置.md"),
        vault_root_alternates=[str(root)],
    )
    resolved = revalidate(moved)
    assert resolved.vault_root == root
    assert resolved.path_note and str(root) in resolved.path_note
    assert resolved.profile_note == root / "10 Projects" / "P" / "expnote 项目配置.md"
