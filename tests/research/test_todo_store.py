"""The manual TODO store: schema, guards, migration, export and restore."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from minivault import write
from writekit import audit_records, data, registered_vault, run

from expnote.research.errors import ResearchError
from expnote.research.todo_export import export_bytes, parse_export
from expnote.research.todo_store import (
    COLUMNS,
    STORE_SCHEMA,
    TodoStore,
    read_rows,
    store_exists,
    store_path,
)
from expnote.research.view import open_view

PID = "off2on"
OLD_FILE = (
    "---\ntype: todo-list\n---\n# 待办\n\n"
    "- [ ] 用户事项 [[别的笔记]] [id:: T-0007] [plan:: PLAN-900A] "
    "[due:: 2026-10-25] [by:: agent]\n"
    "  续行说明\n"
    "- [x] 已完成事项 [id:: T-0003] [done:: 2026-10-21] [prio:: high]\n"
    "- [ ] 没有编号的一行\n"
    "- [ ] 给代理的事项 [id:: T-0009] [owner:: agent] [by:: agent]\n"
    "- [ ] 重复编号 [id:: T-0009]\n"
    "- [ ] 坏所有者 [id:: T-0011] [owner:: robot]\n"
    "- [x] 又一个没编号\n"
)


@pytest.fixture
def world(tmp_path):
    return registered_vault(tmp_path)


@pytest.fixture
def store():
    with TodoStore.open("unit") as opened:
        yield opened


def record(number=1, **overrides):
    return {
        "number": number,
        "id": f"T-{number:04d}",
        "text": f"task {number}",
        "owner": "agent",
        "by": "agent",
        "source": "cli",
        **overrides,
    }


def rows():
    loaded = read_rows(PID)
    return loaded[0] if loaded else []


def row(ident):
    return next(r for r in rows() if r["id"] == ident)


# ---- the store itself --------------------------------------------------------------


def test_a_new_store_has_schema_meta_and_no_rows(store):
    assert store.meta_get("schema") == str(STORE_SCHEMA)
    assert store.meta_get("project") == "unit"
    assert store_exists("unit") and store.path == store_path("unit")
    assert store.count() == 0 and store.max_number() == 0
    tables = {
        r[0]
        for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"meta", "todos", "todo_events"} <= tables
    columns = [r[1] for r in store.conn.execute("PRAGMA table_info(todos)")]
    assert tuple(columns) == COLUMNS


def test_reading_never_creates_the_store():
    assert read_rows("never") is None and not store_exists("never")
    assert not store_path("never").exists()


def test_every_change_is_a_versioned_event(store):
    with store.transaction():
        store.insert(record(), actor="cli", at="2026-10-22T01:00:00Z")
    with store.transaction():
        store.edit(
            1, {"text": "reworded", "plan": "P-1"}, expected_version=1, actor="gui"
        )
    with store.transaction():
        store.set_done(
            1, True, expected_version=2, actor="cli", at="2026-10-23T01:00:00Z"
        )
    with store.transaction():
        store.set_done(1, False, expected_version=3, actor="cli")
    with store.transaction():
        done = store.drop(1, " not needed ", expected_version=4, actor="cli")
    assert (done["state"], done["version"], done["drop_reason"]) == (
        "dropped",
        5,
        "not needed",
    )
    assert done["dropped_on"] and done["created_at"] == "2026-10-22T01:00:00Z"
    events = store.events(1)
    assert [e["action"] for e in events] == ["add", "edit", "done", "reopen", "drop"]
    assert [e["actor"] for e in events] == ["cli", "gui", "cli", "cli", "cli"]
    assert events[1]["before"] == {"text": "task 1", "plan": None, "version": 1}
    assert events[1]["after"] == {"text": "reworded", "plan": "P-1", "version": 2}
    assert events[2]["after"]["done_on"] == "2026-10-23"
    assert events[3]["before"]["state"] == "done"


def test_conflicts_dropped_rows_and_bad_moves_are_refused(store):
    with store.transaction():
        store.insert(record(1), actor="t")
        store.insert(record(2), actor="t")
    with pytest.raises(ResearchError) as stale, store.transaction():
        store.edit(1, {"text": "x"}, expected_version=7, actor="t")
    assert stale.value.code == "CONFLICT" and stale.value.exit_code == 5
    with pytest.raises(ResearchError) as taken, store.transaction():
        store.insert(record(1), actor="t")
    assert taken.value.code == "CONFLICT"
    with pytest.raises(ResearchError) as gate, store.transaction():
        store.set_done(
            1, False, expected_version=1, actor="t"
        )  # open: nothing to reopen
    assert gate.value.code == "GATE_FAILED"
    with store.transaction():
        store.drop(2, "no", expected_version=1, actor="t")
    for attempt in (
        lambda: store.set_done(2, True, expected_version=2, actor="t"),
        lambda: store.edit(2, {"text": "y"}, expected_version=2, actor="t"),
        lambda: store.drop(2, "again", expected_version=2, actor="t"),
    ):
        with pytest.raises(ResearchError) as refused, store.transaction():
            attempt()
        assert refused.value.code == "GATE_FAILED"
    with pytest.raises(ResearchError) as empty, store.transaction():
        store.drop(1, "  ", expected_version=1, actor="t")
    assert empty.value.exit_code == 2
    with pytest.raises(ResearchError) as unknown, store.transaction():
        store.edit(9, {"text": "z"}, expected_version=1, actor="t")
    assert unknown.value.code == "ADDRESS_NOT_FOUND"
    assert store.get(2)["version"] == 2 and len(store.events(2)) == 2


def test_triggers_guard_identity_and_dropped_rows_against_stray_sql(store):
    with store.transaction():
        store.insert(record(1), actor="t")
        store.insert(record(2), actor="t")
        store.drop(2, "no", expected_version=1, actor="t")
    for sql in (
        "UPDATE todos SET id='T-9999' WHERE number=1",
        "UPDATE todos SET \"by\"='user' WHERE number=1",
        "UPDATE todos SET created_at='x' WHERE number=1",
        "UPDATE todos SET text='revived', state='open' WHERE number=2",
        "UPDATE todos SET owner='robot' WHERE number=1",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            store.conn.execute(sql)
    assert store.get(1)["id"] == "T-0001" and store.get(2)["state"] == "dropped"


def test_a_second_connection_waits_for_the_writer_then_sees_its_rows(store):
    other = TodoStore.open("unit")
    try:
        with store.transaction():
            store.insert(record(1), actor="t")
            # the writer holds the lock: a read-only opener still reads the old state
            assert read_rows("unit") == ([], [])
        assert len(read_rows("unit")[0]) == 1 and other.count() == 1
    finally:
        other.close()


# ---- before migration: reads from the file, writes refused ---------------------------


def test_before_migration_reads_come_from_the_file_and_writes_refuse(world):
    root, proj = world
    old = proj / "待办.md"
    write(old, OLD_FILE)
    listed = data("todo", "list", "--source", "manual")["todos"]
    assert {t["id"] for t in listed} >= {"T-0007", "T-0009", "T-0011"}
    assert all(t["version"] is None for t in listed)
    assert not store_exists(PID)
    before = old.read_bytes()
    commands = (
        ("add", "x", "--owner", "agent"),
        ("edit", "T-0007", "--owner", "agent"),
        ("done", "T-0007"),
        ("drop", "T-0007", "--reason", "x"),
    )
    for args in commands:
        for apply in ([], ["--apply"]):
            refused = run("todo", *args, *apply)
            assert refused["_exit"] == 2, args
            assert refused["error"]["code"] == "TODO_NOT_MIGRATED"
            assert refused["error"]["remediation"] == "expnote todo migrate --apply"
    assert old.read_bytes() == before and not store_exists(PID)
    check = {c["id"]: c for c in data("doctor")["checks"]}["todo-store"]
    assert check["status"] == "warn" and "TODO_NOT_MIGRATED" in check["message"]


def test_a_file_without_task_lines_does_not_block_the_first_write(world):
    root, proj = world
    write(proj / "待办.md", "---\ntype: todo-list\n---\n# 待办\n\nnothing yet\n")
    added = run("todo", "add", "first", "--owner", "agent", "--apply")
    assert added["_exit"] == 0 and added["data"]["id"] == "T-0001"
    assert store_exists(PID)


def test_once_the_store_exists_the_old_file_is_ignored(world):
    root, proj = world
    run("todo", "add", "in the store", "--owner", "agent", "--apply")
    write(proj / "待办.md", OLD_FILE)
    listed = data("todo", "list", "--source", "manual")["todos"]
    assert [t["id"] for t in listed] == ["T-0001"]
    assert run("todo", "add", "more", "--owner", "agent", "--apply")["_exit"] == 0


# ---- migration -----------------------------------------------------------------------


def journal_copies(ctx_records):
    return [r["payload"]["journal"] for r in ctx_records if r["payload"].get("journal")]


def test_migrate_dry_run_changes_nothing_and_shows_rows_and_the_deleted_file(world):
    root, proj = world
    old = proj / "待办.md"
    write(old, OLD_FILE)
    dry = run("todo", "migrate")
    assert dry["_exit"] == 4
    info = dry["data"]
    assert info["dryRun"] is True and info["applied"] is False
    assert info["confirmCommand"] == "expnote todo migrate --apply"
    store_change, export_change, delete_change = info["changes"]
    assert store_change["op"] == "todo-migrate"
    assert "T-0007" in store_change["unifiedDiff"]
    assert export_change["path"].endswith("ops/todos.jsonl")
    assert delete_change["op"] == "delete" and delete_change["path"].endswith("待办.md")
    assert {r["id"] for r in info["rows"]} >= {"T-0003", "T-0007", "T-0009"}
    assert old.read_text(encoding="utf-8") == OLD_FILE
    assert not store_exists(PID) and not (proj / "ops" / "todos.jsonl").exists()
    assert audit_records() == []


def test_migrate_keeps_ids_numbers_the_rest_and_warns(world):
    root, proj = world
    old = proj / "待办.md"
    write(old, OLD_FILE)
    done = run("todo", "migrate", "--apply")
    assert done["_exit"] == 0 and done["data"]["applied"] is True
    by_id = {r["id"]: r for r in rows()}
    # kept: T-0003, T-0007, T-0009, T-0011; the rest in file order after the highest
    assert sorted(by_id) == [
        "T-0003", "T-0007", "T-0009", "T-0011", "T-0012", "T-0013", "T-0014",
    ]  # fmt: skip
    seven = by_id["T-0007"]
    assert seven["text"] == "用户事项 [[别的笔记]] 续行说明"
    assert (seven["plan"], seven["due"], seven["by"], seven["owner"]) == (
        "PLAN-900A", "2026-10-25", "agent", "user",
    )  # fmt: skip
    assert (seven["state"], seven["source"], seven["version"]) == (
        "open",
        "migrated",
        1,
    )
    three = by_id["T-0003"]
    assert (three["state"], three["done_on"], three["by"]) == (
        "done",
        "2026-10-21",
        "user",
    )
    assert by_id["T-0009"]["owner"] == "agent"
    assert by_id["T-0011"]["owner"] == "user"  # robot is not an owner
    assert (
        by_id["T-0012"]["text"] == "没有编号的一行"
        and by_id["T-0012"]["state"] == "open"
    )
    assert by_id["T-0013"]["text"] == "重复编号" and by_id["T-0014"]["state"] == "done"
    assert by_id["T-0014"]["done_on"] is None
    codes = sorted(w["code"] for w in done["warnings"])
    assert codes == ["TODO_BAD_OWNER", "TODO_ID_REASSIGNED", "TODO_UNKNOWN_FIELDS"]
    unknown = next(w for w in done["warnings"] if w["code"] == "TODO_UNKNOWN_FIELDS")
    assert "T-0003" in unknown["message"] and "prio" in unknown["message"]
    events = read_rows(PID)[1]
    assert {e["action"] for e in events} == {"migrate"} and len(events) == 7
    assert (proj / "ops" / "todos.jsonl").read_bytes().count(b'"kind": "todo"') == 7
    # the next add continues the numbering
    added = run("todo", "add", "after", "--owner", "agent", "--apply")
    assert added["data"]["id"] == "T-0015"


def test_migrate_backs_the_file_up_through_the_journal_then_deletes_it(world):
    root, proj = world
    old = proj / "待办.md"
    write(old, OLD_FILE)
    done = run("todo", "migrate", "--apply")
    assert not old.exists()
    deleted = [r for r in audit_records() if r["payload"]["op"] == "delete"]
    assert len(deleted) == 1
    payload = deleted[0]["payload"]
    assert payload["command"] == "todo migrate" and payload["path"].endswith("待办.md")
    with open(payload["journal"], encoding="utf-8") as backup:
        assert backup.read() == OLD_FILE
    assert any(c.get("journal") == payload["journal"] for c in done["data"]["changes"])
    ops = [r["payload"]["op"] for r in audit_records()]
    assert ops == ["export", "delete"]  # the export lands before the file goes
    listed = data("todo", "list", "--source", "manual", "--include-done")["todos"]
    assert len(listed) == 7 and all(t["version"] == 1 for t in listed)


def test_migrate_refuses_when_the_store_exists_or_there_is_no_file(world):
    root, proj = world
    nothing = run("todo", "migrate", "--apply")
    assert nothing["_exit"] == 3 and nothing["error"]["code"] == "NOT_FOUND"
    assert not store_exists(PID)
    write(proj / "待办.md", OLD_FILE)
    assert run("todo", "migrate", "--apply")["_exit"] == 0
    write(proj / "待办.md", "- [ ] back again [id:: T-0100]\n")
    for apply in ([], ["--apply"]):
        again = run("todo", "migrate", *apply)
        assert again["_exit"] == 5 and again["error"]["code"] == "TODO_STORE_EXISTS"
    assert (proj / "待办.md").exists() and len(rows()) == 7


def test_migrate_stops_before_touching_anything_when_the_export_is_foreign(world):
    root, proj = world
    write(proj / "待办.md", OLD_FILE)
    write(proj / "ops" / "todos.jsonl", "someone else's file\n")
    refused = run("todo", "migrate", "--apply")
    assert refused["_exit"] == 5 and refused["error"]["code"] == "EXPORT_DIVERGED"
    assert (proj / "待办.md").exists() and not store_exists(PID)
    assert (proj / "ops" / "todos.jsonl").read_text(encoding="utf-8") == (
        "someone else's file\n"
    )


# ---- export and restore --------------------------------------------------------------


def add(text="one"):
    return run("todo", "add", text, "--owner", "agent", "--apply")


def test_the_export_follows_each_change_and_is_skipped_when_identical(world):
    root, proj = world
    export = proj / "ops" / "todos.jsonl"
    assert run("todo", "export")["_exit"] == 3  # no store yet
    add("one")
    first = export.read_bytes()
    assert first.count(b'"kind": "todo"') == 1 and b'"kind": "event"' in first
    stamp = export.stat().st_mtime_ns
    events = len(audit_records())
    again = run("todo", "export", "--apply")
    assert again["_exit"] == 0 and again["data"]["changes"] == []
    assert export.stat().st_mtime_ns == stamp and len(audit_records()) == events
    run("todo", "done", "T-0001", "--apply")
    assert export.read_bytes() != first
    assert audit_records()[-1]["payload"]["op"] == "export"
    parsed = parse_export(export.read_bytes())
    assert export_bytes(*parsed) == export.read_bytes()


def test_a_changed_export_is_diverged_until_forced(world):
    root, proj = world
    add("one")
    export = proj / "ops" / "todos.jsonl"
    export.write_text("# edited\n", encoding="utf-8")
    second = add("two")
    assert second["_exit"] == 0  # the TODO is recorded regardless
    assert [w["code"] for w in second["warnings"]] == ["EXPORT_DIVERGED"]
    assert "todo export" in second["warnings"][0]["message"]
    assert export.read_text(encoding="utf-8") == "# edited\n"
    refused = run("todo", "export", "--apply")
    assert refused["_exit"] == 5 and refused["error"]["code"] == "EXPORT_DIVERGED"
    check = {c["id"]: c for c in data("doctor")["checks"]}["todo-store"]
    assert check["status"] == "warn" and "EXPORT_DIVERGED" in check["message"]
    preview = run("todo", "export", "--force")
    assert preview["_exit"] == 4 and preview["warnings"] == []
    forced = run("todo", "export", "--apply", "--force")
    assert forced["_exit"] == 0 and forced["data"]["applied"] is True
    assert export.read_bytes().count(b'"kind": "todo"') == 2
    check = {c["id"]: c for c in data("doctor")["checks"]}["todo-store"]
    assert check["status"] == "ok" and "2 TODO(s)" in check["message"]
    export.unlink()
    check = {c["id"]: c for c in data("doctor")["checks"]}["todo-store"]
    assert "EXPORT_MISSING" in check["message"]
    assert run("todo", "export", "--apply")["_exit"] == 0 and export.exists()


def test_doctor_says_so_when_there_is_no_store_yet(world):
    check = {c["id"]: c for c in data("doctor")["checks"]}["todo-store"]
    assert check["status"] == "info" and "no TODO store yet" in check["message"]


def test_restore_rebuilds_the_store_from_the_export(world):
    root, proj = world
    add("one")
    add("two")
    run("todo", "done", "T-0001", "--apply")
    run("todo", "drop", "T-0002", "--reason", "gone", "--apply")
    export = proj / "ops" / "todos.jsonl"
    saved = export.read_bytes()
    before = read_rows(PID)
    store_path(PID).unlink()
    export.unlink()
    dry = run("todo", "restore", "--from", str(proj.parent / "nope.jsonl"))
    assert dry["_exit"] == 2 and dry["error"]["code"] == "USAGE_PATH_NOT_FOUND"
    backup = proj.parent / "todos.jsonl"
    backup.write_bytes(saved)
    preview = run("todo", "restore", "--from", str(backup))
    assert preview["_exit"] == 4 and preview["data"]["rows"] == 2
    assert not store_exists(PID)
    done = run("todo", "restore", "--from", str(backup), "--apply")
    assert done["_exit"] == 0 and done["data"]["applied"] is True
    assert read_rows(PID) == before
    assert export.read_bytes() == saved
    # the restored store keeps working, and dropped rows stay terminal
    assert add("three")["data"]["id"] == "T-0003"
    refused = run("todo", "done", "T-0002", "--apply")
    assert refused["_exit"] == 1


def test_restore_refuses_a_store_with_rows_unless_replaced(world):
    root, proj = world
    add("one")
    backup = proj.parent / "todos.jsonl"
    backup.write_bytes((proj / "ops" / "todos.jsonl").read_bytes())
    add("two")
    refused = run("todo", "restore", "--from", str(backup), "--apply")
    assert refused["_exit"] == 5 and refused["error"]["code"] == "STORE_NOT_EMPTY"
    assert len(rows()) == 2
    replaced = run("todo", "restore", "--from", str(backup), "--replace", "--apply")
    assert replaced["_exit"] == 0 and len(rows()) == 1


@pytest.mark.parametrize(
    "text",
    ["not json\n", '{"kind": "mystery"}\n', '{"kind": "todo", "id": "T-1"}\n', "[1]\n"],
)
def test_restore_rejects_a_malformed_export(world, text):
    root, proj = world
    broken = proj.parent / "broken.jsonl"
    broken.write_text(text, encoding="utf-8")
    result = run("todo", "restore", "--from", str(broken), "--apply")
    assert result["_exit"] == 2 and result["error"]["code"] == "EXPORT_INVALID"
    assert not store_exists(PID)


# ---- consumers read the store --------------------------------------------------------


def test_next_home_and_bundle_see_store_rows(world):
    from expnote.research.bundle import build_bundle
    from expnote.research.home import build_home

    root, proj = world
    run(
        "todo", "add", "user thing", "--owner", "user", "--plan", "PLAN-900A", "--apply"
    )
    run("todo", "add", "agent thing", "--owner", "agent", "--apply")
    run("todo", "add", "finished", "--owner", "agent", "--apply")
    run("todo", "done", "T-0003", "--apply")
    steps = data("next", "--owner", "agent")
    assert [s["message"] for s in steps["steps"] if s["kind"] == "manual"] == [
        "agent thing"
    ]
    assert any(i["id"] == "T-0001" for i in steps["waiting"]["items"])
    with open_view(PID) as view:
        home, _ = build_home(view)
        assert home["todos"]["manual"] == 2  # the ticked one is not open
        items, _ = build_bundle(view, "PLAN-900A", sections=["openItems"])
    manual = [i for i in items["openItems"] if i["kind"] == "manual"]
    assert [(i["id"], i["state"], i["version"]) for i in manual] == [
        ("T-0001", "open", 1)
    ]
    plans = {p["id"]: p for p in data("plan", "list")["plans"]}
    assert plans["PLAN-900A"]["openItems"] >= 1


def test_the_json_shape_of_a_manual_item_is_stable(world):
    run("todo", "add", "shape", "--owner", "agent", "--plan", "PLAN-900A", "--apply")
    (item,) = data("todo", "list", "--source", "manual")["todos"]
    assert item == {
        "id": "T-0001",
        "source": "manual",
        "kind": "manual",
        "owner": "agent",
        "severity": "info",
        "ref": "T-0001",
        "message": "shape",
        "action": None,
        "plan": "PLAN-900A",
        "command": None,
        "state": "open",
        "due": None,
        "by": "agent",
        "version": 1,
        "doneOn": None,
        "droppedOn": None,
        "dropReason": None,
    }


def test_the_tests_ran_against_this_worktree():
    import expnote

    assert Path(expnote.__file__).resolve().is_relative_to(Path(__file__).parents[2])
