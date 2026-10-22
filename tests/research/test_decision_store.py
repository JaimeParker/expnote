"""The decision store: transitions, immutability, version conflicts, export, restore."""

from __future__ import annotations

import json
import sqlite3

import pytest
from decisionkit import decision_record, seed
from writekit import audit_records, data, invoke, registered_vault, run

from expnote.research.decision_export import (
    export_bytes,
    parse_export,
    restore_rows,
)
from expnote.research.decision_store import (
    DecisionStore,
    read_rows,
    store_exists,
    store_path,
)
from expnote.research.errors import ResearchError

PID = "off2on"
USER = {"confirmed_by": "user"}


@pytest.fixture
def store(tmp_path):
    with DecisionStore.open("unit") as opened:
        yield opened


def proposed(number=1, **overrides):
    return decision_record(
        number, status="proposed", confirmed_by=None, confirmed_on=None, **overrides
    )


# ---- the store itself ----------------------------------------------------------------


def test_a_proposal_moves_to_confirmed_and_every_step_is_an_event(store):
    with store.transaction():
        store.insert(proposed(), actor="cli", at="2026-10-22T01:00:00Z")
    with store.transaction():
        row = store.transition(
            1, "confirmed", expected_version=1, actor="gui", at="2026-10-22T02:00:00Z",
            confirmed_by="user",
        )  # fmt: skip
    assert (row["status"], row["version"]) == ("confirmed", 2)
    assert row["confirmed_by"] == "user" and row["confirmed_on"] == "2026-10-22"
    assert row["updated_at"].endswith("02:00:00Z")
    assert row["created_at"].endswith("01:00:00Z")
    first, second = store.events(1)
    assert (first["action"], first["actor"]) == ("propose", "cli")
    assert first["before"] is None
    assert (second["action"], second["actor"]) == ("approve", "gui")
    assert second["before"] == {"status": "proposed", "version": 1}
    assert second["after"]["status"] == "confirmed" and second["after"]["version"] == 2


def test_a_recorded_confirmation_needs_who_and_the_users_words(store):
    record = decision_record(1, confirmed_by="agent-for-user", authorization=None)
    with pytest.raises(ResearchError) as error, store.transaction():
        store.insert(record, actor="cli")
    assert error.value.code == "GATE_FAILED"
    with store.transaction():
        row = store.insert({**record, "authorization": "用户原话"}, actor="cli")
    assert row["status"] == "confirmed"
    assert store.events(1)[0]["action"] == "record"


def test_only_the_lifecycle_moves_are_allowed(store):
    with store.transaction():
        store.insert(proposed(1), actor="t")
        store.insert(proposed(2), actor="t")
        store.insert(decision_record(3), actor="t")
    moves = [
        (1, "superseded"),  # proposed cannot be superseded
        (2, "proposed"),
        (3, "rejected"),  # confirmed cannot be rejected
        (3, "confirmed"),
    ]
    for number, target in moves:
        with pytest.raises(ResearchError) as error, store.transaction():
            store.transition(
                number, target, expected_version=1, actor="t", confirmed_by="user",
                superseded_by=2,
            )  # fmt: skip
        assert error.value.code == "GATE_FAILED", (number, target)
    with store.transaction():
        store.transition(1, "rejected", expected_version=1, actor="t", **USER)
    with pytest.raises(ResearchError), store.transaction():
        store.transition(1, "confirmed", expected_version=2, actor="t", **USER)
    with pytest.raises(ResearchError):  # superseding needs a successor
        with store.transaction():
            store.transition(3, "superseded", expected_version=1, actor="t")
    with store.transaction():
        row = store.transition(
            3, "superseded", expected_version=1, actor="t", superseded_by=2
        )
    assert row["superseded_by"] == 2 and row["status"] == "superseded"


def test_an_agent_decision_needs_the_users_words_at_every_gate(store):
    with store.transaction():
        store.insert(proposed(), actor="t")
    with pytest.raises(ResearchError) as error, store.transaction():
        store.transition(
            1, "confirmed", expected_version=1, actor="t",
            confirmed_by="agent-for-user", authorization="  ",
        )  # fmt: skip
    assert error.value.details["gates"][0]["code"] == "DEC.AGENT_NO_AUTH"
    assert store.get(1)["status"] == "proposed"  # the failed transaction rolled back


