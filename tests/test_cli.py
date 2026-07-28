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
) -> None:
    result = runner.invoke(
        app,
        [
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
        ],
    )
    assert result.exit_code == 0, result.output


def _events(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (tmp_path / ".expnote" / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]


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
    assert updated["status"] == "finished"
    assert updated["metadata"] == {"algo": "sac", "seed": "1"}

    result = runner.invoke(
        app, ["run", "show", "run1", "--root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown["topic_title"] == "topic"
    assert shown["metadata"] == {"algo": "sac", "seed": "1"}

    result = runner.invoke(app, ["run", "list", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == ["run1"]
    assert rows[0]["status"] == "finished"


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
