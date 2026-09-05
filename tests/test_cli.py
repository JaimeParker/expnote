from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app

runner = CliRunner()


def _init_with_topic(tmp_path: Path, topic: str = "topic") -> None:
    assert (
        runner.invoke(
            app,
            [
                "init",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--obsidian-root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["topic", "add", topic, "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )


def _add_run(
    tmp_path: Path,
    run_id: str = "run1",
    topic: str = "topic",
    status: str = "running",
    purpose: str | None = None,
    analysis: str | None = None,
) -> None:
    args = [
        "run",
        "add",
        "--workspace-dir",
        str(tmp_path / ".expnote"),
        "--topic",
        topic,
        "--run-id",
        run_id,
        "--purpose",
        purpose if purpose is not None else f"purpose {run_id}",
        "--status",
        status,
        "--meta",
        "algo=sac",
    ]
    if analysis is not None:
        args.extend(["--analysis", analysis])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def _events(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (tmp_path / ".expnote" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]


def _moc_rows(tmp_path: Path, moc_path: str, section: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT run_id, position
                FROM moc_entries
                WHERE moc_path = ? AND section = ? AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                (moc_path, section),
            )
        ]
    finally:
        conn.close()


def _moc_table_ids(path: Path) -> list[str]:
    return [
        part.split("]]", 1)[0]
        for part in path.read_text(encoding="utf-8").split("[[")[1:]
    ]


def test_guide_agent_json_is_machine_readable():
    result = runner.invoke(app, ["guide", "agent", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["topic"] == "agent"
    assert "SQLite is the source of truth" in data["principles"]
    assert "--workspace" in data["required_flags"]
    assert "create_run" in data["workflows"]
    assert "run.show" in data["commands"]
    assert "sync markdown --pull-analysis" in json.dumps(data)
    assert "sync all" in json.dumps(data)
    assert "markdown.table.add_topic" in data["commands"]
    assert "markdown table diff" in json.dumps(data)
    assert "doc.add" in data["commands"]
    assert "doc update <doc_id> --body-file <path>" in json.dumps(data)
    assert "sync markdown --pull-docs" not in json.dumps(data)
    assert data["doc_charts"]["series_chart"]["example"][0]["source"] == "metrics.csv"
    assert "workspace-dir/doc-assets/<doc_id>" in json.dumps(data)


def test_guide_agent_human_output_mentions_core_workflow():
    result = runner.invoke(app, ["guide", "agent"])

    assert result.exit_code == 0, result.output
    assert "SQLite is the source of truth" in result.output
    assert "Obsidian Markdown is an optional projection" in result.output
    assert "init -> moc add -> topic add --moc-id -> run add" in result.output
    assert "expnote markdown table sections" in result.output
    assert "expnote doc show <doc_id> --json" in result.output
    assert "Doc chart workflow" in result.output
    assert "{{ chart:<chart_id> }}" in result.output
    assert "charts.json" in result.output
    assert "expnote validate --json" in result.output


def test_doc_help_mentions_chart_placeholders():
    result = runner.invoke(app, ["doc", "add", "--help"])

    assert result.exit_code == 0, result.output
    assert "{{ chart:id }}" in result.output
    assert "doc-assets" in result.output

    result = runner.invoke(app, ["doc", "update", "--help"])

    assert result.exit_code == 0, result.output
    assert "{{ chart:id }}" in result.output
    assert "doc-assets" in result.output


def test_guide_agent_mentions_status_lookup_and_manual_status():
    result = runner.invoke(app, ["guide", "agent"])

    assert result.exit_code == 0, result.output
    assert "expnote run status running --json" in result.output
    assert "status is manual" in result.output

    result = runner.invoke(app, ["guide", "agent", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "run status running --json" in data["workflows"]["query_run"]
    assert "manual" in data["common_pitfalls"]["status"]


def test_help_and_guide_surface_low_error_run_creation_paths():
    guide = runner.invoke(app, ["guide", "agent"])
    assert guide.exit_code == 0, guide.output
    assert "import rlgarden <config.json>" in guide.output
    assert "run add --from <existing_run_id>" in guide.output
    assert "topic set-schema" in guide.output
    assert "run refresh <run_id>" in guide.output

    run_help = runner.invoke(app, ["run", "add", "--help"])
    assert run_help.exit_code == 0, run_help.output
    assert "One-line headline metric only" in run_help.output

    import_help = runner.invoke(app, ["import", "rlgarden", "--help"])
    assert import_help.exit_code == 0, import_help.output
    assert "wandb run URL" in import_help.output


def test_guide_agent_json_includes_run_record_template():
    result = runner.invoke(app, ["guide", "agent", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    template = data["run_record_template"]["example"]
    assert template["run_id"] == "a7zf90k7"
    assert template["result"] == "78% success at 1M steps"
    assert "[[k2m9p3qw]]" in template["relation"]
    assert template["metadata"]["seed"] == 1
    assert template["status"] == "finished"
    checklist = data["run_record_template"]["checklist"]
    assert any("Metadata captures every hyperparameter" in item for item in checklist)


def test_guide_agent_human_output_includes_run_record_template():
    result = runner.invoke(app, ["guide", "agent"])

    assert result.exit_code == 0, result.output
    assert "Good run record template (run_id a7zf90k7):" in result.output
    assert "78% success at 1M steps" in result.output
    assert "[[k2m9p3qw]]" in result.output
    assert "Run record checklist:" in result.output


def test_guide_agent_run_record_checklist_is_synced_between_json_and_text():
    json_result = runner.invoke(app, ["guide", "agent", "--json"])
    text_result = runner.invoke(app, ["guide", "agent"])

    assert json_result.exit_code == 0, json_result.output
    assert text_result.exit_code == 0, text_result.output
    data = json.loads(json_result.output)
    for item in data["run_record_template"]["checklist"]:
        assert item in text_result.output


def test_guide_rejects_unknown_topic():
    result = runner.invoke(app, ["guide", "unknown", "--json"])

    assert result.exit_code != 0
    assert "supported guide topic" in result.output


def test_init_with_external_state_dir_keeps_state_out_of_root(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state" / "mani-skill-training"

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace-dir",
            str(state_dir),
            "--obsidian-root",
            str(root),
            "--notes-dir",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training/runs",
            "--moc-path",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["obsidian_root"] == str(root.resolve())
    assert data["workspace_dir"] == str(state_dir.resolve())
    assert (state_dir / "expnote.sqlite").exists()
    assert (state_dir / "events.jsonl").exists()
    assert (state_dir / "config.toml").exists()
    assert not (root / ".expnote").exists()
    assert (
        root / "10 Projects" / "AI Lab RFT 项目" / "ManiSkill Training" / "runs"
    ).exists()

    config = (state_dir / "config.toml").read_text(encoding="utf-8")
    assert f'obsidian_root = "{root.resolve()}"' in config
    assert f'state_dir = "{state_dir.resolve()}"' in config
    assert (
        'moc_path = "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md"' in config
    )


def test_init_supports_index_path_alias(tmp_path):
    state_dir = tmp_path / "state"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace-dir",
            str(state_dir),
            "--index-path",
            "custom-index.md",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["index_path"] == "custom-index.md"
    assert data["index_scope"] == "workspace_dir"
    config = (state_dir / "config.toml").read_text(encoding="utf-8")
    assert 'index_path = "custom-index.md"' in config


def test_workspace_registry_supports_active_workspace(tmp_path):
    state_dir = tmp_path / "state"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            "baseline",
            "--workspace-dir",
            str(state_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["workspace", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == [
        {
            "active": True,
            "name": "baseline",
            "workspace_dir": str(state_dir.resolve()),
        }
    ]

    result = runner.invoke(app, ["moc", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "default"


def test_workspace_pack_and_unpack_rewrites_config_and_registers(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPNOTE_CONFIG_HOME", str(tmp_path / "config-home"))
    source_root = tmp_path / "source-vault"
    source_state = tmp_path / "source-state"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            "source",
            "--workspace-dir",
            str(source_state),
            "--obsidian-root",
            str(source_root),
            "--notes-dir",
            "10 Projects/Baseline/runs",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        ["topic", "add", "topic", "--workspace", "source", "--json"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace",
            "source",
            "--topic",
            "topic",
            "--run-id",
            "packed-run",
            "--status",
            "finished",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    (source_state / "wandb-cache").mkdir()
    (source_state / "wandb-cache" / "cached.json").write_text("{}", encoding="utf-8")

    archive_path = tmp_path / "workspace.tar.gz"
    result = runner.invoke(
        app,
        [
            "workspace",
            "pack",
            str(archive_path),
            "--workspace",
            "source",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    pack_data = json.loads(result.output)
    assert pack_data["archive_path"] == str(archive_path.resolve())
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
        assert "expnote-pack.json" in names
        assert "state/expnote.sqlite" in names
        assert "state/events.jsonl" in names
        assert "state/config.toml" in names
        assert "state/wandb-cache/cached.json" in names

    target_state = tmp_path / "target-state"
    target_root = tmp_path / "target-vault"
    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "target",
            "--workspace-dir",
            str(target_state),
            "--obsidian-root",
            str(target_root),
            "--notes-dir",
            "New Vault/runs",
            "--docs-dir",
            "New Vault/analyses",
            "--index-path",
            "new-index.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    unpack_data = json.loads(result.output)
    assert unpack_data["workspace"] == "target"
    assert unpack_data["workspace_dir"] == str(target_state.resolve())
    assert unpack_data["obsidian_root"] == str(target_root.resolve())
    config = (target_state / "config.toml").read_text(encoding="utf-8")
    assert f'state_dir = "{target_state.resolve()}"' in config
    assert f'obsidian_root = "{target_root.resolve()}"' in config
    assert 'notes_dir = "New Vault/runs"' in config
    assert 'docs_dir = "New Vault/analyses"' in config
    assert 'index_path = "new-index.md"' in config
    assert 'moc_path' not in config
    assert (target_state / "wandb-cache" / "cached.json").exists()

    result = runner.invoke(
        app,
        ["run", "list", "--workspace-dir", str(target_state), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["packed-run"]

    result = runner.invoke(app, ["workspace", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert {
        "name": "target",
        "workspace_dir": str(target_state.resolve()),
        "active": True,
    } in rows


def test_workspace_unpack_no_obsidian_and_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPNOTE_CONFIG_HOME", str(tmp_path / "config-home"))
    source_root = tmp_path / "source-vault"
    source_state = tmp_path / "source-state"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            "source",
            "--workspace-dir",
            str(source_state),
            "--obsidian-root",
            str(source_root),
            "--notes-dir",
            "Project/runs",
            "--moc-path",
            "Project MOC.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    archive_path = tmp_path / "workspace.tar.gz"
    result = runner.invoke(
        app,
        ["workspace", "pack", str(archive_path), "--workspace", "source"],
    )
    assert result.exit_code == 0, result.output

    target_state = tmp_path / "target-state"
    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "invalid",
            "--workspace-dir",
            str(tmp_path / "invalid-state"),
            "--no-obsidian",
            "--notes-dir",
            "Project/runs",
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "--no-obsidian cannot be combined" in result.output

    moc_state = tmp_path / "moc-state"
    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "moc-workspace",
            "--workspace-dir",
            str(moc_state),
            "--obsidian-root",
            str(tmp_path / "target-vault"),
            "--moc-path",
            "New MOC.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    config = (moc_state / "config.toml").read_text(encoding="utf-8")
    assert 'moc_path = "New MOC.md"' in config
    assert "index_path" not in config

    target_state.mkdir()
    (target_state / "old.txt").write_text("old", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "web-only",
            "--workspace-dir",
            str(target_state),
            "--no-obsidian",
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "Use --replace" in result.output

    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "web-only",
            "--workspace-dir",
            str(target_state),
            "--no-obsidian",
            "--replace",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    config = (target_state / "config.toml").read_text(encoding="utf-8")
    assert 'state_dir = "' in config
    assert "obsidian_root" not in config
    assert "notes_dir" not in config
    assert "docs_dir" not in config
    assert "moc_path" not in config
    assert 'index_path = "index.md"' in config
    assert not (target_state / "old.txt").exists()

    result = runner.invoke(
        app, ["validate", "--workspace-dir", str(target_state), "--json"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(target_state), "--json"]
    )
    assert result.exit_code != 0
    assert "no Obsidian projection configured" in result.output


def test_workspace_unpack_rejects_unsafe_archive_member(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPNOTE_CONFIG_HOME", str(tmp_path / "config-home"))
    archive_path = tmp_path / "bad.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        manifest = b"{}"
        info = tarfile.TarInfo("expnote-pack.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        bad = b"bad"
        bad_info = tarfile.TarInfo("../bad")
        bad_info.size = len(bad)
        archive.addfile(bad_info, io.BytesIO(bad))

    result = runner.invoke(
        app,
        [
            "workspace",
            "unpack",
            str(archive_path),
            "--workspace",
            "bad",
            "--workspace-dir",
            str(tmp_path / "target"),
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "unsafe archive member path" in result.output


def test_web_only_workspace_rejects_markdown_projection(tmp_path):
    state_dir = tmp_path / "state"
    result = runner.invoke(app, ["init", "--workspace-dir", str(state_dir), "--json"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["validate", "--workspace-dir", str(state_dir), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["projection_conflicts"] == []

    result = runner.invoke(
        app, ["sync", "markdown", "--workspace-dir", str(state_dir), "--json"]
    )
    assert result.exit_code != 0
    assert "no Obsidian projection configured" in result.output


def test_web_detach_starts_background_process_and_returns(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    result = runner.invoke(app, ["init", "--workspace-dir", str(state_dir), "--json"])
    assert result.exit_code == 0, result.output

    calls = []

    class FakeProcess:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProcess()

    monkeypatch.setattr("expnote.cli.subprocess.Popen", fake_popen)

    result = runner.invoke(
        app,
        [
            "web",
            "--workspace-dir",
            str(state_dir),
            "--host",
            "0.0.0.0",
            "--port",
            "8765",
            "--no-open",
            "--detach",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "expnote web started in background: http://localhost:8765 (pid 12345)" in (
        result.output
    )
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[1:4] == ["-m", "expnote.cli", "web"]
    assert "--workspace-dir" in cmd
    assert str(state_dir.resolve()) in cmd
    assert "--host" in cmd
    assert "0.0.0.0" in cmd
    assert "--port" in cmd
    assert "8765" in cmd
    assert "--no-open" in cmd
    assert "--detach" not in cmd
    assert kwargs["stdin"] is not None
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["EXPNOTE_WEB_DETACHED_CHILD"] == "1"


def test_init_records_default_and_custom_docs_dir(tmp_path):
    default_root = tmp_path / "default"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace-dir",
            str(default_root / ".expnote"),
            "--obsidian-root",
            str(default_root),
            "--notes-dir",
            "10 Projects/Baseline/runs",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["docs_dir"] == "10 Projects/Baseline/analyses"
    assert (default_root / "10 Projects" / "Baseline" / "analyses").exists()
    config = (default_root / ".expnote" / "config.toml").read_text(encoding="utf-8")
    assert 'docs_dir = "10 Projects/Baseline/analyses"' in config

    custom_root = tmp_path / "custom"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace-dir",
            str(custom_root / ".expnote"),
            "--obsidian-root",
            str(custom_root),
            "--notes-dir",
            "runs",
            "--docs-dir",
            "docs/analysis",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["docs_dir"] == "docs/analysis"
    assert (custom_root / "docs" / "analysis").exists()


def test_existing_schema_migrates_on_mutating_cli_use(tmp_path):
    state_dir = tmp_path / ".expnote"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "config.toml").write_text(
        'project = "old"\nnotes_dir = "notes/runs"\nmoc_path = "notes/moc.md"\n',
        encoding="utf-8",
    )
    conn = sqlite3.connect(state_dir / "expnote.sqlite")
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE topics (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            topic_id TEXT NOT NULL REFERENCES topics(id),
            purpose TEXT NOT NULL DEFAULT '',
            relation TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO topics(id, title, summary, created_at, updated_at)
        VALUES('topic_old', 'Old Topic', '', '2026-01-01', '2026-01-01');
        INSERT INTO runs(
            id, topic_id, purpose, relation, result, status,
            started_at, updated_at, metadata_json
        )
        VALUES(
            'old1', 'topic_old', 'old purpose', '', '', 'running',
            '2026-01-01', '2026-01-01', '{}'
        );
        """
    )
    conn.commit()
    conn.close()

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "old1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "no such table: mocs" in str(result.exception)

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "old1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--status",
            "finished",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["analysis"] == ""
    conn = sqlite3.connect(state_dir / "expnote.sqlite")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
        "('docs', 'doc_runs', 'topic_schemas', 'topic_history')"
    ).fetchall()
    version = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    conn.close()
    assert {row[0] for row in rows} == {
        "docs",
        "doc_runs",
        "topic_schemas",
        "topic_history",
    }
    assert version == "6"


def test_external_state_dir_supports_cli_workflow(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"
    common = ["--workspace-dir", str(state_dir)]

    result = runner.invoke(
        app, ["init", *common, "--obsidian-root", str(root), "--json"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["topic", "add", "topic", *common, "--json"])
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
            "external1",
            "--status",
            "running",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["run", "list", *common, "--json"])
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["external1"]

    result = runner.invoke(app, ["validate", *common, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["projection_conflicts"] == []
    assert data["counts"] == {
        "artifacts": 0,
        "runs": 1,
        "topics": 1,
        "benchmarks": 0,
    }

    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert [event["type"] for event in events] == ["init", "topic.add", "run.add"]


def test_validate_reports_projection_conflicts(tmp_path):
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--obsidian-root",
            str(tmp_path),
            "--moc-path",
            "notes/experiments.md",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["topic", "add", "topic", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output
    _add_run(tmp_path, "run1")
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            "notes/experiments.md",
            "--section",
            "topic",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["validate", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["projection_conflicts"]
    assert data["projection_conflicts"][0]["kind"] == (
        "auto_index_contains_curated_moc_table"
    )


def test_external_state_dir_must_be_reused_for_follow_up_commands(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"

    result = runner.invoke(
        app,
        ["init", "--workspace-dir", str(state_dir), "--obsidian-root", str(root)],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["topic", "list", "--workspace-dir", str(root / ".expnote"), "--json"]
    )
    assert result.exit_code != 0
    assert not (root / ".expnote").exists()


def test_init_add_query_and_soft_delete(tmp_path):
    result = runner.invoke(
        app, ["init", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".expnote" / "expnote.sqlite").exists()

    result = runner.invoke(
        app,
        [
            "topic",
            "add",
            "StackCube ablation",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "StackCube ablation",
            "--run-id",
            "abc123",
            "--purpose",
            "test purpose",
            "--status",
            "running",
            "--meta",
            "algo=sac",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "abc123"
    assert data["metadata"]["algo"] == "sac"

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "status = 'running'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["abc123"]

    result = runner.invoke(
        app,
        [
            "run",
            "delete",
            "abc123",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["run", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_run_list_filters_by_status(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "running1", status="running")
    _add_run(tmp_path, "finished1", status="finished")

    result = runner.invoke(
        app,
        [
            "run",
            "list",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--status",
            "running",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["running1"]
    assert rows[0]["status"] == "running"
    assert "topic_title" in rows[0]
    assert "metadata" in rows[0]


def test_run_list_combines_topic_and_status_filters(tmp_path):
    _init_with_topic(tmp_path, topic="Topic A")
    result = runner.invoke(
        app,
        [
            "topic",
            "add",
            "Topic B",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    _add_run(tmp_path, "a-running", topic="Topic A", status="running")
    _add_run(tmp_path, "a-finished", topic="Topic A", status="finished")
    _add_run(tmp_path, "b-running", topic="Topic B", status="running")

    result = runner.invoke(
        app,
        [
            "run",
            "list",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "Topic A",
            "--status",
            "running",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["a-running"]


def test_run_list_status_hides_soft_deleted_runs(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "active", status="running")
    _add_run(tmp_path, "deleted", status="running")
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

    result = runner.invoke(
        app,
        [
            "run",
            "list",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--status",
            "running",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["active"]


def test_run_status_lists_matching_runs(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "running1", status="running")
    _add_run(tmp_path, "finished1", status="finished")

    result = runner.invoke(
        app,
        [
            "run",
            "status",
            "running",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["running1"]


def test_doc_crud_and_run_links(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")
    _add_run(tmp_path, "run2")

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "compare1",
            "--moc-id",
            "default",
            "--title",
            "Compare seeds",
            "--body",
            "Initial comparison",
            "--run-id",
            "run2",
            "--run-id",
            "run1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "compare1"
    assert data["moc_id"] == "default"
    assert data["title"] == "Compare seeds"
    assert data["body"] == "Initial comparison"
    assert [row["run_id"] for row in data["runs"]] == ["run2", "run1"]

    result = runner.invoke(
        app,
        [
            "doc",
            "update",
            "compare1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--append-body",
            "Final note",
            "--meta",
            "owner=agent",
            "--meta-json",
            "seeds=[1,2]",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["body"] == "Initial comparison\n\nFinal note"
    assert data["metadata"] == {"owner": "agent", "seeds": [1, 2]}

    result = runner.invoke(
        app,
        [
            "doc",
            "link",
            "compare1",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--position",
            "1",
            "--role",
            "baseline",
            "--note",
            "main comparison",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [row["run_id"] for row in data["runs"]] == ["run1", "run2"]
    assert data["runs"][0]["role"] == "baseline"

    result = runner.invoke(
        app,
        [
            "doc",
            "unlink",
            "compare1",
            "run2",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [row["run_id"] for row in data["runs"]] == ["run1"]

    result = runner.invoke(
        app, ["doc", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["compare1"]

    result = runner.invoke(
        app,
        [
            "doc",
            "delete",
            "compare1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["doc", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_doc_add_reads_body_from_file(tmp_path):
    _init_with_topic(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text("Body from a file.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "filedoc",
            "--moc-id",
            "default",
            "--title",
            "File-backed doc",
            "--body-file",
            str(body_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "Body from a file."


def test_doc_add_reads_body_from_stdin(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "stdindoc",
            "--moc-id",
            "default",
            "--title",
            "Stdin-backed doc",
            "--body-file",
            "-",
            "--json",
        ],
        input="Body from stdin.\n",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "Body from stdin."


def test_doc_add_body_file_strips_trailing_newlines(tmp_path):
    _init_with_topic(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text("line one\nline two\n\n\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "trimdoc",
            "--moc-id",
            "default",
            "--title",
            "Trimmed doc",
            "--body-file",
            str(body_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "line one\nline two"


def test_doc_add_rejects_body_and_body_file(tmp_path):
    _init_with_topic(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "conflictdoc",
            "--moc-id",
            "default",
            "--title",
            "Conflict doc",
            "--body",
            "inline",
            "--body-file",
            str(body_path),
        ],
    )

    assert result.exit_code != 0
    assert "--body and --body-file cannot be used together" in result.output


def test_doc_add_body_file_missing_path(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "missingdoc",
            "--moc-id",
            "default",
            "--title",
            "Missing file doc",
            "--body-file",
            str(tmp_path / "nonexistent.md"),
        ],
    )

    assert result.exit_code != 0
    assert "could not read --body-file" in result.output


def test_doc_update_reads_body_from_file(tmp_path):
    _init_with_topic(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "doc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--doc-id",
                "updatedoc",
                "--moc-id",
                "default",
                "--title",
                "Update doc",
                "--body",
                "original",
            ],
        ).exit_code
        == 0
    )
    body_path = tmp_path / "new_body.md"
    body_path.write_text("Replaced from file.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "update",
            "updatedoc",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--body-file",
            str(body_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "Replaced from file."


def test_doc_update_appends_body_from_file(tmp_path):
    _init_with_topic(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "doc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--doc-id",
                "appenddoc",
                "--moc-id",
                "default",
                "--title",
                "Append doc",
                "--body",
                "existing",
            ],
        ).exit_code
        == 0
    )
    append_path = tmp_path / "append.md"
    append_path.write_text("appended from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "update",
            "appenddoc",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--append-body-file",
            str(append_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == "existing\n\nappended from file"


def test_doc_update_rejects_body_and_body_file(tmp_path):
    _init_with_topic(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "doc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--doc-id",
                "conflictupdatedoc",
                "--moc-id",
                "default",
                "--title",
                "Conflict update doc",
            ],
        ).exit_code
        == 0
    )
    body_path = tmp_path / "body.md"
    body_path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "update",
            "conflictupdatedoc",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--body",
            "inline",
            "--body-file",
            str(body_path),
        ],
    )

    assert result.exit_code != 0
    assert "--body and --body-file cannot be used together" in result.output


def test_doc_update_rejects_body_file_and_append_body(tmp_path):
    _init_with_topic(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "doc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--doc-id",
                "crossconflictdoc",
                "--moc-id",
                "default",
                "--title",
                "Cross conflict doc",
            ],
        ).exit_code
        == 0
    )
    body_path = tmp_path / "body.md"
    body_path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "doc",
            "update",
            "crossconflictdoc",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--body-file",
            str(body_path),
            "--append-body",
            "extra",
        ],
    )

    assert result.exit_code != 0
    assert "--body and --append-body cannot be used together" in result.output


def test_sql_moc_crud_and_moc_scoped_topics(tmp_path):
    assert (
        runner.invoke(
            app, ["init", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "moc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-id",
            "baseline",
            "--title",
            "Baseline MOC",
            "--summary",
            "Offline to online baselines",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "baseline"

    for moc_id in ["baseline", "default"]:
        result = runner.invoke(
            app,
            [
                "topic",
                "add",
                "Same Topic",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                moc_id,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "moc",
            "show",
            "baseline",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"] == "Offline to online baselines"
    assert [topic["title"] for topic in data["topics"]] == ["Same Topic"]


def test_doc_link_rejects_cross_moc_run(tmp_path):
    assert (
        runner.invoke(
            app, ["init", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "moc",
                "add",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "other",
                "--title",
                "Other MOC",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "topic",
                "add",
                "Other Topic",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--moc-id",
                "other",
            ],
        ).exit_code
        == 0
    )
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "default-run")
    _add_run(tmp_path, "other-run", topic="Other Topic")

    result = runner.invoke(
        app,
        [
            "doc",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--doc-id",
            "default-doc",
            "--moc-id",
            "default",
            "--title",
            "Default Doc",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "doc",
            "link",
            "default-doc",
            "other-run",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "run not found in MOC default" in result.output


def test_duplicate_run_id_errors(tmp_path):
    _init_with_topic(tmp_path)
    args = [
        "run",
        "add",
        "--workspace-dir",
        str(tmp_path / ".expnote"),
        "--topic",
        "topic",
        "--run-id",
        "same",
    ]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code != 0


def test_topic_update_delete_and_include_deleted(tmp_path):
    _init_with_topic(tmp_path, "old")

    result = runner.invoke(
        app,
        [
            "topic",
            "update",
            "old",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--new-title",
            "new",
            "--summary",
            "summary",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["title"] == "new"
    assert data["summary"] == "summary"

    result = runner.invoke(
        app,
        [
            "topic",
            "delete",
            "new",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["topic", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []

    result = runner.invoke(
        app,
        [
            "topic",
            "list",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--include-deleted",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["title"] == "new"
    assert rows[0]["deleted_at"] is not None


def test_topic_schema_round_trips_and_validate_reports_missing_metadata(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "topic", "set-schema", "topic",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--required-meta", "algorithm",
            "--required-meta", "env_id",
            "--required-meta", "seed",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["required_metadata"] == ["algorithm", "env_id", "seed"]

    _add_run(tmp_path, "run1")

    validate = runner.invoke(
        app,
        [
            "validate",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--stale-running-days", "0",
            "--json",
        ],
    )
    assert validate.exit_code == 0, validate.output
    data = json.loads(validate.output)
    assert data["ok"] is False
    assert data["missing_required_metadata"] == [
        {
            "run_id": "run1",
            "topic_id": schema["topic_id"],
            "topic_title": "topic",
            "moc_id": "default",
            "missing_metadata": ["algorithm", "env_id", "seed"],
        }
    ]

    show = runner.invoke(
        app,
        [
            "topic", "schema", "topic",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert show.exit_code == 0, show.output
    assert json.loads(show.output)[0]["required_metadata"] == [
        "algorithm", "env_id", "seed"
    ]


def test_topic_update_writes_history(tmp_path):
    _init_with_topic(tmp_path, "old")

    update = runner.invoke(
        app,
        [
            "topic", "update", "old",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--new-title", "new",
            "--summary", "summary",
        ],
    )
    assert update.exit_code == 0, update.output

    history = runner.invoke(
        app,
        [
            "topic", "history", "new",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert history.exit_code == 0, history.output
    data = json.loads(history.output)
    assert len(data) == 1
    assert data[0]["fields"] == ["title", "summary"]
    assert data[0]["old_values"] == {"title": "old", "summary": ""}
    assert data[0]["new_values"] == {"title": "new", "summary": "summary"}


def test_validate_reports_stale_running_runs(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "old")
    started_at = (datetime.now(UTC) - timedelta(days=4)).replace(microsecond=0)
    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    try:
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE id = ?",
            (started_at.isoformat(), "old"),
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["validate", "--workspace-dir", str(tmp_path / ".expnote"), "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["stale_running_runs"][0]["run_id"] == "old"
    assert data["stale_running_runs"][0]["age_days"] >= 3


def test_run_show_update_list_and_metadata_merge(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--purpose",
            "new purpose",
            "--relation",
            "baseline",
            "--result",
            "better",
            "--analysis",
            "updated analysis",
            "--status",
            "finished",
            "--meta",
            "seed=1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    updated = json.loads(result.output)
    assert updated["purpose"] == "new purpose"
    assert updated["relation"] == "baseline"
    assert updated["result"] == "better"
    assert updated["analysis"] == "updated analysis"
    assert updated["status"] == "finished"
    assert updated["metadata"] == {"algo": "sac", "seed": "1"}

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown["topic_title"] == "topic"
    assert shown["analysis"] == "updated analysis"
    assert shown["metadata"] == {"algo": "sac", "seed": "1"}

    result = runner.invoke(
        app, ["run", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["run1"]
    assert rows[0]["status"] == "finished"


def test_run_create_accepts_id_alias(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "create",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--id",
            "alias1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "alias1"


def test_run_add_accepts_topic_id(tmp_path):
    _init_with_topic(tmp_path)
    result = runner.invoke(
        app, ["topic", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    topic_id = json.loads(result.output)[0]["id"]

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic-id",
            topic_id,
            "--run-id",
            "by-topic-id",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["topic_id"] == topic_id


def test_run_add_rejects_topic_and_topic_id_together(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--topic-id",
            "topic_x",
            "--run-id",
            "bad-topic",
        ],
    )

    assert result.exit_code != 0
    assert "--topic and --topic-id cannot be used together" in result.output


def test_run_update_appends_analysis_with_blank_line(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="first observation")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--append-analysis",
            "second observation",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == (
        "first observation\n\nsecond observation"
    )


def test_run_update_appends_analysis_without_leading_blank_line(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--append-analysis",
            "first observation",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == "first observation"


def test_run_update_rejects_analysis_replace_and_append(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="first observation")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--analysis",
            "replacement",
            "--append-analysis",
            "extra",
        ],
    )

    assert result.exit_code != 0
    assert "--analysis and --append-analysis cannot be used together" in result.output


def test_run_add_reads_analysis_from_file(tmp_path):
    _init_with_topic(tmp_path)
    analysis_path = tmp_path / "analysis.md"
    analysis_path.write_text("Analysis from a file.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "filerun",
            "--purpose",
            "purpose filerun",
            "--analysis-file",
            str(analysis_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == "Analysis from a file."


def test_run_add_reads_analysis_from_stdin(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "stdinrun",
            "--purpose",
            "purpose stdinrun",
            "--analysis-file",
            "-",
            "--json",
        ],
        input="Analysis from stdin.\n",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == "Analysis from stdin."


def test_run_add_analysis_file_missing_path(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "missingrun",
            "--purpose",
            "purpose missingrun",
            "--analysis-file",
            str(tmp_path / "nonexistent.md"),
        ],
    )

    assert result.exit_code != 0
    assert "could not read --analysis-file" in result.output


def test_run_update_reads_analysis_from_file(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="original")
    analysis_path = tmp_path / "analysis.md"
    analysis_path.write_text("Replaced from file.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--analysis-file",
            str(analysis_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == "Replaced from file."


def test_run_update_appends_analysis_from_file(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="first observation")
    append_path = tmp_path / "append.md"
    append_path.write_text("second observation", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--append-analysis-file",
            str(append_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["analysis"] == (
        "first observation\n\nsecond observation"
    )


def test_run_update_rejects_analysis_and_analysis_file(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="original")
    analysis_path = tmp_path / "analysis.md"
    analysis_path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--analysis",
            "inline",
            "--analysis-file",
            str(analysis_path),
        ],
    )

    assert result.exit_code != 0
    assert "--analysis and --analysis-file cannot be used together" in result.output


def test_run_update_rejects_analysis_file_and_append_analysis(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", analysis="original")
    analysis_path = tmp_path / "analysis.md"
    analysis_path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--analysis-file",
            str(analysis_path),
            "--append-analysis",
            "extra",
        ],
    )

    assert result.exit_code != 0
    assert "--analysis and --append-analysis cannot be used together" in result.output


def test_run_add_and_update_support_typed_metadata(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "typed",
            "--meta",
            "seed=1",
            "--meta-json",
            "typed_seed=1",
            "--meta-json",
            "lr=0.0003",
            "--meta-json",
            "use_wandb=true",
            "--meta-json",
            'tags=["a","b"]',
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(result.output)["metadata"]
    assert metadata["seed"] == "1"
    assert metadata["typed_seed"] == 1
    assert metadata["lr"] == 0.0003
    assert metadata["use_wandb"] is True
    assert metadata["tags"] == ["a", "b"]

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "typed",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--meta-json",
            'hparams={"batch":256}',
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metadata"]["hparams"] == {"batch": 256}


def test_run_add_and_update_support_metadata_json_object(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "metadata-object",
            "--metadata-json",
            '{"algo":"calql","seed":1}',
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metadata"] == {"algo": "calql", "seed": 1}

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "metadata-object",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--metadata-json",
            '{"clip":true}',
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metadata"] == {
        "algo": "calql",
        "clip": True,
        "seed": 1,
    }


def test_run_update_unsets_metadata_keys(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--meta",
            "seed=1",
            "--unset-meta",
            "algo",
            "--unset-meta",
            "missing",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metadata"] == {"seed": "1"}


def test_run_update_rejects_set_and_unset_same_metadata_key(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--meta",
            "seed=1",
            "--unset-meta",
            "seed",
        ],
    )

    assert result.exit_code != 0
    assert "cannot be set and unset" in result.output


def test_run_add_rejects_invalid_meta_json(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "bad",
            "--meta-json",
            "seed={bad",
        ],
    )

    assert result.exit_code != 0


def test_run_add_rejects_nested_metadata_key_object(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "bad",
            "--meta-json",
            'metadata={"algo":"calql"}',
        ],
    )

    assert result.exit_code != 0
    assert "--metadata-json" in result.output


def test_run_show_field_outputs_single_public_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--field",
            "purpose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "purpose run1\n"


def test_run_show_field_outputs_json_scalar(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", status="finished")

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--field",
            "status",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == "finished"


def test_run_show_field_outputs_metadata_object(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--field",
            "metadata",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"algo": "sac"}


def test_run_show_field_supports_topic_alias(tmp_path):
    _init_with_topic(tmp_path, "StackCube SAC")
    _add_run(tmp_path, "run1", topic="StackCube SAC")

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--field",
            "topic",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "StackCube SAC\n"


def test_run_show_field_rejects_unknown_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "show",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--field",
            "unknown",
        ],
    )

    assert result.exit_code != 0
    assert "supported fields:" in result.output


def test_run_delete_hides_run_from_list_and_query(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "deleted")
    _add_run(tmp_path, "active")

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

    result = runner.invoke(
        app, ["run", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["active"]

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "1 = 1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["active"]


def test_artifact_add_list_and_delete(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "artifact",
            "add",
            "run1",
            "file:///tmp/model.pt",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--kind",
            "checkpoint",
            "--note",
            "best model",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    artifact = json.loads(result.output)
    assert artifact["run_id"] == "run1"
    assert artifact["kind"] == "checkpoint"
    assert artifact["uri"] == "file:///tmp/model.pt"

    result = runner.invoke(
        app,
        [
            "artifact",
            "list",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == [artifact["id"]]
    assert rows[0]["note"] == "best model"

    result = runner.invoke(
        app,
        [
            "artifact",
            "delete",
            artifact["id"],
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "artifact",
            "list",
            "run1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_relation_add_and_delete_updates_database_and_events(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "src")
    _add_run(tmp_path, "dst")

    result = runner.invoke(
        app,
        [
            "relation",
            "add",
            "src",
            "dst",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--kind",
            "compares-to",
            "--note",
            "same seed",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    relation = json.loads(result.output)
    assert relation["src_run_id"] == "src"
    assert relation["dst_run_id"] == "dst"
    assert relation["kind"] == "compares-to"

    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    row = conn.execute(
        "SELECT note, deleted_at FROM relations WHERE id = ?", (relation["id"],)
    ).fetchone()
    conn.close()
    assert row == ("same seed", None)

    result = runner.invoke(
        app,
        [
            "relation",
            "delete",
            relation["id"],
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    row = conn.execute(
        "SELECT deleted_at FROM relations WHERE id = ?", (relation["id"],)
    ).fetchone()
    conn.close()
    assert row[0] is not None

    event_types = [event["type"] for event in _events(tmp_path)]
    assert "relation.add" in event_types
    assert "relation.delete" in event_types


def test_events_jsonl_records_mutating_commands_and_not_failed_duplicates(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "same")
    before = _events(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "same",
        ],
    )
    assert result.exit_code != 0
    after = _events(tmp_path)
    assert after == before

    event_types = [event["type"] for event in after]
    assert event_types == ["init", "topic.add", "run.add"]
    assert all({"id", "type", "ts", "payload"} <= event.keys() for event in after)


def test_run_query_rejects_unsafe_fragments(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    unsafe_args = [
        ["--where", "1=1; DROP TABLE runs"],
        ["--where", "1=1 -- comment"],
        ["--order-by", "started_at DESC; DELETE FROM runs"],
    ]
    for fragment in unsafe_args:
        result = runner.invoke(
            app,
            [
                "run",
                "query",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                *fragment,
                "--json",
            ],
        )
        assert result.exit_code != 0


def test_run_query_supports_restricted_where_and_order_by(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "abc123", status="running")
    _add_run(tmp_path, "done1", status="finished")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "status = 'running' AND id = 'abc123'",
            "--order-by",
            "id ASC",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["abc123"]


def test_run_query_supports_status_option(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", status="running")
    _add_run(tmp_path, "run2", status="running")
    _add_run(tmp_path, "done1", status="finished")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--status",
            "running",
            "--where",
            "id = 'run2'",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["run2"]


def test_run_query_supports_analysis_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "abc123", analysis="useful analysis")
    _add_run(tmp_path, "other", analysis="different")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "analysis = 'useful analysis'",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["abc123"]


def test_run_query_supports_metadata_fields(tmp_path):
    _init_with_topic(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "typed",
            "--meta",
            "algo=sac",
            "--meta-json",
            "seed=1",
            "--meta-json",
            "use_wandb=true",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--run-id",
            "other",
        ],
    )
    assert result.exit_code == 0, result.output

    cases = [
        ("metadata.algo = 'sac'", ["typed"]),
        ("metadata.seed = 1", ["typed"]),
        ("metadata.use_wandb = true", ["typed"]),
        ("metadata.missing = null", ["other", "typed"]),
    ]
    for where, expected_ids in cases:
        result = runner.invoke(
            app,
            [
                "run",
                "query",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                "--where",
                where,
                "--order-by",
                "id ASC",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert [row["id"] for row in json.loads(result.output)] == expected_ids


def test_moc_add_list_remove_and_diff(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    moc_path = "Inbox/Test MOC.md"

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    moc = (tmp_path / moc_path).read_text(encoding="utf-8")
    assert "## StackCube" in moc
    assert "<!-- expnote:moc-table:start -->" in moc
    assert "[[moc1]]" in moc

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "list",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["run_id"] for row in json.loads(result.output)] == ["moc1"]

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "diff",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed"] is False

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "remove",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[[moc1]]" not in (tmp_path / moc_path).read_text(encoding="utf-8")


def test_query_commands_work_with_readonly_database(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "readonly1", status="running", purpose="readonly purpose")
    moc_path = "Inbox/Readonly MOC.md"
    workspace_args = ["--workspace-dir", str(tmp_path / ".expnote")]
    assert (
        runner.invoke(
            app,
            [
                "artifact",
                "add",
                "readonly1",
                "checkpoint.pt",
                *workspace_args,
                "--json",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "doc",
                "add",
                *workspace_args,
                "--doc-id",
                "readonly-doc",
                "--moc-id",
                "default",
                "--title",
                "Readonly doc",
                "--run-id",
                "readonly1",
                "--body",
                "Readonly body",
                "--json",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "markdown",
                "table",
                "add",
                "readonly1",
                *workspace_args,
                "--moc-path",
                moc_path,
                "--section",
                "Readonly",
                "--json",
            ],
        ).exit_code
        == 0
    )
    events_before = (tmp_path / ".expnote" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    db_path = tmp_path / ".expnote" / "expnote.sqlite"
    db_path.chmod(0o444)
    commands = [
        ["topic", "list", *workspace_args, "--json"],
        ["run", "list", *workspace_args, "--json"],
        ["run", "status", "running", *workspace_args, "--json"],
        ["run", "show", "readonly1", *workspace_args, "--json"],
        ["run", "show", "readonly1", *workspace_args, "--field", "status"],
        [
            "run",
            "query",
            *workspace_args,
            "--where",
            "status = 'running'",
            "--json",
        ],
        ["moc", "list", *workspace_args, "--json"],
        ["moc", "show", "default", *workspace_args, "--json"],
        ["doc", "list", *workspace_args, "--json"],
        ["doc", "show", "readonly-doc", *workspace_args, "--json"],
        ["artifact", "list", "readonly1", *workspace_args, "--json"],
        [
            "markdown",
            "table",
            "list",
            *workspace_args,
            "--moc-path",
            moc_path,
            "--json",
        ],
        [
            "markdown",
            "table",
            "sections",
            *workspace_args,
            "--moc-path",
            moc_path,
            "--json",
        ],
        [
            "markdown",
            "table",
            "diff",
            *workspace_args,
            "--moc-path",
            moc_path,
            "--section",
            "Readonly",
            "--json",
        ],
        ["validate", *workspace_args, "--json"],
    ]

    try:
        for command in commands:
            result = runner.invoke(app, command)
            assert result.exit_code == 0, f"{command}: {result.output}"
    finally:
        db_path.chmod(0o644)

    assert (
        tmp_path / ".expnote" / "events.jsonl"
    ).read_text(encoding="utf-8") == events_before


def test_moc_sections_lists_registered_sections(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    moc_path = "Inbox/Test MOC.md"
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "260728-StackCube",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "sections",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {"moc_path": moc_path, "section": "260728-StackCube", "rows": 1}
    ]


def test_moc_add_topic_registers_active_topic_runs(tmp_path):
    _init_with_topic(tmp_path)
    for run_id in ["a", "b", "c"]:
        _add_run(tmp_path, run_id, purpose=f"purpose {run_id}")
    moc_path = "Inbox/Test MOC.md"

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add-topic",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--moc-path",
            moc_path,
            "--section",
            "260728-Topic",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["added"] == ["a", "b", "c"]
    assert data["skipped"] == []
    assert data["sync"]["rows"] == 3
    assert _moc_table_ids(tmp_path / moc_path) == ["a", "b", "c"]

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add-topic",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--moc-path",
            moc_path,
            "--section",
            "260728-Topic",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["added"] == []
    assert data["skipped"] == ["a", "b", "c"]
    assert _moc_rows(tmp_path, moc_path, "260728-Topic") == [
        {"run_id": "a", "position": 1},
        {"run_id": "b", "position": 2},
        {"run_id": "c", "position": 3},
    ]


def test_moc_sync_rejects_unregistered_empty_section(tmp_path):
    _init_with_topic(tmp_path)
    moc_path = "Inbox/Test MOC.md"

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "sync",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "Wrong Section",
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "no registered MOC entries" in result.output
    assert not (tmp_path / moc_path).exists()


def test_moc_sync_allow_empty_creates_empty_section(tmp_path):
    _init_with_topic(tmp_path)
    moc_path = "Inbox/Test MOC.md"

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "sync",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "Empty",
            "--allow-empty",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["rows"] == 0
    assert "## Empty" in (tmp_path / moc_path).read_text(encoding="utf-8")


def test_sync_all_updates_registered_curated_mocs(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="old")
    moc_path = "Inbox/Test MOC.md"
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--purpose",
            "new",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        ["sync", "markdown", "--workspace-dir", str(tmp_path / ".expnote"), "--json"],
    )
    assert result.exit_code == 0, result.output
    markdown_data = json.loads(result.output)
    assert markdown_data["curated_moc_sections"]["synced"] == 0
    assert markdown_data["curated_moc_sections"]["registered"] == 1
    assert "old" in (tmp_path / moc_path).read_text(encoding="utf-8")

    result = runner.invoke(
        app, ["sync", "all", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["curated_moc_sections"]["synced"] == 1
    assert "new" in (tmp_path / moc_path).read_text(encoding="utf-8")


def test_moc_update_normalizes_positions(tmp_path):
    _init_with_topic(tmp_path)
    for run_id in ["a", "b", "c"]:
        _add_run(tmp_path, run_id, purpose=f"purpose {run_id}")
    moc_path = "Inbox/Test MOC.md"
    common = [
        "--workspace-dir",
        str(tmp_path / ".expnote"),
        "--moc-path",
        moc_path,
        "--section",
        "StackCube",
    ]

    for run_id in ["a", "b", "c"]:
        result = runner.invoke(
            app, ["markdown", "table", "add", run_id, *common, "--json"]
        )
        assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["markdown", "table", "list", *common, "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["run_id"] for row in rows] == ["a", "b", "c"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(
        app,
        ["markdown", "table", "update", "c", *common, "--position", "1", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["c", "a", "b"]
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert _moc_table_ids(tmp_path / moc_path) == ["c", "a", "b"]

    result = runner.invoke(
        app,
        ["markdown", "table", "update", "a", *common, "--position", "99", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["c", "b", "a"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(
        app,
        ["markdown", "table", "update", "a", *common, "--position", "0", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["a", "c", "b"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(app, ["markdown", "table", "remove", "c", *common, "--json"])
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["a", "b"]
    assert [row["position"] for row in rows] == [1, 2]

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "update",
            "missing",
            *common,
            "--position",
            "1",
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_moc_diff_reports_manual_table_changes(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    moc_path = "Inbox/Test MOC.md"
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
        ],
    )
    assert result.exit_code == 0, result.output

    path = tmp_path / moc_path
    path.write_text(
        path.read_text(encoding="utf-8").replace("[[moc1]]", "[[manual]]"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "diff",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["changed"] is True
    assert data["missing"] == ["moc1"]
    assert data["stale"] == ["manual"]


def test_moc_diff_reads_observed_ids_from_run_column_only(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    _add_run(tmp_path, "compare1", purpose="comparison")
    moc_path = "Inbox/Test MOC.md"
    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--relation",
            "Compare against [[compare1]].",
            "--result",
            "Result mentions [[compare1]].",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "add",
            "moc1",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "diff",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["observed"] == ["moc1"]
    assert data["missing"] == []
    assert data["stale"] == []


def test_moc_diff_reports_projection_conflict(tmp_path):
    _init_with_topic(tmp_path)
    path = tmp_path / "Inbox" / "Managed MOC.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "<!-- expnote:managed:start -->\n\n# Experiment MOC\n\n"
        "<!-- expnote:managed:end -->\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "markdown",
            "table",
            "diff",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--moc-path",
            "Inbox/Managed MOC.md",
            "--section",
            "StackCube",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["conflicts"][0]["kind"] == "curated_moc_contains_auto_index"


def test_run_query_supports_topic_alias_and_limit(tmp_path):
    _init_with_topic(tmp_path, "topic a")
    result = runner.invoke(
        app, ["topic", "add", "topic b", "--workspace-dir", str(tmp_path / ".expnote")]
    )
    assert result.exit_code == 0, result.output
    _add_run(tmp_path, "a1", topic="topic a")
    _add_run(tmp_path, "b1", topic="topic b")
    _add_run(tmp_path, "b2", topic="topic b")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "topic = 'topic b'",
            "--order-by",
            "id DESC",
            "--limit",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["b2"]


def test_run_query_treats_sql_like_literal_as_value(tmp_path):
    _init_with_topic(tmp_path)
    malicious_value = "running'; DROP TABLE runs; --"
    _add_run(tmp_path, "odd", status=malicious_value)

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--where",
            "status = 'running''; DROP TABLE runs; --'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["odd"]

    result = runner.invoke(
        app, ["run", "list", "--workspace-dir", str(tmp_path / ".expnote"), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["odd"]


def test_run_query_rejects_unsupported_restricted_syntax(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    unsupported = [
        ["--where", "status = 'running' OR id = 'run1'"],
        ["--where", "(status = 'running')"],
        ["--where", "lower(status) = 'running'"],
        ["--where", "metadata.hparams.lr = 0.0003"],
        ["--where", "unknown = 'x'"],
        ["--order-by", "unknown ASC"],
        ["--order-by", "id SIDEWAYS"],
        ["--order-by", "lower(id) ASC"],
    ]
    for fragment in unsupported:
        result = runner.invoke(
            app,
            [
                "run",
                "query",
                "--workspace-dir",
                str(tmp_path / ".expnote"),
                *fragment,
                "--json",
            ],
        )
        assert result.exit_code != 0


def test_run_stats_groups_by_status(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1", status="running")
    _add_run(tmp_path, "run2", status="running")
    _add_run(tmp_path, "run3", status="finished")

    result = runner.invoke(
        app,
        ["run", "stats", "--workspace-dir", str(tmp_path / ".expnote"), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = {row["group"]: row["count"] for row in json.loads(result.output)}
    assert rows == {"running": 2, "finished": 1}


def test_run_stats_groups_by_metadata_key_with_unset_bucket(tmp_path):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "sac1", "--meta", "algo=sac",
        ],
    )
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "sac2", "--meta", "algo=sac",
        ],
    )
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "noalgo",
        ],
    )

    result = runner.invoke(
        app,
        [
            "run", "stats", "--workspace-dir", str(tmp_path / ".expnote"),
            "--group-by", "metadata.algo", "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = {row["group"]: row["count"] for row in json.loads(result.output)}
    assert rows == {"sac": 2, "(unset)": 1}


def test_run_stats_rejects_unwhitelisted_group_by(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run", "stats", "--workspace-dir", str(tmp_path / ".expnote"),
            "--group-by", "unknown", "--json",
        ],
    )
    assert result.exit_code != 0


def test_run_add_from_clones_purpose_and_metadata(tmp_path):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "source",
            "--purpose", "Original purpose",
            "--meta", "algo=sac", "--meta", "seed=1",
        ],
    )

    result = runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--run-id", "clone", "--from", "source",
            "--meta", "seed=2",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["purpose"] == "Original purpose"
    assert data["metadata"] == {"algo": "sac", "seed": "2"}
    assert data["topic_id"] is not None
    assert data["status"] == "running"
    assert data["relation"] == ""
    assert data["result"] == ""


def test_run_add_from_explicit_purpose_overrides_source(tmp_path):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "source",
            "--purpose", "Original purpose",
        ],
    )

    result = runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--run-id", "clone", "--from", "source",
            "--purpose", "New purpose",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["purpose"] == "New purpose"


def test_run_add_from_missing_source_fails(tmp_path):
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--run-id", "clone", "--from", "missing",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_run_add_json_warns_for_manual_metadata_and_wandb_id_mismatch(tmp_path):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "source",
            "--purpose", "source",
            "--meta", "algorithm=sac",
            "--meta", "env_id=Kitchen-v0",
            "--meta", "seed=1",
        ],
    )

    result = runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "manual-id",
            "--purpose", "manual",
            "--meta", "algorithm=sac",
            "--meta", "env_id=Kitchen-v0",
            "--meta", "seed=2",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/wandb-id",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any("run add --from source" in warning for warning in data["warnings"])
    assert any("wandb-id" in warning for warning in data["warnings"])


def test_run_diff_reports_field_and_metadata_differences(tmp_path):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run1",
            "--purpose", "Same purpose", "--status", "running",
            "--meta", "seed=1", "--meta", "only_a=x",
        ],
    )
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run2",
            "--purpose", "Same purpose", "--status", "finished",
            "--meta", "seed=2", "--meta", "only_b=y",
        ],
    )

    result = runner.invoke(
        app,
        [
            "run", "diff", "run1", "run2",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["fields"]["purpose"]["changed"] is False
    assert data["fields"]["status"] == {
        "a": "running", "b": "finished", "changed": True
    }
    assert data["metadata"]["changed"] == {"seed": {"a": "1", "b": "2"}}
    assert data["metadata"]["only_in_a"] == {"only_a": "x"}
    assert data["metadata"]["only_in_b"] == {"only_b": "y"}

    text_result = runner.invoke(
        app,
        ["run", "diff", "run1", "run2", "--workspace-dir", str(tmp_path / ".expnote")],
    )
    assert text_result.exit_code == 0, text_result.output
    assert "status" in text_result.output
    assert "purpose" not in text_result.output


def test_run_diff_missing_run_fails(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run", "diff", "run1", "missing",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert result.exit_code != 0


def test_sync_wandb_status_reports_without_writing_by_default(tmp_path, monkeypatch):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run1", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/run1",
        ],
    )
    monkeypatch.setattr(
        "expnote.cli.fetch_wandb_run_state", lambda url: "finished"
    )

    result = runner.invoke(
        app,
        [
            "sync", "wandb-status",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["mismatched"] == [
        {
            "run_id": "run1",
            "local_status": "running",
            "wandb_status": "finished",
            "suggested_status": "finished",
        }
    ]
    assert data["updated"] == []

    show_result = runner.invoke(
        app,
        [
            "run", "show", "run1", "--field", "status",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert show_result.output.strip() == "running"


def test_run_refresh_dry_run_reports_wandb_summary(tmp_path, monkeypatch):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run1", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/run1",
        ],
    )
    monkeypatch.setattr(
        "expnote.cli.fetch_wandb_run_summary",
        lambda url: {
            "state": "finished",
            "run_path": "entity/project/run1",
            "summary": {"eval/return": 123.456789},
        },
    )

    result = runner.invoke(
        app,
        [
            "run", "refresh", "run1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["suggested_status"] == "finished"
    assert data["result"] == "eval/return=123.457"
    assert data["updated"] == []

    show_result = runner.invoke(
        app,
        [
            "run", "show", "run1", "--workspace-dir", str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    show = json.loads(show_result.output)
    assert show["status"] == "running"
    assert show["result"] == ""


def test_run_refresh_apply_writes_status_result_and_summary(tmp_path, monkeypatch):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run1", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/run1",
        ],
    )
    monkeypatch.setattr(
        "expnote.cli.fetch_wandb_run_summary",
        lambda url: {
            "state": "finished",
            "run_path": "entity/project/run1",
            "summary": {"success_rate": 0.75},
        },
    )

    result = runner.invoke(
        app,
        [
            "run", "refresh", "run1", "--apply",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["updated"] == ["status", "result", "metadata"]

    show_result = runner.invoke(
        app,
        [
            "run", "show", "run1", "--workspace-dir", str(tmp_path / ".expnote"),
            "--json",
        ],
    )
    show = json.loads(show_result.output)
    assert show["status"] == "finished"
    assert show["result"] == "success_rate=0.75"
    assert show["metadata"]["wandb_summary"] == {"success_rate": 0.75}
    assert "run.update" in [event["type"] for event in _events(tmp_path)]


def test_sync_wandb_status_apply_writes_and_logs_event(tmp_path, monkeypatch):
    _init_with_topic(tmp_path)
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "run1", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/run1",
        ],
    )
    monkeypatch.setattr(
        "expnote.cli.fetch_wandb_run_state", lambda url: "crashed"
    )

    result = runner.invoke(
        app,
        [
            "sync", "wandb-status", "--apply",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["updated"] == ["run1"]

    show_result = runner.invoke(
        app,
        [
            "run", "show", "run1", "--field", "status",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert show_result.output.strip() == "failed"
    events = [event["type"] for event in _events(tmp_path)]
    assert events.count("run.update") >= 1


def test_sync_wandb_status_reports_errors_without_aborting(tmp_path, monkeypatch):
    _init_with_topic(tmp_path)
    from expnote.wandb_live import WandbLiveError

    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "bad", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/bad",
        ],
    )
    runner.invoke(
        app,
        [
            "run", "add", "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic", "--run-id", "good", "--status", "running",
            "--meta", "wandb_url=https://wandb.ai/entity/project/runs/good",
        ],
    )

    def fake_fetch(url):
        if "bad" in url:
            raise WandbLiveError("wandb_api_error", "boom")
        return "finished"

    monkeypatch.setattr("expnote.cli.fetch_wandb_run_state", fake_fetch)

    result = runner.invoke(
        app,
        [
            "sync", "wandb-status",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["errors"] == [{"run_id": "bad", "reason": "boom"}]
    assert [row["run_id"] for row in data["mismatched"]] == ["good"]


def _add_benchmark(
    tmp_path: Path, benchmark_id: str = "bench1", title: str = "Bench"
) -> None:
    result = runner.invoke(
        app,
        [
            "benchmark", "add",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--benchmark-id", benchmark_id,
            "--title", title,
        ],
    )
    assert result.exit_code == 0, result.output


def _add_benchmark_task(tmp_path: Path, benchmark_id: str, title: str) -> str:
    result = runner.invoke(
        app,
        [
            "benchmark", "task", "add", benchmark_id,
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--title", title,
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["id"]


def _add_benchmark_algo(tmp_path: Path, benchmark_id: str, title: str) -> str:
    result = runner.invoke(
        app,
        [
            "benchmark", "algo", "add", benchmark_id,
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--title", title,
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["id"]


def _add_finished_run(
    tmp_path: Path, run_id: str, status: str = "finished", result_text: str = "ok"
) -> None:
    result = runner.invoke(
        app,
        [
            "run", "add",
            "--workspace-dir", str(tmp_path / ".expnote"),
            "--topic", "topic",
            "--run-id", run_id,
            "--status", status,
            "--result", result_text,
        ],
    )
    assert result.exit_code == 0, result.output


def test_benchmark_add_and_show(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path, "bench1", "Offline RL Benchmark")

    result = runner.invoke(
        app,
        [
            "benchmark", "show", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "bench1"
    assert data["title"] == "Offline RL Benchmark"
    assert data["tasks"] == []
    assert data["algos"] == []
    assert data["cells"] == []


def test_benchmark_task_and_algo_add_list_ordered_by_position(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_task(tmp_path, "bench1", "hopper")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_benchmark_algo(tmp_path, "bench1", "iql")

    result = runner.invoke(
        app,
        [
            "benchmark", "task", "list", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.output)] == ["antmaze", "hopper"]

    result = runner.invoke(
        app,
        [
            "benchmark", "algo", "list", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.output)] == ["calql", "iql"]


def test_benchmark_task_add_rejects_duplicate_title(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")

    result = runner.invoke(
        app,
        [
            "benchmark", "task", "add", "bench1",
            "--title", "antmaze",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert result.exit_code != 0


def test_benchmark_task_resolves_by_title_or_id_to_same_task(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    task_id = _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1", result_text="82% success")

    result = runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    cells = json.loads(result.output)["cells"]
    assert cells[0]["task_id"] == task_id

    result = runner.invoke(
        app,
        [
            "benchmark", "unlink", "bench1",
            "--task-id", task_id, "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cells"] == []


def test_benchmark_link_rejects_non_terminal_run(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1", status="running", result_text="")

    result = runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert result.exit_code != 0
    assert "terminal" in (result.output + str(result.exception))


def test_benchmark_link_succeeds_with_finished_or_failed_run(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_task(tmp_path, "bench1", "hopper")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1", status="finished", result_text="82% success")
    _add_finished_run(tmp_path, "run2", status="failed", result_text="diverged at 10k")

    for run_id, task in [("run1", "antmaze"), ("run2", "hopper")]:
        link = runner.invoke(
            app,
            [
                "benchmark", "link", "bench1", run_id,
                "--task", task, "--algo", "calql",
                "--workspace-dir", str(tmp_path / ".expnote"), "--json",
            ],
        )
        assert link.exit_code == 0, link.output

    result = runner.invoke(
        app,
        [
            "benchmark", "matrix", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["cells"]) == 2
    statuses = {c["run_id"]: c["status"] for c in data["cells"]}
    assert statuses == {"run1": "finished", "run2": "failed"}


def test_benchmark_link_replaces_run_on_relink(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1", result_text="result run1")
    _add_finished_run(tmp_path, "run2", result_text="result run2")

    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    result = runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run2",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    cells = json.loads(result.output)["cells"]
    assert len(cells) == 1
    assert cells[0]["run_id"] == "run2"

    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM benchmark_cells WHERE deleted_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_benchmark_unlink_clears_cell(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1")
    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )

    result = runner.invoke(
        app,
        [
            "benchmark", "unlink", "bench1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cells"] == []

    result = runner.invoke(
        app,
        [
            "benchmark", "matrix", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert json.loads(result.output)["cells"] == []


def test_benchmark_matrix_text_and_json_output(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_task(tmp_path, "bench1", "hopper")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_benchmark_algo(tmp_path, "bench1", "iql")
    _add_finished_run(tmp_path, "run1", result_text="82% success")
    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )

    result = runner.invoke(
        app,
        [
            "benchmark", "matrix", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "antmaze" in result.output
    assert "hopper" in result.output
    assert "calql" in result.output
    assert "iql" in result.output
    assert "run1" in result.output
    assert "—" in result.output

    result = runner.invoke(
        app,
        [
            "benchmark", "matrix", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {t["title"] for t in data["tasks"]} == {"antmaze", "hopper"}
    assert {a["title"] for a in data["algos"]} == {"calql", "iql"}
    assert len(data["cells"]) == 1


def test_benchmark_delete_cascades_to_tasks_algos_cells(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1")
    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )

    result = runner.invoke(
        app,
        [
            "benchmark", "delete", "bench1",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(tmp_path / ".expnote" / "expnote.sqlite")
    try:
        tasks = conn.execute(
            "SELECT COUNT(*) FROM benchmark_tasks WHERE deleted_at IS NULL"
        ).fetchone()[0]
        algos = conn.execute(
            "SELECT COUNT(*) FROM benchmark_algos WHERE deleted_at IS NULL"
        ).fetchone()[0]
        cells = conn.execute(
            "SELECT COUNT(*) FROM benchmark_cells WHERE deleted_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert (tasks, algos, cells) == (0, 0, 0)


def test_benchmark_events_use_expected_type_names(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path)
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1")
    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )
    runner.invoke(
        app,
        [
            "benchmark", "unlink", "bench1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )

    event_types = {event["type"] for event in _events(tmp_path)}
    assert {
        "benchmark.add",
        "benchmark_task.add",
        "benchmark_algo.add",
        "benchmark.link",
        "benchmark.unlink",
    } <= event_types


def test_sync_markdown_writes_benchmark_note(tmp_path):
    _init_with_topic(tmp_path)
    _add_benchmark(tmp_path, "bench1", "Offline RL Benchmark")
    _add_benchmark_task(tmp_path, "bench1", "antmaze")
    _add_benchmark_algo(tmp_path, "bench1", "calql")
    _add_finished_run(tmp_path, "run1", result_text="82% success")
    runner.invoke(
        app,
        [
            "benchmark", "link", "bench1", "run1",
            "--task", "antmaze", "--algo", "calql",
            "--workspace-dir", str(tmp_path / ".expnote"),
        ],
    )

    result = runner.invoke(
        app,
        ["sync", "all", "--workspace-dir", str(tmp_path / ".expnote")],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["benchmark_notes"] == 1

    notes_dir = tmp_path / "notes" / "runs"
    benchmark_path = notes_dir / "bench1.md"
    assert benchmark_path.exists()
    text = benchmark_path.read_text(encoding="utf-8")
    assert "antmaze" in text
    assert "calql" in text
    assert "[[run1]]" in text
    assert "(finished)" not in text
    assert "82% success" not in text
