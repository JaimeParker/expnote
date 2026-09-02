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
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--obsidian-root",
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
            app,
            ["topic", "add", "topic", "--workspace-dir", str(tmp_path / ".expnote")],
        ).exit_code
        == 0
    )


def _add_run(
    tmp_path: Path,
    run_id: str = "wandb123",
    purpose: str = "purpose",
    relation: str = "",
    result: str = "",
    analysis: str | None = None,
) -> None:
    args = [
        "run",
        "add",
        "--workspace-dir",
        str(tmp_path / ".expnote"),
        "--topic",
        "topic",
        "--run-id",
        run_id,
        "--purpose",
        purpose,
        "--relation",
        relation,
        "--result",
        result,
    ]
    if analysis is not None:
        args.extend(["--analysis", analysis])
    assert runner.invoke(app, args).exit_code == 0


def _add_doc(tmp_path: Path, doc_id: str = "compare1", body: str = "body") -> None:
    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            doc_id,
            "--moc-id",
            "default",
            "--title",
            "Compare seeds",
            "--body",
            body,
            "--run-id",
            "wandb123",
        ],
    )
    assert result.exit_code == 0, result.output


def test_markdown_sync_is_idempotent_and_preserves_run_note_user_content(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path)
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    note = tmp_path / "ManiSkill Training" / "wandb123.md"
    note.write_text(
        note.read_text(encoding="utf-8") + "\nmanual analysis\n",
        encoding="utf-8",
    )

    first = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )
    second = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")

    assert first == second
    assert "[[wandb123]]" in second
    assert "manual analysis" in note.read_text(encoding="utf-8")


def test_markdown_sync_links_active_run_ids_in_text_fields(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, run_id="target")
    _add_run(
        tmp_path,
        run_id="source",
        purpose="compare target and [[missing]]",
        relation="see `target` and target",
        result="checkpoint-target.pt target",
        analysis="analysis mentions target\n\n```text\ntarget\n```",
    )
    assert (
        runner.invoke(
            app,
            [
                "run",
                "delete",
                "target",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--json",
            ],
        ).exit_code
        == 0
    )
    _add_run(tmp_path, run_id="active")
    assert (
        runner.invoke(
            app,
            [
                "run",
                "update",
                "source",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--purpose",
                "compare active and [[target]]",
                "--relation",
                "see `active` and active",
                "--result",
                "checkpoint-active.pt active",
                "--analysis",
                "analysis mentions active\n\n```text\nactive\n```",
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )

    assert result.exit_code == 0, result.output
    note = (tmp_path / "ManiSkill Training" / "source.md").read_text(encoding="utf-8")
    assert "compare [[active]] and target" in note
    assert "see `active` and [[active]]" in note
    assert "checkpoint-active.pt [[active]]" in note
    assert "analysis mentions [[active]]" in note
    assert "```text\nactive\n```" in note

    second = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert second.exit_code == 0, second.output


def test_markdown_sync_rejects_changed_analysis_without_policy(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, analysis="initial analysis")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    note = tmp_path / "ManiSkill Training" / "wandb123.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace("initial analysis", "human analysis"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )

    assert result.exit_code != 0
    assert "--pull-analysis" in result.output
    assert "--force" in result.output


def test_markdown_sync_pull_analysis_updates_sql(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, analysis="initial analysis")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    note = tmp_path / "ManiSkill Training" / "wandb123.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace("initial analysis", "human analysis"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "sync",
            "markdown",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--pull-analysis",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "wandb123",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == "human analysis"


def test_markdown_sync_force_overwrites_changed_analysis(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, analysis="sql analysis")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    note = tmp_path / "ManiSkill Training" / "wandb123.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace("sql analysis", "human analysis"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote"), "--force"],
    )
    assert result.exit_code == 0, result.output

    assert "sql analysis" in note.read_text(encoding="utf-8")
    assert "human analysis" not in note.read_text(encoding="utf-8")


