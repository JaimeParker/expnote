from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app

runner = CliRunner()


def _setup_workspace(
    tmp_path: Path,
    notes_dir: str = "ManiSkill Training",
    moc_path: str = "ManiSkill Training MOC.md",
) -> None:
    assert (
        runner.invoke(
            app,
            [
                "init",
                "--root",
                str(tmp_path),
                "--notes-dir",
                notes_dir,
                "--moc-path",
                moc_path,
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["topic", "add", "topic", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )


def _add_run(
    tmp_path: Path,
    run_id: str = "wandb123",
    purpose: str = "purpose",
) -> None:
    assert (
        runner.invoke(
            app,
            [
                "run",
                "add",
                "--root",
                str(tmp_path),
                "--topic",
                "topic",
                "--run-id",
                run_id,
                "--purpose",
                purpose,
            ],
        ).exit_code
        == 0
    )


def test_markdown_sync_is_idempotent_and_preserves_run_note_user_content(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path)
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )

    note = tmp_path / "ManiSkill Training" / "wandb123.md"
    note.write_text(
        note.read_text(encoding="utf-8") + "\nmanual analysis\n",
        encoding="utf-8",
    )

    first = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )
    second = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")

    assert first == second
    assert "[[wandb123]]" in second
    assert "manual analysis" in note.read_text(encoding="utf-8")


def test_markdown_sync_preserves_moc_user_content_outside_managed_block(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path)
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )

    moc = tmp_path / "ManiSkill Training MOC.md"
    moc.write_text(
        moc.read_text(encoding="utf-8") + "\nmanual moc note\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )

    content = moc.read_text(encoding="utf-8")
    assert "[[wandb123]]" in content
    assert "manual moc note" in content


def test_markdown_table_cells_escape_pipes_and_newlines(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, purpose="compare a|b\nsecond line")
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )

    moc = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert "compare a\\|b<br>second line" in moc


def test_markdown_sync_supports_custom_chinese_paths(tmp_path):
    _setup_workspace(
        tmp_path,
        notes_dir="10 Projects/实验 记录",
        moc_path="10 Projects/实验 MOC.md",
    )
    _add_run(tmp_path, run_id="cn123")
    result = runner.invoke(
        app, ["sync", "markdown", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["run_notes"] == 1
    assert (tmp_path / "10 Projects" / "实验 MOC.md").exists()
    assert (tmp_path / "10 Projects" / "实验 记录" / "cn123.md").exists()


def test_markdown_sync_omits_soft_deleted_runs_from_moc(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, run_id="deleted")
    _add_run(tmp_path, run_id="active")

    result = runner.invoke(
        app, ["run", "delete", "deleted", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert (
        runner.invoke(app, ["sync", "markdown", "--root", str(tmp_path)]).exit_code
        == 0
    )

    moc = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert "[[active]]" in moc
    assert "[[deleted]]" not in moc
