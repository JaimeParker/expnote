"""Project decision isolation through the normal CLI and Research web entry points."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from decisionkit import decision_record, seed_records
from fastapi.testclient import TestClient
from minivault import DECISIONS_PATH
from writekit import registered_vault, run

from expnote.research.decision_store import DecisionStore, store_path
from expnote.research.registry import open_project
from expnote.research.web_routes import create_research_app


def ok(*args):
    response = run(*args)
    assert response["_exit"] == 0, response
    return response["data"]


def new(scope, title):
    return ok(
        "decision", "new", "--scope", scope, "--layer", "research", "--title",
        title, "--decided", title, "--reason", "test", "--plans", "PLAN-001",
        "--apply",
    )["id"]  # fmt: skip


def test_new_split_project_limits_home_bundles_search_and_todos(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    project = vault / "Study"
    project.mkdir()
    ok("project", "init", "--project-dir", project, "--layout", "split", "--apply")
    ok("new", "plan", "--id", "PLAN-001", "--title", "Project plan", "--apply")
    retained = new("research", "shared-token research decision")
    engineering = new("engineering", "shared-token engineering decision")

    assert [r["id"] for r in ok("decision", "list")["decisions"]] == [retained]
    assert [
        r["id"] for r in ok("decision", "list", "--scope", "engineering")["decisions"]
    ] == [engineering]
    assert ok("decision", "show", engineering)["decision"]["scope"] == "engineering"
    args = ("plan", "bundle", "PLAN-001", "--sections", "decisions")
    assert [r["id"] for r in ok(*args)["decisions"]] == [retained]
    assert [
        r["id"] for r in ok(*args, "--decision-scope", "engineering")["decisions"]
    ] == [engineering]
    query = ("search", "shared-token", "--type", "decision")
    assert [r["entityId"] for r in ok(*query)["hits"]] == [retained]
    assert [
        r["entityId"] for r in ok(*query, "--decision-scope", "engineering")["hits"]
    ] == [engineering]
    assert engineering not in str(ok("todo", "list"))
    assert engineering not in str(ok("next"))
    for command in (
        ("plan", "submit", "PLAN-001", "--decision", engineering),
        (
            "decision",
            "set",
            engineering,
            "--status",
            "confirmed",
            "--authorization",
            "yes",
        ),
    ):
        denied = run(*command, "--apply")
        assert denied["_exit"] == 1 and "DEC.SCOPE" in str(denied), denied

    project_id = open_project(None).entry.id
    base = f"/api/research/projects/{project_id}"
    with TestClient(create_research_app()) as client:
        home = client.get(base + "/home").json()["data"]["decisions"]
        assert home["total"] == 1 and home["awaiting"][0]["id"] == retained
        index = client.get(base + "/decisions").json()["data"]
        assert [d["id"] for d in index["decisions"]] == [retained]
        index = client.get(base + "/decisions?scope=engineering").json()["data"]
        assert [d["id"] for d in index["decisions"]] == [engineering]
        assert client.get(base + "/decisions/" + engineering).status_code == 200

    with DecisionStore.open(project_id) as store:
        assert store.conn.execute("SELECT id FROM decisions").fetchone()[0] == retained
        assert (
            store.conn.execute("SELECT id FROM engineering_decisions").fetchone()[0]
            == engineering
        )
        assert store.max_number() == 2

    ok(
        "decision",
        "set",
        engineering,
        "--scope",
        "engineering",
        "--status",
        "confirmed",
        "--authorization",
        "Confirm the runtime choice",
        "--apply",
    )
    denied = run("plan", "submit", "PLAN-001", "--decision", engineering, "--apply")
    assert denied["_exit"] == 1 and "DEC.SCOPE" in str(denied)
    spec = tmp_path / "workflow.json"
    spec.write_text(
        json.dumps(
            {
                "deliverables": [
                    {
                        "id": "test",
                        "question": "Does the study replicate?",
                        "criteria": "PLAN",
                        "review": "agent",
                        "files": ["result"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    ok(
        "plan",
        "submit",
        "PLAN-001",
        "--decision",
        retained,
        "--workflow",
        spec,
        "--apply",
    )
    ok(
        "decision",
        "set",
        retained,
        "--status",
        "confirmed",
        "--authorization",
        "Confirm the research scope",
        "--apply",
    )
    created = ok(
        "new",
        "exec",
        "--plan",
        "PLAN-001",
        "--stage",
        "S1",
        "--title",
        "Run the approved study",
        "--apply",
    )
    path = Path(created["created"][0]["absPath"])
    heading = open_project(None).profile.heading_rules
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"## {heading}", f"## {heading}\n\nFollow the approved rules."
        ),
        encoding="utf-8",
    )
    frozen = ok(
        "exec", "freeze", "PLAN-001-S1", "--self-approve", "--no-new-rules", "--apply"
    )
    freeze_id = frozen["decision"]
    assert ok("decision", "show", freeze_id)["decision"]["scope"] == "engineering"
    engineering_export = (
        open_project(None).resolved.material_dir / "ops/engineering-decisions.jsonl"
    )
    assert freeze_id in engineering_export.read_text(encoding="utf-8")
    bundle = ok("plan", "bundle", "PLAN-001", "--sections", "decisions,execPlans")
    assert [d["id"] for d in bundle["decisions"]] == [retained]
    assert bundle["execPlans"][0]["rulesDecision"] == freeze_id


def test_migration_and_scoped_restore_preserve_history(tmp_path, monkeypatch):
    _, project = registered_vault(tmp_path)
    (project / DECISIONS_PATH).write_text("# Decisions\n", encoding="utf-8")
    archive = project / "ops" / "Engineering archive.md"
    archive.parent.mkdir(exist_ok=True)
    original = (
        "---\ntype: engineering-decision-log\nrecord_id: archived-decisions\n---\n"
        "# Engineering archive\n\n"
        "| # | Decision |\n|---|---|\n| 6 | full-running-history |\n"
    )
    archive.write_text(original, encoding="utf-8")
    seed_records(
        decision_record(1, title="Study decision"),
        decision_record(6, title="Frozen runtime", layer="工程"),
    )
    # A present mixed export must be backed up and replaced.
    ok("decision", "export", "--apply")
    manifest = tmp_path / "scopes.json"
    selection = {
        "decisions": {
            "O2O-DEC-0001": {"scope": "research", "version": 1},
            "O2O-DEC-0006": {"scope": "engineering", "version": 2},
        },
        "archive": {
            "path": "ops/Engineering archive.md",
            "sha256": hashlib.sha256(original.encode()).hexdigest(),
        },
    }
    args = ("decision", "migrate-scopes", "--from-file", manifest)
    manifest.write_text(json.dumps(selection), encoding="utf-8")
    stale = run(*args, "--apply")
    assert stale["_exit"] == 5, stale
    selection["decisions"]["O2O-DEC-0006"]["version"] = 1
    manifest.write_text(json.dumps(selection), encoding="utf-8")
    preview = run(*args)
    assert preview["_exit"] == 4, preview
    assert len(ok("decision", "list")["decisions"]) == 2

    from expnote.research import decision_scopes

    with monkeypatch.context() as undo:

        def fail(*args, **kwargs):
            raise OSError("injected backup failure")

        undo.setattr(decision_scopes, "_backup", fail)
        failed = run(*args, "--apply")
        assert failed["_exit"] == 6, failed
    assert len(ok("decision", "list")["decisions"]) == 2
    sync_export = decision_scopes.sync_export
    with monkeypatch.context() as undo:

        def block_engineering(*args, scope, **kwargs):
            if scope == "engineering":
                raise OSError("injected engineering export failure")
            return sync_export(*args, scope=scope, **kwargs)

        undo.setattr(decision_scopes, "sync_export", block_engineering)
        output = run(*args, "--apply")
        assert output["_exit"] == 0 and output["warnings"][0]["code"] == "EXPORT_IO"
        migrated = output["data"]
    assert ok(*args, "--apply")["applied"] is True
    backup = Path(migrated["backup"])
    assert (backup / "decisions.sqlite").is_file()
    assert (backup / "decisions.jsonl").is_file()
    assert (backup / "archive.md").read_text(encoding="utf-8") == original
    assert ok(*args, "--apply")["applied"] is False

    ident = "O2O-DEC-0006"
    item = ok("decision", "show", ident)["decision"]
    assert "full-running-history" in item["historicalSource"]
    assert item["history"][0]["action"] == "import"
    assert ok("search", "full-running-history")["hits"] == []
    engineering_hits = ok(
        "search",
        "full-running-history",
        "--type",
        "decision",
        "--decision-scope",
        "engineering",
    )["hits"]
    assert [r["entityId"] for r in engineering_hits] == [ident]
    with TestClient(create_research_app()) as client:
        base = "/api/research/projects/off2on"
        detail = client.get(f"{base}/decisions/{ident}").json()["data"]
        assert "full-running-history" in detail["decision"]["historicalSource"]
        old = client.get(f"{base}/note/Engineering%20archive").json()["data"]
        assert ident in old["html"]

    target = project / "ops" / "engineering-decisions.jsonl"
    assert ident in target.read_text(encoding="utf-8")
    research = (project / "ops" / "decisions.jsonl").read_text(encoding="utf-8")
    assert ident not in research
    wrong = run("decision", "restore", "--from", target, "--replace", "--apply")
    assert wrong["_exit"] == 2, wrong
    ok(
        "decision",
        "restore",
        "--from",
        target,
        "--scope",
        "engineering",
        "--replace",
        "--apply",
    )
    assert ok("decision", "show", ident)["decision"]["history"] == item["history"]
    assert (project / "ops" / "decisions.jsonl").read_text(encoding="utf-8") == research
    assert store_path("off2on").is_file()