def test_markdown_sync_preserves_moc_user_content_outside_managed_block(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path)
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    moc = tmp_path / "ManiSkill Training MOC.md"
    moc.write_text(
        moc.read_text(encoding="utf-8") + "\nmanual moc note\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    content = moc.read_text(encoding="utf-8")
    assert "[[wandb123]]" in content
    assert "manual moc note" in content


def test_markdown_table_cells_escape_pipes_and_newlines(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, purpose="compare a|b\nsecond line")
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    moc = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert "compare a\\|b<br>second line" in moc


def test_markdown_markers_have_blank_lines_around_content(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, analysis="analysis")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "wandb123",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            "Section MOC.md",
            "--section",
            "StackCube",
        ],
    )
    assert result.exit_code == 0, result.output

    moc = (tmp_path / "Section MOC.md").read_text(encoding="utf-8")
    note = (tmp_path / "ManiSkill Training" / "wandb123.md").read_text(encoding="utf-8")

    assert "<!-- expnote:managed:start -->\n\n# wandb123" in note
    assert (
        "<!-- expnote:analysis:start -->\n\nanalysis\n\n<!-- expnote:analysis:end -->"
    ) in note
    assert "<!-- expnote:analysis:end -->\n\n<!-- expnote:managed:end -->" in note
    assert (
        "<!-- expnote:moc-table:start -->\n\n"
        "| # | run | purpose | relation | result | status |"
    ) in moc
    assert "| 1 | [[wandb123]]" in moc
    assert "\n\n<!-- expnote:moc-table:end -->" in moc


def test_sync_markdown_rejects_auto_index_with_curated_moc_table(tmp_path):
    _setup_workspace(tmp_path, moc_path="Baseline MOC.md")
    for run_id in ["run1", "run2", "run3"]:
        _add_run(tmp_path, run_id=run_id)
        result = runner.invoke(
            app,
            [
                "markdown",
                "table",
                "add",
                run_id,
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-path",
                "Baseline MOC.md",
                "--section",
                "topic",
            ],
        )
        assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )

    assert result.exit_code != 0
    assert "projection conflict" in result.output
    assert "auto index path contains expnote:moc-table" in result.output


def test_moc_writes_reject_curated_target_with_managed_block(tmp_path):
    commands = [
        ["markdown", "table", "add", "run1"],
        ["markdown", "table", "remove", "run1"],
        ["markdown", "table", "update", "run1", "--position", "1"],
        ["markdown", "table", "sync"],
    ]
    for index, command in enumerate(commands):
        root = tmp_path / f"case{index}"
        root.mkdir()
        _setup_workspace(root, moc_path="runs/_expnote-index.md")
        _add_run(root, run_id="run1")
        if command[2] != "add":
            result = runner.invoke(
                app,
                [
                    "markdown",
                    "table",
                    "add",
                    "run1",
                    "--workspace-dir",
                    str(root / ".expnote"),
                    "--moc-path",
                    "Curated MOC.md",
                    "--section",
                    "topic",
                ],
            )
            assert result.exit_code == 0, result.output
        (root / "Curated MOC.md").write_text(
            "<!-- expnote:managed:start -->\n\n# Experiment MOC\n\n"
            "<!-- expnote:managed:end -->\n",
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                *command,
                "--workspace-dir",
                str(root / ".expnote"),
                "--moc-path",
                "Curated MOC.md",
                "--section",
                "topic",
            ],
        )

        assert result.exit_code != 0
        assert "projection conflict" in result.output
        assert "curated MOC path contains expnote:managed" in result.output