def test_a_stale_version_is_a_conflict_and_changes_nothing(store):
    with store.transaction():
        store.insert(proposed(), actor="t")
    with pytest.raises(ResearchError) as error, store.transaction():
        store.transition(1, "confirmed", expected_version=3, actor="t", **USER)
    assert error.value.code == "CONFLICT" and error.value.exit_code == 5
    assert error.value.details == {"expectedVersion": 3, "currentVersion": 1}
    assert store.get(1)["version"] == 1 and len(store.events(1)) == 1
    with pytest.raises(ResearchError) as missing, store.transaction():
        store.transition(9, "confirmed", expected_version=1, actor="t", **USER)
    assert missing.value.code == "NOT_FOUND"


def test_a_taken_number_is_a_conflict(store):
    with store.transaction():
        store.insert(proposed(), actor="t")
    with pytest.raises(ResearchError) as error, store.transaction():
        store.insert(proposed(), actor="t")
    assert error.value.code == "CONFLICT"
    assert store.max_number() == 1 and store.count() == 1


def test_text_columns_are_immutable_for_raw_sql_too(store):
    with store.transaction():
        store.insert(proposed(), actor="t")
    for column in ("title", "decided", "reason", "options", "lands", "plans", "date"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.conn.execute(f"UPDATE decisions SET {column}='x' WHERE number=1")
    with pytest.raises(sqlite3.IntegrityError, match="illegal"):
        store.conn.execute("UPDATE decisions SET status='superseded' WHERE number=1")
    assert store.get(1)["decided"] == "做 A。"


def test_a_dry_run_rolls_everything_back(store):
    with store.dry_run():
        store.insert(proposed(), actor="t")
        assert store.count() == 1
    assert store.count() == 0 and store.events() == []


def test_reading_never_creates_the_store(tmp_path):
    assert read_rows("never-written") is None and not store_exists("never-written")
    assert not store_path("never-written").exists()
    with DecisionStore.open_for_preview("never-written") as preview:
        with preview.dry_run():
            preview.insert(proposed(), actor="t")
    assert not store_path("never-written").exists()  # a preview is in memory
    seed(1, project_id="made")
    rows, events = read_rows("made")
    assert [r["number"] for r in rows] == [1] and events[0]["action"] == "import"


# ---- the export ----------------------------------------------------------------------


def test_export_bytes_are_sorted_deterministic_and_parse_back(store):
    with store.transaction():
        store.insert(proposed(2), actor="t", at="2026-10-22T00:00:00Z")
        legacy = decision_record(1, legacy=True)
        store.insert(legacy, actor="t", at="2026-10-22T00:00:00Z")
    data_a = export_bytes(store.rows(), store.events())
    assert data_a == export_bytes(list(reversed(store.rows())), store.events())
    kinds = [json.loads(line)["kind"] for line in data_a.decode().splitlines()]
    assert kinds == ["decision", "decision", "event", "event"]
    numbers = [json.loads(line).get("number") for line in data_a.decode().splitlines()]
    assert numbers[:2] == [1, 2]
    rows, events = parse_export(data_a)
    assert rows == store.rows() and events == store.events()


def test_parse_export_refuses_malformed_files():
    malformed = (b"not json\n", b'{"kind":"alien"}\n', b'{"kind":"decision"}\n')
    for bad in (*malformed, b"[1]\n"):
        with pytest.raises(ResearchError) as error:
            parse_export(bad)
        assert error.value.code == "EXPORT_INVALID"


def test_restore_rows_round_trips_into_a_fresh_store(store, tmp_path):
    with store.transaction():
        store.insert(proposed(1, supersedes=[]), actor="cli")
        store.insert(decision_record(2, plans=["P"], targets=["[[a]]"]), actor="cli")
        store.transition(1, "rejected", expected_version=1, actor="gui", **USER)
    rows, events = parse_export(export_bytes(store.rows(), store.events()))
    with DecisionStore.open("fresh") as fresh:
        with fresh.transaction():
            assert restore_rows(fresh, rows, events, replace=False) == 2
        assert fresh.rows() == store.rows() and fresh.events() == store.events()
        with pytest.raises(ResearchError) as error, fresh.transaction():
            restore_rows(fresh, rows, events, replace=False)
        assert error.value.code == "STORE_NOT_EMPTY" and error.value.exit_code == 5
        with fresh.transaction():
            restore_rows(fresh, rows, events, replace=True)
        assert fresh.rows() == store.rows()
        with pytest.raises(sqlite3.IntegrityError):  # triggers survive a restore
            fresh.conn.execute("UPDATE decisions SET decided='x' WHERE number=1")


# ---- the export file through the CLI -------------------------------------------------


@pytest.fixture
def world(tmp_path):
    return registered_vault(tmp_path)


def new(title="T"):
    return run(
        "decision", "new", "--title", title, "--layer", "研究", "--decided", "做 A。",
        "--reason", "因为 B。", "--apply",
    )  # fmt: skip


def test_the_export_is_written_after_each_change_and_skipped_when_identical(world):
    root, proj = world
    export = proj / "ops" / "decisions.jsonl"
    assert not export.exists()
    new("one")
    first = export.read_bytes()
    assert first.count(b'"kind": "decision"') == 1
    stamp = export.stat().st_mtime_ns
    events = len(audit_records())
    again = run("decision", "export", "--apply")
    assert again["_exit"] == 0 and again["data"]["changes"] == []
    assert export.stat().st_mtime_ns == stamp and len(audit_records()) == events
    dry = run("decision", "export")
    assert dry["_exit"] == 0 and dry["data"]["dryRun"] is False  # nothing to do
    new("two")
    assert export.read_bytes() != first and export.read_bytes().startswith(first[:20])
    assert audit_records()[-1]["payload"]["op"] == "export"


def test_export_dry_run_shows_the_diff_when_the_file_is_missing(world):
    root, proj = world
    new()
    export = proj / "ops" / "decisions.jsonl"
    export.unlink()
    dry = run("decision", "export")
    assert dry["_exit"] == 4 and dry["data"]["changes"][0]["path"].endswith(".jsonl")
    assert not export.exists()
    done = run("decision", "export", "--apply")
    assert done["_exit"] == 0 and export.exists()


def test_a_changed_export_is_diverged_until_forced(world):
    root, proj = world
    new("one")
    export = proj / "ops" / "decisions.jsonl"
    export.write_text("# someone edited this\n", encoding="utf-8")
    second = new("two")
    assert second["_exit"] == 0  # the decision is recorded regardless
    assert [w["code"] for w in second["warnings"]] == ["EXPORT_DIVERGED"]
    assert export.read_text(encoding="utf-8") == "# someone edited this\n"
    refused = run("decision", "export", "--apply")
    assert refused["_exit"] == 5 and refused["error"]["code"] == "EXPORT_DIVERGED"
    preview = run("decision", "export", "--force")
    assert preview["_exit"] == 4 and preview["warnings"] == []
    forced = run("decision", "export", "--apply", "--force")
    assert forced["_exit"] == 0 and forced["data"]["applied"] is True
    assert export.read_bytes().count(b'"kind": "decision"') == 2
    assert data("doctor")["checks"][-1]["status"] == "ok"


def test_doctor_reports_the_store_and_whether_the_export_is_current(world):
    root, proj = world
    checks = {c["id"]: c for c in data("doctor")["checks"]}
    assert checks["decision-store"]["status"] == "info"
    new()
    checks = {c["id"]: c for c in data("doctor")["checks"]}
    assert checks["decision-store"]["status"] == "ok"
    assert "1 decision(s)" in checks["decision-store"]["message"]
    assert str(store_path(PID)) in checks["decision-store"]["message"]
    export = proj / "ops" / "decisions.jsonl"
    export.write_text("edited\n", encoding="utf-8")
    checks = {c["id"]: c for c in data("doctor")["checks"]}
    assert checks["decision-store"]["status"] == "warn"
    assert "EXPORT_DIVERGED" in checks["decision-store"]["message"]
    export.unlink()
    checks = {c["id"]: c for c in data("doctor")["checks"]}
    assert "EXPORT_MISSING" in checks["decision-store"]["message"]


def test_restore_rebuilds_an_identical_store_from_the_export(world, tmp_path):
    root, proj = world
    a = new("one")["data"]["id"]
    new("two")
    run(
        "decision", "set", a, "--status", "confirmed",
        "--authorization", "用户原话：好", "--apply",
    )  # fmt: skip
    before = read_rows(PID)
    backup = tmp_path / "decisions.jsonl"
    backup.write_bytes((proj / "ops" / "decisions.jsonl").read_bytes())
    refused = run("decision", "restore", "--from", backup, "--apply")
    assert refused["_exit"] == 5 and refused["error"]["code"] == "STORE_NOT_EMPTY"
    assert read_rows(PID) == before
    dry = run("decision", "restore", "--from", backup, "--replace")
    assert dry["_exit"] == 4 and dry["data"]["rows"] == 2
    store_path(PID).unlink()  # the machine's data home is lost
    (proj / "ops" / "decisions.jsonl").unlink()
    done = run("decision", "restore", "--from", backup, "--apply")
    assert done["_exit"] == 0 and done["data"]["rows"] == 2 and done["warnings"] == []
    assert read_rows(PID) == before  # identical rows and events
    assert (proj / "ops" / "decisions.jsonl").read_bytes() == backup.read_bytes()
    # the restored store is a working store: it still enforces the rules
    again = run(
        "decision", "set", a, "--status", "rejected", "--authorization", "用户原话",
        "--apply",
    )  # fmt: skip
    assert again["_exit"] == 1
    replaced = run("decision", "restore", "--from", backup, "--replace", "--apply")
    assert replaced["_exit"] == 0 and read_rows(PID) == before


def test_restore_refuses_an_invalid_file(world, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"kind":"decision","number":1}\n', encoding="utf-8")
    result = run("decision", "restore", "--from", bad, "--apply")
    assert result["_exit"] == 2 and result["error"]["code"] == "EXPORT_INVALID"
    missing = run("decision", "restore", "--from", tmp_path / "nope.jsonl", "--apply")
    assert missing["_exit"] == 2
    assert not store_exists(PID)


def test_decision_export_without_a_store_says_so(world):
    result = run("decision", "export")
    assert result["_exit"] == 3 and result["error"]["code"] == "NOT_FOUND"


def test_search_finds_store_decisions_by_title_and_text(world):
    run(
        "decision", "new", "--title", "熵正则化关闭", "--layer", "研究",
        "--decided", "把熵项去掉。", "--reason", "它让评论家发散。",
        "--plans", "PLAN-900B",
        "--status", "confirmed", "--authorization", "用户原话：同意", "--apply",
    )  # fmt: skip
    new("别的")
    hits = data("search", "评论家")["hits"]
    assert [(h["type"], h["stem"], h["status"]) for h in hits] == [
        ("decision", "O2O-DEC-0009", "confirmed")
    ]
    assert hits[0]["snippet"].endswith(": 它让评论家发散。")
    assert hits[0]["snippet"].startswith("O2O-DEC-0009:")
    two_chars = data("search", "熵")["hits"]
    assert [h["stem"] for h in two_chars] == ["O2O-DEC-0009"]
    by_title = data("search", "熵正则化")
    assert by_title["matchMode"] == "fts"
    assert by_title["hits"][0]["stem"] == "O2O-DEC-0009"
    only = data("search", "--type", "decision", "--status", "proposed")["hits"]
    assert [h["stem"] for h in only] == ["O2O-DEC-0010"]
    assert data("search", "评论家", "--type", "plan")["hits"] == []
    in_plan = data("search", "评论家", "--plan", "PLAN-900B")["hits"]
    assert [h["stem"] for h in in_plan] == ["O2O-DEC-0009"]
    assert data("search", "评论家", "--plan", "PLAN-900A")["hits"] == []


def test_a_stored_decision_wikilink_is_not_an_unresolved_link(world):
    root, proj = world
    new()
    note = proj / "引用.md"
    note.write_text("见 [[O2O-DEC-0009]] 与 [[O2O-DEC-0099]]。\n", encoding="utf-8")
    result = invoke("check", "--code", "LINK.UNRESOLVED", "--json")
    findings = json.loads(result.stdout)["data"]["findings"]
    assert [f["message"] for f in findings] == ["link not found: [[O2O-DEC-0099]]"]
