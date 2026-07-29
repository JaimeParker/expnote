from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app

runner = CliRunner()


def _init_with_topic(tmp_path: Path, topic: str = "topic") -> None:
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["topic", "add", topic, "--root", str(tmp_path)]).exit_code
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
        "--root",
        str(tmp_path),
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
        for line in (tmp_path / ".expnote" / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
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
    assert "--state-dir" in data["required_flags"]
    assert "create_run" in data["workflows"]
    assert "run.show" in data["commands"]
    assert "sync markdown --pull-analysis" in json.dumps(data)
    assert "moc diff" in json.dumps(data)


def test_guide_agent_human_output_mentions_core_workflow():
    result = runner.invoke(app, ["guide", "agent"])

    assert result.exit_code == 0, result.output
    assert "SQLite is the source of truth" in result.output
    assert "Markdown is a projection" in result.output
    assert "init -> topic add -> run add -> moc add -> sync markdown" in result.output
    assert "expnote validate --json" in result.output


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
            "--root",
            str(root),
            "--state-dir",
            str(state_dir),
            "--notes-dir",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training/runs",
            "--moc-path",
            "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["root"] == str(root.resolve())
    assert data["state_dir"] == str(state_dir.resolve())
    assert (state_dir / "expnote.sqlite").exists()
    assert (state_dir / "events.jsonl").exists()
    assert (state_dir / "config.toml").exists()
    assert not (root / ".expnote").exists()
    assert (
        root / "10 Projects" / "AI Lab RFT 项目" / "ManiSkill Training" / "runs"
    ).exists()

    config = (state_dir / "config.toml").read_text(encoding="utf-8")
    assert f'root = "{root.resolve()}"' in config
    assert f'state_dir = "{state_dir.resolve()}"' in config


def test_existing_schema_migrates_on_cli_use(tmp_path):
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
        app, ["run", "show", "old1", "--root", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["analysis"] == ""


def test_external_state_dir_supports_cli_workflow(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"
    common = ["--root", str(root), "--state-dir", str(state_dir)]

    result = runner.invoke(app, ["init", *common, "--json"])
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
    assert json.loads(result.output)["counts"] == {
        "artifacts": 0,
        "runs": 1,
        "topics": 1,
    }

    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert [event["type"] for event in events] == ["init", "topic.add", "run.add"]


def test_external_state_dir_must_be_reused_for_follow_up_commands(tmp_path):
    root = tmp_path / "vault"
    state_dir = tmp_path / "state"

    result = runner.invoke(
        app, ["init", "--root", str(root), "--state-dir", str(state_dir)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "list", "--root", str(root), "--json"])
    assert result.exit_code != 0
    assert not (root / ".expnote").exists()


def test_init_add_query_and_soft_delete(tmp_path):
    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".expnote" / "expnote.sqlite").exists()

    result = runner.invoke(
        app,
        ["topic", "add", "StackCube ablation", "--root", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "run",
            "add",
            "--root",
            str(tmp_path),
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
            "--root",
            str(tmp_path),
            "--where",
            "status = 'running'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["abc123"]

    result = runner.invoke(
        app, ["run", "delete", "abc123", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["run", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_duplicate_run_id_errors(tmp_path):
    _init_with_topic(tmp_path)
    args = [
        "run",
        "add",
        "--root",
        str(tmp_path),
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
            "--root",
            str(tmp_path),
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
        app, ["topic", "delete", "new", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []

    result = runner.invoke(
        app,
        [
            "topic",
            "list",
            "--root",
            str(tmp_path),
            "--include-deleted",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["title"] == "new"
    assert rows[0]["deleted_at"] is not None


def test_run_show_update_list_and_metadata_merge(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app,
        [
            "run",
            "update",
            "run1",
            "--root",
            str(tmp_path),
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
        app, ["run", "show", "run1", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown["topic_title"] == "topic"
    assert shown["analysis"] == "updated analysis"
    assert shown["metadata"] == {"algo": "sac", "seed": "1"}

    result = runner.invoke(app, ["run", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["run1"]
    assert rows[0]["status"] == "finished"


def test_run_show_field_outputs_single_public_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app, ["run", "show", "run1", "--root", str(tmp_path), "--field", "purpose"]
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
            "--root",
            str(tmp_path),
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
        ["run", "show", "run1", "--root", str(tmp_path), "--field", "metadata"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"algo": "sac"}


def test_run_show_field_supports_topic_alias(tmp_path):
    _init_with_topic(tmp_path, "StackCube SAC")
    _add_run(tmp_path, "run1", topic="StackCube SAC")

    result = runner.invoke(
        app, ["run", "show", "run1", "--root", str(tmp_path), "--field", "topic"]
    )

    assert result.exit_code == 0, result.output
    assert result.output == "StackCube SAC\n"


def test_run_show_field_rejects_unknown_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    result = runner.invoke(
        app, ["run", "show", "run1", "--root", str(tmp_path), "--field", "unknown"]
    )

    assert result.exit_code != 0
    assert "supported fields:" in result.output


def test_run_delete_hides_run_from_list_and_query(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "deleted")
    _add_run(tmp_path, "active")

    result = runner.invoke(
        app, ["run", "delete", "deleted", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["run", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["active"]

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--root",
            str(tmp_path),
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
            "--root",
            str(tmp_path),
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
        app, ["artifact", "list", "run1", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == [artifact["id"]]
    assert rows[0]["note"] == "best model"

    result = runner.invoke(
        app,
        ["artifact", "delete", artifact["id"], "--root", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["artifact", "list", "run1", "--root", str(tmp_path), "--json"]
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
            "--root",
            str(tmp_path),
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
        ["relation", "delete", relation["id"], "--root", str(tmp_path), "--json"],
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
            "--root",
            str(tmp_path),
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
            ["run", "query", "--root", str(tmp_path), *fragment, "--json"],
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
            "--root",
            str(tmp_path),
            "--where",
            "status = 'running' AND id = 'abc123'",
            "--order-by",
            "id ASC",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["abc123"]


def test_run_query_supports_analysis_field(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "abc123", analysis="useful analysis")
    _add_run(tmp_path, "other", analysis="different")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--root",
            str(tmp_path),
            "--where",
            "analysis = 'useful analysis'",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["abc123"]


def test_moc_add_list_remove_and_diff(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    moc_path = "Inbox/Test MOC.md"

    result = runner.invoke(
        app,
        [
            "moc",
            "add",
            "moc1",
            "--root",
            str(tmp_path),
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
            "moc",
            "list",
            "--root",
            str(tmp_path),
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
            "moc",
            "diff",
            "--root",
            str(tmp_path),
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
            "moc",
            "remove",
            "moc1",
            "--root",
            str(tmp_path),
            "--moc-path",
            moc_path,
            "--section",
            "StackCube",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[[moc1]]" not in (tmp_path / moc_path).read_text(encoding="utf-8")


def test_moc_update_normalizes_positions(tmp_path):
    _init_with_topic(tmp_path)
    for run_id in ["a", "b", "c"]:
        _add_run(tmp_path, run_id, purpose=f"purpose {run_id}")
    moc_path = "Inbox/Test MOC.md"
    common = [
        "--root",
        str(tmp_path),
        "--moc-path",
        moc_path,
        "--section",
        "StackCube",
    ]

    for run_id in ["a", "b", "c"]:
        result = runner.invoke(app, ["moc", "add", run_id, *common, "--json"])
        assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["moc", "list", *common, "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["run_id"] for row in rows] == ["a", "b", "c"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(
        app,
        ["moc", "update", "c", *common, "--position", "1", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["c", "a", "b"]
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert _moc_table_ids(tmp_path / moc_path) == ["c", "a", "b"]

    result = runner.invoke(
        app,
        ["moc", "update", "a", *common, "--position", "99", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["c", "b", "a"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(
        app,
        ["moc", "update", "a", *common, "--position", "0", "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["a", "c", "b"]
    assert [row["position"] for row in rows] == [1, 2, 3]

    result = runner.invoke(app, ["moc", "remove", "c", *common, "--json"])
    assert result.exit_code == 0, result.output
    rows = _moc_rows(tmp_path, moc_path, "StackCube")
    assert [row["run_id"] for row in rows] == ["a", "b"]
    assert [row["position"] for row in rows] == [1, 2]

    result = runner.invoke(
        app,
        ["moc", "update", "missing", *common, "--position", "1", "--json"],
    )
    assert result.exit_code != 0


def test_moc_diff_reports_manual_table_changes(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "moc1", purpose="p1")
    moc_path = "Inbox/Test MOC.md"
    result = runner.invoke(
        app,
        [
            "moc",
            "add",
            "moc1",
            "--root",
            str(tmp_path),
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
            "moc",
            "diff",
            "--root",
            str(tmp_path),
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


def test_run_query_supports_topic_alias_and_limit(tmp_path):
    _init_with_topic(tmp_path, "topic a")
    result = runner.invoke(app, ["topic", "add", "topic b", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _add_run(tmp_path, "a1", topic="topic a")
    _add_run(tmp_path, "b1", topic="topic b")
    _add_run(tmp_path, "b2", topic="topic b")

    result = runner.invoke(
        app,
        [
            "run",
            "query",
            "--root",
            str(tmp_path),
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
            "--root",
            str(tmp_path),
            "--where",
            "status = 'running''; DROP TABLE runs; --'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["odd"]

    result = runner.invoke(app, ["run", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.output)] == ["odd"]


def test_run_query_rejects_unsupported_restricted_syntax(tmp_path):
    _init_with_topic(tmp_path)
    _add_run(tmp_path, "run1")

    unsupported = [
        ["--where", "status = 'running' OR id = 'run1'"],
        ["--where", "(status = 'running')"],
        ["--where", "lower(status) = 'running'"],
        ["--where", "metadata.algo = 'sac'"],
        ["--where", "unknown = 'x'"],
        ["--order-by", "unknown ASC"],
        ["--order-by", "id SIDEWAYS"],
        ["--order-by", "lower(id) ASC"],
    ]
    for fragment in unsupported:
        result = runner.invoke(
            app,
            ["run", "query", "--root", str(tmp_path), *fragment, "--json"],
        )
        assert result.exit_code != 0