def test_markdown_sync_supports_custom_chinese_paths(tmp_path):
    _setup_workspace(
        tmp_path,
        notes_dir="10 Projects/实验 记录",
        moc_path="10 Projects/实验 MOC.md",
    )
    _add_run(tmp_path, run_id="cn123")
    result = runner.invoke(
        app,
        ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote"), "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["run_notes"] == 1
    assert (tmp_path / "10 Projects" / "实验 MOC.md").exists()
    assert (tmp_path / "10 Projects" / "实验 记录" / "cn123.md").exists()


def test_markdown_sync_uses_external_state_dir_and_writes_to_root(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"
    common = ["--workspace-dir", str(state_dir)]

    result = runner.invoke(
        app,
        [
            "init",
            *common,
            "--obsidian-root",
            str(root),
            "--notes-dir",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training/runs",
            "--moc-path",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["topic", "add", "topic", *common])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "run",
            "add",
            *common,
            "--topic",
            "topic",
            "--run-id",
            "nzn5efly",
            "--purpose",
            "StackCube repro",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["sync", "markdown", *common, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["run_notes"] == 1
    assert (state_dir / "expnote.sqlite").exists()
    assert not (root / ".expnote").exists()
    assert (
        root / "10 Projects" / "AI Lab RFT 项目" / "ManiSkill Training MOC.md"
    ).exists()
    assert (
        root
        / "10 Projects"
        / "AI Lab RFT 项目"
        / "ManiSkill Training"
        / "runs"
        / "nzn5efly.md"
    ).exists()


def test_markdown_sync_writes_default_auto_index_to_state_dir(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"
    common = ["--workspace-dir", str(state_dir)]

    result = runner.invoke(
        app,
        [
            "init",
            *common,
            "--obsidian-root",
            str(root),
            "--notes-dir",
            "runs",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["topic", "add", "topic", *common])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "run",
            "add",
            *common,
            "--topic",
            "topic",
            "--run-id",
            "run1",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["sync", "markdown", *common, "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["index"] == str(state_dir / "index.md")
    assert (state_dir / "index.md").exists()
    assert (root / "runs" / "run1.md").exists()
    assert not (root / "runs" / "_expnote-index.md").exists()


def test_markdown_sync_writes_custom_auto_index_to_state_dir(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"
    common = ["--workspace-dir", str(state_dir)]

    result = runner.invoke(
        app,
        [
            "init",
            *common,
            "--obsidian-root",
            str(root),
            "--index-path",
            "debug/index.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["topic", "add", "topic", *common])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["run", "add", *common, "--topic", "topic", "--run-id", "run1"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["sync", "markdown", *common, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["index"] == str(state_dir / "debug" / "index.md")
    assert (state_dir / "debug" / "index.md").exists()


def test_markdown_sync_writes_doc_note_and_run_backlink(tmp_path):
    _setup_workspace(tmp_path, notes_dir="Project/runs", moc_path="Project/MOC.md")
    _add_run(tmp_path, analysis="run analysis")
    _add_doc(tmp_path, body="Compare seed outcomes.")

    result = runner.invoke(
        app,
        ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote"), "--json"],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["doc_notes"] == 1
    assert data["pulled_docs"] == 0

    doc = (tmp_path / "Project" / "analyses" / "compare1.md").read_text(
        encoding="utf-8"
    )
    run_note = (tmp_path / "Project" / "runs" / "wandb123.md").read_text(
        encoding="utf-8"
    )
    assert "# Compare seeds" in doc
    assert "| 1 | [[wandb123]]" in doc
    assert (
        "<!-- expnote:doc-body:start -->\n\n"
        "Compare seed outcomes.\n\n"
        "<!-- expnote:doc-body:end -->"
    ) in doc
    assert "## Related Docs" in run_note
    assert "- [[compare1]] Compare seeds" in run_note


def test_markdown_sync_rejects_changed_doc_body_without_policy(tmp_path):
    _setup_workspace(tmp_path, notes_dir="Project/runs", moc_path="Project/MOC.md")
    _add_run(tmp_path)
    _add_doc(tmp_path, body="SQL body")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    doc_path = tmp_path / "Project" / "analyses" / "compare1.md"
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace("SQL body", "Human body"),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )

    assert result.exit_code != 0
    assert "doc update" in result.output
    assert "--body-file" in result.output
    assert "--force" in result.output


def test_markdown_sync_pull_docs_is_rejected(tmp_path):
    _setup_workspace(tmp_path, notes_dir="Project/runs", moc_path="Project/MOC.md")
    _add_run(tmp_path)
    _add_doc(tmp_path, body="SQL body")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    doc_path = tmp_path / "Project" / "analyses" / "compare1.md"
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace("SQL body", "Human body"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "sync",
            "markdown",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--pull-docs",
        ],
    )
    assert result.exit_code != 0
    assert "Document body imports are no longer supported" in result.output
    assert "doc update" in result.output
    assert "--body-file" in result.output

    result = runner.invoke(
        app,
        [
            "doc",
            "show",
            "compare1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "SQL body"


def test_markdown_sync_force_overwrites_changed_doc_body(tmp_path):
    _setup_workspace(tmp_path, notes_dir="Project/runs", moc_path="Project/MOC.md")
    _add_run(tmp_path)
    _add_doc(tmp_path, body="SQL body")
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output

    doc_path = tmp_path / "Project" / "analyses" / "compare1.md"
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace("SQL body", "Human body"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote"), "--force"],
    )
    assert result.exit_code == 0, result.output

    content = doc_path.read_text(encoding="utf-8")
    assert "SQL body" in content
    assert "Human body" not in content


def test_markdown_sync_omits_soft_deleted_runs_from_moc(tmp_path):
    _setup_workspace(tmp_path)
    _add_run(tmp_path, run_id="deleted")
    _add_run(tmp_path, run_id="active")

    result = runner.invoke(
        app,
        [
            "run",
            "delete",
            "deleted",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        runner.invoke(
            app, ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    moc = (tmp_path / "ManiSkill Training MOC.md").read_text(encoding="utf-8")
    assert "[[active]]" in moc
    assert "[[deleted]]" not in moc
