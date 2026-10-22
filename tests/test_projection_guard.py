from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from expnote.cli import app
from expnote.markdown import run_note_filename, sync_markdown
from expnote.research.registry import (
    ProjectEntry,
    Registry,
    registry_path,
    save_registry,
)
from expnote.workspace import ProjectionDisabled

runner = CliRunner()


def _flat(output: str) -> str:
    return re.sub(r"[│╭╮╰╯─\s]+", " ", output)


def _register_research_vault(vault: Path, alternates: list[str] | None = None) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    entry = ProjectEntry(
        id="proj",
        name="proj",
        vault_id="v1",
        vault_root=str(vault),
        project_dir=str(vault / "project"),
        images_dir=str(vault / "images"),
        profile_note=str(vault / "project" / "profile.md"),
        vault_root_alternates=alternates or [],
    )
    save_registry(Registry(active_project="proj", projects={"proj": entry}))


def _invoke(*args: str):
    return runner.invoke(app, [*args])


def _init_workspace(state: Path, obsidian_root: Path) -> None:
    result = _invoke(
        "init",
        "--workspace-dir",
        str(state),
        "--obsidian-root",
        str(obsidian_root),
    )
    assert result.exit_code == 0, result.output
    result = _invoke("topic", "add", "topic", "--workspace-dir", str(state))
    assert result.exit_code == 0, result.output


def _add_run(state: Path, run_id: str) -> None:
    result = _invoke(
        "run",
        "add",
        "--workspace-dir",
        str(state),
        "--topic",
        "topic",
        "--run-id",
        run_id,
        "--purpose",
        f"purpose {run_id}",
        "--status",
        "running",
        "--json",
    )
    assert result.exit_code == 0, result.output


def _markdown_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md"))


@pytest.fixture
def research_workspace(tmp_path):
    """A workspace created before its obsidian_root became a research vault."""
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    vault.mkdir()
    _init_workspace(state, vault)
    _add_run(state, "run1")
    _register_research_vault(vault)
    return vault, state


def test_init_refuses_obsidian_root_inside_research_vault(tmp_path):
    vault = tmp_path / "vault"
    _register_research_vault(vault)
    for root in (vault, vault / "sub", tmp_path):
        result = _invoke(
            "init",
            "--workspace-dir",
            str(tmp_path / f"state-{root.name}"),
            "--obsidian-root",
            str(root),
        )
        assert result.exit_code == 2, result.output
        assert "disabled" in _flat(result.output)
        assert "expnote web" in _flat(result.output)
        assert not (tmp_path / f"state-{root.name}").exists()
    assert _markdown_files(vault) == []
    assert not (vault / "notes").exists()


def test_init_refuses_alternate_vault_root(tmp_path):
    vault = tmp_path / "vault"
    alternate = tmp_path / "mirror"
    alternate.mkdir()
    _register_research_vault(vault, alternates=[str(alternate)])
    result = _invoke(
        "init",
        "--workspace-dir",
        str(tmp_path / "state"),
        "--obsidian-root",
        str(alternate / "sub"),
    )
    assert result.exit_code == 2, result.output
    assert "disabled" in _flat(result.output)


def test_init_without_obsidian_root_and_unrelated_root_still_work(tmp_path):
    _register_research_vault(tmp_path / "vault")
    result = _invoke("init", "--workspace-dir", str(tmp_path / "web-only"))
    assert result.exit_code == 0, result.output
    other = tmp_path / "other"
    result = _invoke(
        "init",
        "--workspace-dir",
        str(tmp_path / "state"),
        "--obsidian-root",
        str(other),
    )
    assert result.exit_code == 0, result.output
    assert (other / "notes" / "runs").is_dir()


def test_run_add_records_in_sqlite_and_writes_no_markdown(research_workspace):
    vault, state = research_workspace
    _add_run(state, "run2")
    result = _invoke("run", "query", "--workspace-dir", str(state), "--json")
    assert result.exit_code == 0, result.output
    assert {row["id"] for row in json.loads(result.output)} == {"run1", "run2"}
    assert _markdown_files(vault) == []


@pytest.mark.parametrize(
    "command",
    [["sync", "markdown"], ["sync", "all"], ["markdown", "sync"]],
)
def test_sync_commands_refuse_research_vault(research_workspace, command):
    vault, state = research_workspace
    result = _invoke(*command, "--workspace-dir", str(state))
    assert result.exit_code != 0, result.output
    assert (
        f"Markdown projection into research vault {vault} is disabled"
        in _flat(result.output)
    )
    assert "expnote web" in _flat(result.output)
    assert _markdown_files(vault) == []


def test_curated_moc_commands_refuse_and_record_nothing(research_workspace):
    vault, state = research_workspace
    result = _invoke(
        "markdown",
        "table",
        "add",
        "run1",
        "--workspace-dir",
        str(state),
        "--moc-path",
        "Inbox/MOC.md",
        "--section",
        "Section",
    )
    assert result.exit_code != 0, result.output
    assert "disabled" in _flat(result.output)
    assert _markdown_files(vault) == []
    result = _invoke(
        "markdown",
        "table",
        "sections",
        "--workspace-dir",
        str(state),
        "--moc-path",
        "Inbox/MOC.md",
        "--json",
    )
    assert result.exit_code != 0, result.output


def test_library_projection_functions_refuse_research_vault(research_workspace):
    vault, state = research_workspace
    with pytest.raises(ProjectionDisabled, match="research vault"):
        sync_markdown(vault, state_dir=state)
    assert _markdown_files(vault) == []


def test_run_rename_keeps_note_in_research_vault_and_warns(research_workspace):
    vault, state = research_workspace
    note = vault / "notes" / "runs" / run_note_filename("run1")
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("hand-written note", encoding="utf-8")
    result = _invoke(
        "run", "rename", "run1", "run1b", "--workspace-dir", str(state), "--json"
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["stale_note_removed"] is None
    assert any("research vault" in warning for warning in data["warnings"])
    assert note.read_text(encoding="utf-8") == "hand-written note"
    result = _invoke("run", "query", "--workspace-dir", str(state), "--json")
    assert [row["id"] for row in json.loads(result.output)] == ["run1b"]


def test_validate_reports_disabled_projection_as_informational(research_workspace):
    _, state = research_workspace
    result = _invoke("validate", "--workspace-dir", str(state), "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert "research vault" in data["projection_disabled"]


def test_projection_off_config_refuses_projection(tmp_path):
    state = tmp_path / "state"
    root = tmp_path / "plain-vault"
    _init_workspace(state, root)
    _add_run(state, "run1")
    config = state / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + 'projection = "off"\n', encoding="utf-8"
    )
    for command in (["sync", "all"], ["sync", "markdown"], ["markdown", "sync"]):
        result = _invoke(*command, "--workspace-dir", str(state))
        assert result.exit_code != 0, result.output
        assert 'projection = "off"' in _flat(result.output)
    assert _markdown_files(root) == []
    result = _invoke("validate", "--workspace-dir", str(state), "--json")
    data = json.loads(result.output)
    assert data["ok"] is True
    assert 'projection = "off"' in data["projection_disabled"]


def test_unrelated_obsidian_root_projects_as_before(tmp_path):
    _register_research_vault(tmp_path / "vault")
    root = tmp_path / "plain"
    state = tmp_path / "state"
    _init_workspace(state, root)
    _add_run(state, "run1")
    result = _invoke("sync", "all", "--workspace-dir", str(state), "--json")
    assert result.exit_code == 0, result.output
    assert (root / "notes" / "runs" / run_note_filename("run1")).exists()
    result = _invoke("validate", "--workspace-dir", str(state), "--json")
    assert "projection_disabled" not in json.loads(result.output)


@pytest.mark.parametrize("registry_text", [None, "not [valid toml", "[projects.x]\n"])
def test_missing_or_broken_registry_does_not_block_projection(tmp_path, registry_text):
    if registry_text is not None:
        registry_path().parent.mkdir(parents=True, exist_ok=True)
        registry_path().write_text(registry_text, encoding="utf-8")
    root = tmp_path / "plain"
    state = tmp_path / "state"
    _init_workspace(state, root)
    _add_run(state, "run1")
    result = _invoke("sync", "all", "--workspace-dir", str(state))
    assert result.exit_code == 0, result.output
    assert (root / "notes" / "runs" / run_note_filename("run1")).exists()


def _pack_workspace(tmp_path: Path, root: Path) -> Path:
    state = tmp_path / "source-state"
    _init_workspace(state, root)
    archive = tmp_path / "workspace.tar.gz"
    result = _invoke(
        "workspace", "pack", str(archive), "--workspace-dir", str(state)
    )
    assert result.exit_code == 0, result.output
    return archive


def test_workspace_unpack_refuses_research_vault_root(tmp_path):
    archive = _pack_workspace(tmp_path, tmp_path / "plain")
    vault = tmp_path / "vault"
    _register_research_vault(vault)
    target = tmp_path / "target-state"
    result = _invoke(
        "workspace",
        "unpack",
        str(archive),
        "--workspace-dir",
        str(target),
        "--obsidian-root",
        str(vault / "sub"),
    )
    assert result.exit_code == 2, result.output
    assert "disabled" in _flat(result.output)
    assert not target.exists()


def test_workspace_unpack_refuses_archive_carried_research_vault_root(tmp_path):
    vault = tmp_path / "vault"
    archive = _pack_workspace(tmp_path, vault)
    _register_research_vault(vault)
    target = tmp_path / "target-state"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    result = _invoke(
        "workspace",
        "unpack",
        str(archive),
        "--workspace-dir",
        str(target),
        "--replace",
    )
    assert result.exit_code == 2, result.output
    assert "disabled" in _flat(result.output)
    assert (target / "keep.txt").exists()
    result = _invoke(
        "workspace",
        "unpack",
        str(archive),
        "--workspace-dir",
        str(target),
        "--replace",
        "--no-obsidian",
    )
    assert result.exit_code == 0, result.output
