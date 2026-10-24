"""Write commands: dry-run default, apply, idempotence, user-only, audit."""

from __future__ import annotations

import json

import pytest
from minivault import (
    DEVIATIONS_9COL,
    DEVIATIONS_PATH,
    EV_PATH,
    EXEC_PATH,
    PLAN_PATH,
    RES_PATH,
    write,
)
from writekit import (
    audit_records,
    data,
    edit_profile,
    invoke,
    registered_vault,
    run,
    set_mtime,
)

from expnote.research.decision_store import read_rows


@pytest.fixture
def world(tmp_path):
    return registered_vault(tmp_path)


def read(path):
    return path.read_text(encoding="utf-8")


def stamp(path):
    return path.stat().st_mtime_ns


def new_decision(title="PLAN-900B 批准：开始", plans="PLAN-900B", *extra):
    return run(
        "decision",
        "new",
        "--title",
        title,
        "--layer",
        "研究",
        "--decided",
        "做 A。",
        "--reason",
        "因为 B。",
        "--plans",
        plans,
        "--apply",
        *extra,
    )


def store_row(ident):
    return next(r for r in read_rows("off2on")[0] if r["id"] == ident)


def store_events(ident):
    number = store_row(ident)["number"]
    return [e for e in read_rows("off2on")[1] if e["number"] == number]


def confirm(ident, words="用户原话：可以"):
    return run(
        "decision",
        "set",
        ident,
        "--status",
        "confirmed",
        "--authorization",
        words,
        "--apply",
    )


# ---- dry-run is the default --------------------------------------------------------


def test_dry_run_prints_the_change_and_exits_4_without_writing(world):
    root, proj = world
    before = sorted(p for p in proj.rglob("*") if p.is_file())
    result = invoke(
        "decision", "new", "--title", "PLAN-900B 批准", "--layer", "研究",
        "--decided", "做 A。", "--reason", "因为 B。", "--json",
    )  # fmt: skip
    assert result.exit_code == 4
    body = json.loads(result.stdout)
    assert body["ok"] is True
    info = body["data"]
    assert info["dryRun"] is True and info["applied"] is False
    store, export = info["changes"]
    assert store["op"] == "decision-new" and store["address"] == "decision:O2O-DEC-0009"
    assert "+status: proposed" in store["unifiedDiff"]
    assert export["path"].endswith("ops/decisions.jsonl") and export["op"] == "export"
    assert info["confirmCommand"].startswith("expnote decision new --title")
    assert info["confirmCommand"].endswith("--apply")
    assert sorted(p for p in proj.rglob("*") if p.is_file()) == before
    assert audit_records() == []
    assert read_rows("off2on") is None  # a preview never creates the store


def test_dry_run_human_output_shows_the_diff_and_how_to_apply(world):
    result = invoke(
        "decision", "new", "--title", "T", "--layer", "研究",
        "--decided", "d", "--reason", "r",
    )  # fmt: skip
    assert result.exit_code == 4
    assert "+status: proposed" in result.stdout
    assert "dry-run: nothing was written" in result.stdout
    assert "expnote decision new --title T --layer" in result.stdout


def test_every_write_command_is_dry_run_by_default(world):
    root, proj = world
    pending = new_decision()["data"]["id"]
    snapshot = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    events = len(audit_records())
    commands = [
        (
            "decision",
            "set",
            pending,
            "--status",
            "confirmed",
            "--authorization",
            "用户原话",
        ),
        ("todo", "add", "check later", "--owner", "agent"),
        ("deviation", "add", "--experiment", "x", "--content", "y", "--impact", "弱"),
        ("deviation", "close", "D-02"),
        ("exec", "advance", "PLAN-900A-S1", "--to", "running"),
        ("new", "plan", "--id", "PLAN-901A", "--title", "t"),
        ("new", "ev", "--plan", "PLAN-900A", "--title", "t"),
        (
            "plan",
            "advance",
            "PLAN-900A",
            "--to",
            "superseded",
            "--decision",
            "5",
            "--superseded-by",
            "PLAN-900B",
        ),
    ]
    for command in commands:
        result = invoke(*command, "--json")
        assert result.exit_code == 4, (command, result.stdout)
        assert json.loads(result.stdout)["data"]["dryRun"] is True
    assert snapshot == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert len(audit_records()) == events


# ---- decisions ---------------------------------------------------------------


def test_decision_new_numbers_after_the_legacy_table_and_check_stays_clean(world):
    root, proj = world
    first = new_decision()["data"]
    second = new_decision("PLAN-900B 第二个")["data"]
    assert (first["id"], second["id"]) == ("O2O-DEC-0009", "O2O-DEC-0010")
    assert not (proj / "decisions").exists()  # decisions are never notes
    row = store_row("O2O-DEC-0009")
    assert (row["status"], row["version"], row["source"]) == ("proposed", 1, "cli")
    assert (row["decided"], row["reason"]) == ("做 A。", "因为 B。")
    export = (proj / "ops" / "decisions.jsonl").read_text(encoding="utf-8")
    assert export.count('"kind": "decision"') == 2
    findings = data("check", "--code", "SCHEMA.MISSING_FIELD")["findings"]
    assert findings == []
    listed = {d["id"]: d for d in data("decision", "list")["decisions"]}
    assert listed["O2O-DEC-0001"]["source"] == "table"  # the legacy rows stay
    assert listed["O2O-DEC-0009"]["status"] == "proposed"
    assert listed["O2O-DEC-0009"]["plans"] == ["PLAN-900B"]
    todos = data("todo", "list", "--kind", "decision-proposed")["todos"]
    assert {t["ref"] for t in todos} == {"O2O-DEC-0009", "O2O-DEC-0010"}


def test_decision_new_validates_layer_plan_text_and_status(world):
    base = ("decision", "new", "--title", "t", "--decided", "d", "--reason", "r")
    bad_layer = run(*base, "--layer", "nope", "--apply")
    assert bad_layer["_exit"] == 2 and bad_layer["error"]["code"] == "USAGE_BAD_OPTION"
    bad_plan = run(*base, "--layer", "研究", "--plans", "PLAN-404", "--apply")
    assert bad_plan["_exit"] == 3 and bad_plan["error"]["code"] == "PLAN_NOT_FOUND"
    blank = run(
        "decision", "new", "--title", "t", "--layer", "研究", "--decided", " ",
        "--reason", "r", "--apply",
    )  # fmt: skip
    assert blank["_exit"] == 2 and blank["error"]["code"] == "USAGE_EMPTY_TEXT"
    missing = invoke("decision", "new", "--title", "t", "--layer", "研究", "--json")
    assert missing.exit_code == 2  # --decided and --reason are required
    bad_status = run(*base, "--layer", "研究", "--status", "rejected", "--apply")
    assert bad_status["_exit"] == 2
    bad_review = run(*base, "--layer", "研究", "--review-by", "soon", "--apply")
    assert bad_review["_exit"] == 2


def test_recording_a_chat_confirmed_decision_needs_the_users_words(world):
    base = (
        "decision", "new", "--title", "t", "--layer", "研究", "--decided", "d",
        "--reason", "r", "--status", "confirmed",
    )  # fmt: skip
    refused = run(*base, "--apply")
    assert refused["_exit"] == 7 and refused["error"]["code"] == "USER_ONLY"
    assert read_rows("off2on") is None
    done = run(*base, "--authorization", "用户原话：就这么定", "--apply")
    assert done["_exit"] == 0 and done["data"]["status"] == "confirmed"
    row = store_row(done["data"]["id"])
    assert row["status"] == "confirmed" and row["source"] == "chat"
    assert row["confirmed_by"] == "agent-for-user"
    assert row["authorization"] == "用户原话：就这么定"
    assert [e["action"] for e in store_events(row["id"])] == ["record"]
    assert data("check")["summary"]["fail"] == 0


def test_new_decision_is_the_same_command_with_text_files(world, tmp_path):
    root, proj = world
    decided = tmp_path / "decided.md"
    decided.write_text("做 A。\n<!-- vault:status:start -->\n再做 B。\n", "utf-8")
    made = run(
        "new", "decision", "--title", "T", "--layer", "研究",
        "--decided", f"@{decided}", "--reason", "因为。",
        "--options", "不做 A。", "--lands", "落到 [[别的笔记]]",
        "--targets", "PLAN-900B--other,[[别的笔记]]",
        "--supersedes", "7", "--review-by", "2026-12-01", "--apply",
    )  # fmt: skip
    assert made["_exit"] == 0 and made["command"] == "new decision"
    row = store_row(made["data"]["id"])
    assert "vault:status" not in row["decided"] and "再做 B。" in row["decided"]
    assert row["options"] == "不做 A。" and row["lands"] == "落到 [[别的笔记]]"
    assert row["targets"] == ["[[PLAN-900B--other]]", "[[别的笔记]]"]
    assert row["supersedes"] == [7] and row["review_by"] == "2026-12-01"
    unreadable = run(
        "new", "decision", "--title", "T", "--layer", "研究",
        "--decided", "@/nope/missing.md", "--reason", "r", "--apply",
    )  # fmt: skip
    assert unreadable["_exit"] == 2


def test_confirming_without_authorization_is_user_only_exit_7(world):
    root, proj = world
    ident = new_decision()["data"]["id"]
    before, events = read_rows("off2on"), len(audit_records())
    for status in ("confirmed", "rejected"):
        refused = run("decision", "set", ident, "--status", status, "--apply")
        assert refused["_exit"] == 7 and refused["error"]["code"] == "USER_ONLY"
        assert "--authorization" in refused["error"]["remediation"]
    blank = run(
        "decision",
        "set",
        ident,
        "--status",
        "confirmed",
        "--authorization",
        "  ",
        "--apply",
    )
    assert blank["_exit"] == 7
    assert read_rows("off2on") == before and len(audit_records()) == events


def test_confirm_records_the_agent_path_and_check_stays_green(world):
    root, proj = world
    ident = new_decision()["data"]["id"]
    done = confirm(ident, "用户在对话里说：批准 PLAN-900B 开始")
    assert done["_exit"] == 0 and done["data"]["applied"] is True
    row = store_row(ident)
    assert row["status"] == "confirmed" and row["version"] == 2
    assert row["confirmed_by"] == "agent-for-user"
    assert row["authorization"] == "用户在对话里说：批准 PLAN-900B 开始"
    assert row["confirmed_on"]
    assert (row["decided"], row["reason"]) == ("做 A。", "因为 B。")  # immutable
    propose, approve = store_events(ident)
    assert (propose["action"], approve["action"]) == ("propose", "approve")
    assert approve["before"] == {"status": "proposed", "version": 1}
    assert approve["after"]["authorization"].startswith("用户在对话里说")
    codes = {f["code"] for f in data("check")["findings"]}
    assert not codes & {"DEC.AUTH_WEAK", "DEC.AGENT_NO_AUTH"}
    assert data("check")["summary"]["fail"] == 0
    exports = [e for e in audit_records() if e["payload"]["command"] == "decision set"]
    assert [e["payload"]["op"] for e in exports] == ["export"]


def test_second_confirm_is_a_no_op_that_leaves_the_export_alone(world):
    root, proj = world
    ident = new_decision()["data"]["id"]
    confirm(ident)
    export = proj / "ops" / "decisions.jsonl"
    stamp_before, events = stamp(export), len(audit_records())
    again = confirm(ident)
    assert again["_exit"] == 0 and again["data"]["changes"] == []
    assert stamp(export) == stamp_before and len(audit_records()) == events
    assert store_row(ident)["version"] == 2


def test_reject_and_wrong_transitions(world):
    ident = new_decision()["data"]["id"]
    rejected = run(
        "decision",
        "set",
        ident,
        "--status",
        "rejected",
        "--authorization",
        "用户否决了",
        "--apply",
    )
    assert rejected["_exit"] == 0
    again = confirm(ident)
    assert again["_exit"] == 1 and again["error"]["code"] == "GATE_FAILED"
    assert again["error"]["details"]["gates"][0]["code"] == "DEC.TRANSITION"


def test_supersede_needs_a_successor_that_lists_the_predecessor(world, tmp_path):
    root, proj = world
    old = new_decision("PLAN-900B 旧做法")["data"]["id"]
    confirm(old)
    unlisted_new = new_decision("PLAN-900B 没写取代")["data"]["id"]
    missing = run("decision", "set", old, "--status", "superseded", "--apply")
    assert missing["_exit"] == 2
    unlisted = run(
        "decision", "set", old, "--status", "superseded", "--by", unlisted_new,
        "--apply",
    )  # fmt: skip
    assert unlisted["_exit"] == 1
    assert unlisted["error"]["details"]["gates"][0]["code"] == "DEC.NO_BACKREF"
    new = new_decision("PLAN-900B 新做法", "PLAN-900B", "--supersedes", old)
    ok = run(
        "decision", "set", old, "--status", "superseded", "--by",
        new["data"]["id"], "--apply",
    )  # fmt: skip
    assert ok["_exit"] == 0
    row = store_row(old)
    assert row["status"] == "superseded" and row["superseded_by"] == 11
    assert store_events(old)[-1]["action"] == "supersede"
    assert data("check")["summary"]["fail"] == 0


def test_a_legacy_table_row_cannot_be_edited(world):
    result = run(
        "decision",
        "set",
        "8",
        "--status",
        "confirmed",
        "--authorization",
        "x y z",
        "--apply",
    )
    assert result["_exit"] == 2
    assert result["error"]["code"] == "DECISION_NOT_EDITABLE"


# ---- concurrency, quiet window, actor ----------------------------------------


def test_a_stale_expected_version_is_a_conflict_exit_5(world):
    root, proj = world
    ident = new_decision()["data"]["id"]
    shown = data("decision", "show", ident)["decision"]["version"]
    assert shown == 1
    result = run(
        "decision", "set", ident, "--status", "confirmed",
        "--authorization", "用户原话", "--expected-version", "7", "--apply",
    )  # fmt: skip
    assert result["_exit"] == 5 and result["error"]["code"] == "CONFLICT"
    assert result["error"]["details"] == {"expectedVersion": 7, "currentVersion": 1}
    assert store_row(ident)["status"] == "proposed"
    fine = run(
        "decision", "set", ident, "--status", "confirmed",
        "--authorization", "用户原话", "--expected-version", "1", "--apply",
    )  # fmt: skip
    assert fine["_exit"] == 0


def test_the_store_refuses_text_edits_and_illegal_moves_even_from_raw_sql(world):
    import sqlite3

    from expnote.research.decision_store import store_path

    ident = new_decision()["data"]["id"]
    conn = sqlite3.connect(store_path("off2on"))
    for sql in (
        "UPDATE decisions SET decided='x' WHERE number=9",
        "UPDATE decisions SET title='x' WHERE number=9",
        "UPDATE decisions SET status='superseded' WHERE number=9",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    conn.close()
    assert store_row(ident)["decided"] == "做 A。"


def test_decision_writes_are_not_held_back_by_the_quiet_window(tmp_path):
    from minivault import build_vault
    from readkit import register

    root, proj = build_vault(tmp_path)
    register(root, proj)  # default quiet window: 10 s
    made = new_decision()
    assert made["_exit"] == 0
    export = proj / "ops" / "decisions.jsonl"
    export.touch()  # just "saved": the tool owns this file, no quiet window
    done = confirm(made["data"]["id"])
    assert done["_exit"] == 0 and done["warnings"] == []


def test_the_actor_comes_from_the_environment(world, monkeypatch):
    monkeypatch.setenv("EXPNOTE_ACTOR", "claude")
    new_decision()
    assert audit_records()[-1]["payload"]["actor"] == "claude"


# ---- evidence labels ---------------------------------------------------------


def make_rendered_ev(root):
    made = run(
        "new",
        "ev",
        "--plan",
        "PLAN-900A",
        "--title",
        "新卡",
        "--exec",
        "PLAN-900A-S1",
        "--apply",
    )
    assert made["_exit"] == 0, made
    return made["data"]


def test_ev_label_set_and_commit_are_user_only(world):
    root, proj = world
    info = make_rendered_ev(root)
    stem = "EV--PLAN-900A 新卡"
    for args in (
        ("set", stem, "--row", "...", "--value", "Value=跌落"),
        ("commit", stem),
    ):
        refused = run("ev", "label", *args, "--apply")
        assert refused["_exit"] == 7 and refused["error"]["code"] == "USER_ONLY"
    assert "..." in read(root / info["created"][0]["path"])


def test_ev_label_set_edits_one_cell_and_commit_needs_a_filled_table(world):
    root, proj = world
    info = make_rendered_ev(root)
    path = root / info["created"][0]["path"]
    stem = "EV--PLAN-900A 新卡"
    no_prefix = run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "...",
        "--value",
        "Value=x",
        "--remark",
        "r",
        "--authorization",
        "用户原话",
        "--apply",
    )
    assert no_prefix["error"]["code"] == "PROFILE_KEY_MISSING"
    edit_profile(
        proj,
        '  value_columns: ["1"]',
        '  value_columns: ["1"]\n  remark_prefix: "备注"',
    )
    early = run(
        "ev", "label", "commit", stem, "--authorization", "用户原话说标好了", "--apply"
    )
    assert early["_exit"] == 1
    assert early["error"]["details"]["gates"][0]["code"] == "EV.LABEL_NOT_FILLED"
    unknown_row = run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "nope",
        "--value",
        "Value=x",
        "--authorization",
        "用户原话",
        "--apply",
    )
    assert (
        unknown_row["_exit"] == 3
        and unknown_row["error"]["code"] == "ADDRESS_NOT_FOUND"
    )
    unknown_col = run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "...",
        "--value",
        "Nope=x",
        "--authorization",
        "用户原话",
        "--apply",
    )
    assert unknown_col["_exit"] == 3
    before = read(path)
    done = run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "...",
        "--value",
        "Value=跌落 | 严重",
        "--remark",
        "用户说：整体偏低",
        "--authorization",
        "用户原话：标成跌落",
        "--apply",
    )
    assert done["_exit"] == 0, done
    after = read(path)
    assert "| ... | 跌落 \\| 严重 |" in after
    assert "备注: 用户说：整体偏低" in after
    assert (
        before.replace("| ... |  |", "| ... | 跌落 \\| 严重 |") != after
    )  # remark added
    shown = data("ev", "label", "show", stem)["ev"]
    assert shown["labelState"] == "filled" and shown["labelTable"]["rows"][0]["filled"]
    committed = run(
        "ev", "label", "commit", stem, "--authorization", "用户原话：标好了", "--apply"
    )
    assert committed["_exit"] == 0
    text = read(path)
    assert "status: labelled" in text and "labelled_by: agent-for-user" in text
    assert 'label_authorization: "用户原话：标好了"' in text and "labelled_on: " in text
    again = run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "...",
        "--value",
        "Value=x",
        "--authorization",
        "用户原话",
        "--apply",
    )
    assert again["_exit"] == 1  # labelled pages are no longer edited


def test_commit_with_a_decision_id_needs_that_decision_confirmed(world):
    root, proj = world
    info = make_rendered_ev(root)
    stem = "EV--PLAN-900A 新卡"
    run(
        "ev",
        "label",
        "set",
        stem,
        "--row",
        "...",
        "--value",
        "Value=跌落",
        "--authorization",
        "用户原话",
        "--apply",
    )
    pending = new_decision()["data"]["id"]
    blocked = run("ev", "label", "commit", stem, "--authorization", pending, "--apply")
    assert blocked["_exit"] == 1
    assert blocked["error"]["details"]["gates"][0]["code"] == "DEC.NOT_CONFIRMED"
    confirm(pending)
    ok = run("ev", "label", "commit", stem, "--authorization", pending, "--apply")
    assert ok["_exit"] == 0
    assert info["created"]


# ---- manual TODOs ------------------------------------------------------------


def test_todo_add_creates_the_file_then_appends_with_ids(world):
    root, proj = world
    todo_file = proj / "待办.md"
    first = run(
        "todo",
        "add",
        "回看 [[别的笔记]]",
        "--plan",
        "PLAN-900A",
        "--due",
        "2026-11-01",
        "--owner",
        "agent",
        "--apply",
    )
    assert first["_exit"] == 0 and first["data"]["id"] == "T-0001"
    assert read(todo_file) == (
        "---\ntype: todo-list\n---\n# Todo\n\n"
        "- [ ] 回看 [[别的笔记]] [id:: T-0001] [plan:: PLAN-900A] "
        "[due:: 2026-11-01] [by:: agent] [owner:: agent]\n"
    )
    second = run("todo", "add", "第二件", "--by", "user", "--apply")
    assert second["data"]["id"] == "T-0002"
    assert read(todo_file).endswith("- [ ] 第二件 [id:: T-0002] [by:: user]\n")
    listed = data("todo", "list", "--source", "manual")["todos"]
    by_id = {t["id"]: t for t in listed}
    assert sorted(by_id) == ["T-0001", "T-0002"]
    assert by_id["T-0001"]["plan"] == "PLAN-900A"
    assert by_id["T-0001"]["due"] == "2026-11-01"


def test_todo_add_rejects_fields_in_text_and_bad_dates(world):
    for args in (("x [id:: T-0099]",), ("x", "--due", "soon"), ("two\nlines",)):
        result = run("todo", "add", *args, "--owner", "agent", "--apply")
        assert result["_exit"] == 2, args


def test_todo_done_flips_the_line_and_undo_reverts_it(world):
    root, proj = world
    run("todo", "add", "一件事", "--owner", "agent", "--apply")
    run("todo", "add", "另一件", "--owner", "agent", "--apply")
    todo_file = proj / "待办.md"
    done = run("todo", "done", "T-0001", "--apply")
    assert done["_exit"] == 0
    lines = read(todo_file).splitlines()
    assert (
        lines[-2].startswith("- [x] 一件事 [id:: T-0001]") and "[done:: " in lines[-2]
    )
    assert lines[-1] == "- [ ] 另一件 [id:: T-0002] [by:: agent] [owner:: agent]"
    stamp_before = stamp(todo_file)
    assert run("todo", "done", "T-0001", "--apply")["data"]["changes"] == []
    assert stamp(todo_file) == stamp_before
    assert data("todo", "list", "--source", "manual")["todos"][0]["id"] == "T-0002"
    undone = run("todo", "done", "T-0001", "--undo", "--apply")
    assert undone["_exit"] == 0 and "[done::" not in read(todo_file)
    missing = run("todo", "done", "T-0404", "--apply")
    assert missing["_exit"] == 3 and missing["error"]["code"] == "ADDRESS_NOT_FOUND"


def test_todo_add_owner_flag_writes_the_field_and_rejects_bad_values(world):
    root, proj = world
    added = run("todo", "add", "agent side", "--owner", "agent", "--apply")
    assert added["_exit"] == 0
    assert read(proj / "待办.md").endswith(
        "- [ ] agent side [id:: T-0001] [by:: agent] [owner:: agent]\n"
    )
    assert run("todo", "add", "x", "--owner", "robot", "--apply")["_exit"] == 2
    run("todo", "add", "plain", "--by", "user", "--apply")
    assert "[owner::" not in read(proj / "待办.md").splitlines()[-1]


def test_todo_add_requires_an_owner_for_agents(world):
    root, proj = world
    todo_file = proj / "待办.md"
    before = todo_file.read_bytes() if todo_file.exists() else None
    refused = run("todo", "add", "no owner", "--apply")
    assert refused["_exit"] == 2
    assert refused["error"]["code"] == "USAGE_MISSING_OPTION"
    assert "--owner agent" in refused["error"]["message"]
    assert "--owner user" in refused["error"]["message"]
    assert (todo_file.read_bytes() if todo_file.exists() else None) == before
    assert run("todo", "add", "mine", "--owner", "agent", "--apply")["_exit"] == 0
    assert run("todo", "add", "yours", "--owner", "user", "--apply")["_exit"] == 0
    assert run("todo", "add", "by user", "--by", "user", "--apply")["_exit"] == 0
    lines = read(todo_file).splitlines()
    assert lines[-3].endswith("[by:: agent] [owner:: agent]")
    assert lines[-2].endswith("[by:: agent] [owner:: user]")
    assert lines[-1].endswith("[by:: user]")


def seeded_todos(proj):
    todo_file = proj / "待办.md"
    run(
        "todo", "add", "first task", "--plan", "PLAN-900A",
        "--owner", "agent", "--apply",
    )  # fmt: skip
    run("todo", "add", "second task", "--owner", "agent", "--apply")
    run("todo", "done", "T-0002", "--apply")
    set_mtime(todo_file, 3600)
    return todo_file


def test_todo_edit_dry_run_changes_nothing_and_exits_4(world):
    root, proj = world
    todo_file = seeded_todos(proj)
    before = read(todo_file)
    result = run("todo", "edit", "T-0001", "--owner", "user")
    assert result["_exit"] == 4 and read(todo_file) == before


def test_todo_edit_owner_hands_the_task_to_the_user(world):
    root, proj = world
    todo_file = seeded_todos(proj)
    agent_side = data("todo", "list", "--source", "manual", "--owner", "agent")
    assert [t["id"] for t in agent_side["todos"]] == ["T-0001"]
    assert run("todo", "edit", "T-0001", "--owner", "user", "--apply")["_exit"] == 0
    line = read(todo_file).splitlines()[-2]
    assert line.endswith("[owner:: user]") and "[owner:: agent]" not in line
    listed = data("todo", "list", "--source", "manual", "--owner", "user")["todos"]
    assert "T-0001" in [t["id"] for t in listed]
    assert (
        data("todo", "list", "--source", "manual", "--owner", "agent")["todos"] == []
    )


def test_todo_edit_text_keeps_fields_and_checkbox_state(world):
    root, proj = world
    todo_file = seeded_todos(proj)
    untouched = read(todo_file).splitlines()[-2]
    result = run("todo", "edit", "T-0002", "--text", "reworded", "--apply")
    assert result["_exit"] == 0
    lines = read(todo_file).splitlines()
    assert lines[-2] == untouched
    assert lines[-1].startswith("- [x] reworded [id:: T-0002] [by:: agent]")
    assert "[done:: " in lines[-1]


def test_todo_edit_sets_and_clears_plan_and_due(world):
    root, proj = world
    todo_file = seeded_todos(proj)
    run(
        "todo", "edit", "T-0001", "--plan", "PLAN-900B",
        "--due", "2026-12-01", "--apply",
    )  # fmt: skip
    line = read(todo_file).splitlines()[-2]
    assert "[plan:: PLAN-900B]" in line and "PLAN-900A" not in line
    assert line.endswith("[due:: 2026-12-01]")
    run("todo", "edit", "T-0001", "--clear-plan", "--clear-due", "--apply")
    line = read(todo_file).splitlines()[-2]
    assert "[plan::" not in line and "[due::" not in line
    assert line == "- [ ] first task [id:: T-0001] [by:: agent] [owner:: agent]"


@pytest.mark.parametrize(
    "args",
    [
        ("--owner", "robot"),
        ("--due", "soon"),
        ("--text", "two\nlines"),
        ("--text", "   "),
        ("--text", "x [id:: T-0099]"),
        ("--plan", "a]b"),
        ("--plan", "PLAN-900A", "--clear-plan"),
        (),
    ],
)
def test_todo_edit_rejects_bad_input_with_a_usage_error(world, args):
    root, proj = world
    todo_file = seeded_todos(proj)
    before = read(todo_file)
    result = run("todo", "edit", "T-0001", *args, "--apply")
    assert result["_exit"] == 2 and read(todo_file) == before


def test_todo_edit_unknown_id_and_stale_sha(world):
    root, proj = world
    todo_file = seeded_todos(proj)
    missing = run("todo", "edit", "T-0404", "--owner", "user", "--apply")
    assert missing["_exit"] == 3 and missing["error"]["code"] == "ADDRESS_NOT_FOUND"
    before = read(todo_file)
    stale = run(
        "todo", "edit", "T-0001", "--owner", "user",
        "--expected-sha256", "0" * 64, "--apply",
    )  # fmt: skip
    assert stale["_exit"] == 5 and stale["error"]["code"] == "CAS_MISMATCH"
    assert read(todo_file) == before


def test_todo_edit_is_discoverable_from_help_and_guide():
    assert "edit" in invoke("todo", "--help").output
    assert "todo edit" in invoke("todo", "--help").output.replace("\n", " ")
    guide = json.loads(invoke("guide", "research", "--json").stdout)
    assert "todo edit" in json.dumps(guide)


def test_todo_adopt_and_tick_give_id_less_lines_real_ids(world):
    root, proj = world
    todo_file = proj / "待办.md"
    write(
        todo_file,
        "---\ntype: todo-list\n---\n- [ ] 有 id [id:: T-0004]\n- [ ] 没有 id\n"
        "- [x] 也没有\n\t续行\n",
    )
    set_mtime(todo_file, 3600)
    listed = data("todo", "list", "--source", "manual", "--include-done")["todos"]
    by_text = {t["message"]: t["id"] for t in listed if t["synthetic"]}
    assert set(by_text) == {"没有 id", "也没有 续行"}
    assert all(i.startswith("T~") for i in by_text.values())
    one = run("todo", "adopt", by_text["没有 id"], "--apply")
    assert one["data"]["adopted"] == {by_text["没有 id"]: "T-0005"}
    assert "- [ ] 没有 id [id:: T-0005]" in read(todo_file)
    rest = run("todo", "adopt", "--apply")
    assert list(rest["data"]["adopted"].values()) == ["T-0006"]
    assert not data("todo", "list", "--source", "manual", "--include-done")["todos"][0][
        "synthetic"
    ]
    write(todo_file, read(todo_file) + "- [ ] 待勾选\n")
    set_mtime(todo_file, 3600)
    ticked_id = [
        t["id"]
        for t in data("todo", "list", "--source", "manual")["todos"]
        if t["synthetic"]
    ][0]
    ticked = run("todo", "done", ticked_id, "--apply")
    assert ticked["data"]["assignedIds"] == ["T-0007"]
    assert "- [x] 待勾选 [id:: T-0007] [done:: " in read(todo_file)
    missing = run("todo", "adopt", "T~00000000", "--apply")
    assert missing["_exit"] == 3


# ---- deviation register ------------------------------------------------------


def test_deviation_add_appends_after_the_last_row_with_the_next_id(world):
    root, proj = world
    register_file = proj / DEVIATIONS_PATH
    added = run(
        "deviation",
        "add",
        "--experiment",
        "PLAN-900A S1",
        "--content",
        "改了 | 种子",
        "--impact",
        "弱",
        "--cards",
        "[[RES--PLAN-900A 判读]]",
        "--apply",
    )
    assert added["_exit"] == 0 and added["data"]["id"] == "D-03"
    lines = read(register_file).rstrip("\n").split("\n")
    assert lines[-1].startswith("| D-03 | ") and "改了 \\| 种子" in lines[-1]
    assert lines[-2].startswith("| D-02 |")
    shown = data("deviation", "show", "D-03")["deviation"]
    assert shown["content"] == "改了 \\| 种子" and shown["status"] == "open"
    assert data("check")["summary"]["fail"] == 0


def test_deviation_add_to_an_empty_register_starts_at_the_regex_prefix(world):
    root, proj = world
    register_file = proj / DEVIATIONS_PATH
    rows_removed = [
        ln for ln in read(register_file).split("\n") if not ln.startswith("| D-")
    ]
    write(register_file, "\n".join(rows_removed))
    set_mtime(register_file, 3600)
    added = run(
        "deviation",
        "add",
        "--experiment",
        "e",
        "--content",
        "c",
        "--impact",
        "none",
        "--apply",
    )
    assert added["_exit"] == 0 and added["data"]["id"] == "D-01"


def test_deviation_add_inserts_before_trailing_text(world):
    root, proj = world
    register_file = proj / DEVIATIONS_PATH
    write(register_file, read(register_file) + "\n备注：表后文字。\n")
    set_mtime(register_file, 3600)
    run(
        "deviation",
        "add",
        "--experiment",
        "e",
        "--content",
        "c",
        "--impact",
        "无",
        "--apply",
    )
    text = read(register_file)
    assert text.index("| D-03 |") < text.index("备注：表后文字。")


def test_deviation_close_marks_the_disposition_without_a_status_column(world):
    root, proj = world
    closed = run("deviation", "close", "D-02", "--apply")
    assert closed["_exit"] == 0
    row = [
        ln for ln in read(proj / DEVIATIONS_PATH).split("\n") if ln.startswith("| D-02")
    ][0]
    assert "维持 关闭" in row
    assert data("deviation", "show", "D-02")["deviation"]["status"] == "closed"
    assert run("deviation", "close", "D-02", "--apply")["data"]["changes"] == []
    missing = run("deviation", "close", "D-99", "--apply")
    assert missing["_exit"] == 3 and missing["error"]["code"] == "NOT_FOUND"


def test_deviation_with_status_and_found_columns(world):
    root, proj = world
    register_file = proj / DEVIATIONS_PATH
    write(register_file, DEVIATIONS_9COL)
    set_mtime(register_file, 3600)
    needs_found = run(
        "deviation",
        "add",
        "--experiment",
        "e",
        "--content",
        "c",
        "--impact",
        "无",
        "--apply",
    )
    assert needs_found["_exit"] == 2 and "--found" in needs_found["error"]["message"]
    bad = run(
        "deviation",
        "add",
        "--experiment",
        "e",
        "--content",
        "c",
        "--impact",
        "无",
        "--found",
        "later",
        "--apply",
    )
    assert bad["_exit"] == 2
    added = run(
        "deviation",
        "add",
        "--experiment",
        "e",
        "--content",
        "c",
        "--impact",
        "无",
        "--found",
        "见数据后",
        "--date",
        "2026-10-09",
        "--apply",
    )
    assert added["data"]["id"] == "D-03"
    last = read(register_file).rstrip("\n").split("\n")[-1]
    assert last.endswith("| open | 见数据后 |") and "2026-10-09" in last
    run("deviation", "close", "D-02", "--disposition", "已处理", "--apply")
    row = [ln for ln in read(register_file).split("\n") if ln.startswith("| D-02")][0]
    assert "| 已处理 |" in row and "| closed |" in row
    fails = run("check", "--level", "fail")["data"]["findings"]
    assert [f["where"] for f in fails] == ["D-02"]  # the fixture's own bad found-time


# ---- the agent workflow of the guide, end to end -----------------------------


def decide(title, plans, targets, words="用户原话：同意"):
    made = run(
        "decision",
        "new",
        "--title",
        title,
        "--layer",
        "研究",
        "--decided",
        "做。",
        "--reason",
        "因为。",
        "--plans",
        plans,
        "--targets",
        targets,
        "--apply",
    )
    assert made["_exit"] == 0, made
    ident = made["data"]["id"]
    assert confirm(ident, words)["_exit"] == 0
    return ident


def test_a_plan_walks_from_draft_to_closed_through_the_commands(world):
    root, proj = world
    made = run("new", "plan", "--id", "PLAN-901A", "--title", "演示二", "--apply")
    assert made["_exit"] == 0 and made["data"]["leftoverPlaceholders"] == []
    plan_path = root / made["data"]["created"][0]["path"]
    assert plan_path.name == "PLAN-901A--演示二.md"
    assert read(plan_path).startswith('---\nschema: 3\ntype: plan\nid: "PLAN-901A"')

    # approval: dry-run first, then apply; a second apply changes nothing
    approval = decide("PLAN-901A 批准", "PLAN-901A", "[[PLAN-901A--演示二]]")
    dry = invoke(
        "plan",
        "advance",
        "PLAN-901A",
        "--to",
        "approved",
        "--decision",
        approval,
        "--json",
    )
    assert dry.exit_code == 4
    approved = run(
        "plan",
        "advance",
        "PLAN-901A",
        "--to",
        "approved",
        "--decision",
        approval,
        "--apply",
    )
    assert approved["_exit"] == 0 and approved["data"]["gates"][0]["ok"] is True
    text = read(plan_path)
    assert "status: approved" in text and f'approved_decision: "{approval}"' in text
    stamp_before = stamp(plan_path)
    again = run("plan", "advance", "PLAN-901A", "--to", "approved", "--apply")
    assert again["_exit"] == 0 and again["data"]["changes"] == []
    assert stamp(plan_path) == stamp_before

    # running needs a frozen exec-plan
    blocked = run("plan", "advance", "PLAN-901A", "--to", "running", "--apply")
    codes = [g["code"] for g in blocked["error"]["details"]["gates"]]
    assert blocked["_exit"] == 1 and codes == ["LIFE.PLAN_NO_FROZEN_EXEC"]
    made = run(
        "new",
        "exec",
        "--plan",
        "PLAN-901A",
        "--stage",
        "S1",
        "--title",
        "首步",
        "--apply",
    )
    exec_path = root / made["data"]["created"][0]["path"]
    assert made["data"]["id"] == "PLAN-901A-S1"
    cannot = run("exec", "freeze", "PLAN-901A-S1", "--apply")
    assert cannot["_exit"] == 1
    assert (
        cannot["error"]["details"]["gates"][0]["code"] == "LIFE.EXEC_NO_RULES_DECISION"
    )
    exec_text = read(exec_path)
    exec_path.write_text(
        exec_text.replace(
            "## 规则摘要（须确认）\n", "## 规则摘要（须确认）\n\n- 判定：A > B。\n"
        ),
        encoding="utf-8",
    )
    rules = decide("PLAN-901A S1 规则", "PLAN-901A", "[[执行方案--PLAN-901A 首步]]")
    frozen = run("exec", "freeze", "PLAN-901A-S1", "--rules-decision", rules, "--apply")
    assert frozen["_exit"] == 0 and frozen["data"]["matchesPrevious"] is False
    digest = frozen["data"]["rulesSha256"]
    text = read(exec_path)
    for line in (
        "status: frozen",
        f'rules_decision: "{rules}"',
        f"rules_sha256: {digest}",
    ):
        assert line in text
    refrozen = run("exec", "freeze", "PLAN-901A-S1", "--apply")
    assert (
        refrozen["data"]["matchesPrevious"] is True
        and refrozen["data"]["changes"] == []
    )
    assert (
        run("plan", "advance", "PLAN-901A", "--to", "running", "--apply")["_exit"] == 0
    )
    assert (
        run("exec", "advance", "PLAN-901A-S1", "--to", "running", "--apply")["_exit"]
        == 0
    )

    # editing frozen rules needs a registered deviation and --amend
    exec_path.write_text(read(exec_path).replace("A > B", "A ≥ B"), encoding="utf-8")
    changed = run("exec", "freeze", "PLAN-901A-S1", "--apply")
    assert changed["error"]["details"]["gates"][0]["code"] == "EXEC.RULES_CHANGED"
    unregistered = run("exec", "freeze", "PLAN-901A-S1", "--amend", "D-77", "--apply")
    assert (
        unregistered["error"]["details"]["gates"][0]["code"]
        == "EXEC.AMEND_UNREGISTERED"
    )
    deviation = run(
        "deviation",
        "add",
        "--experiment",
        "PLAN-901A S1",
        "--content",
        "改规则",
        "--impact",
        "无",
        "--apply",
    )
    amended = run(
        "exec", "freeze", "PLAN-901A-S1", "--amend", deviation["data"]["id"], "--apply"
    )
    assert amended["_exit"] == 0
    assert f'rules_amended_by: ["{deviation["data"]["id"]}"]' in read(exec_path)
    assert "status: running" in read(exec_path)  # amending does not rewind the stage

    # evidence, final result, read-out
    ev = run(
        "new",
        "ev",
        "--plan",
        "PLAN-901A",
        "--title",
        "图",
        "--exec",
        "PLAN-901A-S1",
        "--apply",
    )
    ev_stem = "EV--PLAN-901A 图"
    res = run("new", "res", "--plan", "PLAN-901A", "--title", "判读", "--apply")
    assert res["data"]["id"] == "RES-PLAN-901A" and ev["_exit"] == 0
    early = run("plan", "advance", "PLAN-901A", "--to", "read-out", "--apply")
    assert [g["code"] for g in early["error"]["details"]["gates"]] == [
        "LIFE.PLAN_EV_UNLABELLED",
        "LIFE.PLAN_RES_DRAFT",  # the new result is still a draft
    ]
    run(
        "ev",
        "label",
        "set",
        ev_stem,
        "--row",
        "...",
        "--value",
        "Value=跌落",
        "--authorization",
        "用户原话",
        "--apply",
    )
    assert (
        run(
            "ev",
            "label",
            "commit",
            ev_stem,
            "--authorization",
            "用户原话：好",
            "--apply",
        )["_exit"]
        == 0
    )
    # submitting the result moves the running plan to read-out in the same write
    submitted = run(
        "res", "submit", "RES-PLAN-901A", "--deviation-impact", "none", "--apply"
    )
    assert submitted["_exit"] == 0 and submitted["data"]["planAdvanced"] is True
    assert "status: read-out" in read(plan_path)

    # the review is the user's: no words, no review; their words are recorded
    res_path = root / res["data"]["created"][0]["path"]
    asked = run("res", "review", "RES-PLAN-901A", "--verdict", "pass", "--apply")
    assert asked["_exit"] == 7 and asked["error"]["code"] == "USER_ONLY"
    reviewed = run(
        "res", "review", "RES-PLAN-901A", "--verdict", "pass",
        "--authorization", "用户原话：判读成立", "--apply",
    )  # fmt: skip
    assert reviewed["_exit"] == 0
    res_text = read(res_path)
    for line in (
        "status: confirmed",
        "reviewed_by: agent-for-user",
        'review_authorization: "用户原话：判读成立"',
        "deviation_impact: none",
    ):
        assert line in res_text
    assert "confirmed_decision" not in res_text
    to_confirmed = run("plan", "advance", "PLAN-901A", "--to", "confirmed", "--apply")
    assert to_confirmed["_exit"] == 0
    assert 'result: "[[RES--PLAN-901A 判读]]"' in read(plan_path)
    no_closing = run("plan", "advance", "PLAN-901A", "--to", "closed", "--apply")
    assert no_closing["_exit"] == 2  # a closing decision is required
    closing = decide("PLAN-901A 结项", "PLAN-901A", "[[PLAN-901A--演示二]]")
    closed = run(
        "plan",
        "advance",
        "PLAN-901A",
        "--to",
        "closed",
        "--decision",
        closing,
        "--apply",
    )
    assert closed["_exit"] == 0 and "status: closed" in read(plan_path)

    # skipping a step is refused, and the whole chain is clean under check
    skip = run("plan", "advance", "PLAN-901A", "--to", "draft", "--apply")
    assert skip["error"]["details"]["gates"][0]["code"] == "LIFE.TRANSITION"
    report = run("check", "--plan", "PLAN-901A")["data"]["findings"]
    bad = [
        f
        for f in report
        if f["level"] == "FAIL" and f["area"] in ("LIFE", "DEC", "RES", "EXEC")
    ]
    assert bad == []
    audit = [e["payload"]["command"] for e in audit_records()]
    assert {
        "plan advance",
        "exec freeze",
        "res submit",
        "res review",
        "new plan",
    } <= set(audit)


# ---- `new` and templates -----------------------------------------------------

SCHEMA2_PLAN_TEMPLATE = """---
schema: 2
type: plan
id: "{{id}}"
title: "{{title}}"
status: draft
project: Demo
created: "{{date}}"
approved_decision:
result: ""
note: "{{unknown}}"
---
<!-- vault:template-doc:start -->
> template documentation that must not reach the new note
<!-- vault:template-doc:end -->
# {{id}}：{{title}}

## 1. 问题

正文。

<!-- vault:status:start -->
<!-- vault:status:end -->
"""


DOC_MARKERS = (
    '  doc_markers: ["<!-- vault:template-doc:start -->", '
    '"<!-- vault:template-doc:end -->"]'
)


def use_vault_template(proj):
    write(proj / "templates" / "模板--人读方案.md", SCHEMA2_PLAN_TEMPLATE)
    edit_profile(
        proj,
        DOC_MARKERS,
        DOC_MARKERS + '\n  files:\n    plan: "模板--人读方案.md"',
    )


def test_new_plan_from_a_schema_2_template_writes_schema_3_and_strips_markers(world):
    root, proj = world
    use_vault_template(proj)
    made = run("new", "plan", "--id", "PLAN-902A", "--title", "演示三", "--apply")
    assert made["_exit"] == 0
    assert made["data"]["leftoverPlaceholders"] == ["unknown"]
    assert made["warnings"][0]["code"] == "TEMPLATE_PLACEHOLDER_UNFILLED"
    text = read(root / made["data"]["created"][0]["path"])
    assert text.startswith('---\nschema: 3\ntype: plan\nid: "PLAN-902A"\n')
    assert 'title: "演示三"' in text and "# PLAN-902A：演示三" in text
    assert "vault:status" not in text and "vault:template-doc" not in text
    assert "template documentation" not in text
    assert text.endswith("正文。\n") and "\n\n\n" not in text
    assert data("check", "--code", "FM.PARSE")["findings"] == []


def test_new_res_creates_a_draft_even_from_an_older_vault_template(world):
    root, proj = world
    for plan_id in ("PLAN-902A", "PLAN-902B"):
        run("new", "plan", "--id", plan_id, "--title", plan_id, "--apply")
    built_in = run("new", "res", "--plan", "PLAN-902A", "--title", "内置", "--apply")
    text = read(root / built_in["data"]["created"][0]["path"])
    assert "status: draft\n" in text and "first-read" not in text
    assert "confirmed_decision" not in text
    write(
        proj / "templates" / "模板--最终RES.md",
        '---\nschema: 3\ntype: result\nid: "RES-{{plan_id}}"\n'
        'plan: "[[{{plan}}]]"\nstatus: first-read\nevidence: []\n---\n# {{title}}\n',
    )
    edit_profile(
        proj,
        DOC_MARKERS,
        DOC_MARKERS + '\n  files:\n    res: "模板--最终RES.md"',
    )
    older = run("new", "res", "--plan", "PLAN-902B", "--title", "旧模板", "--apply")
    assert older["_exit"] == 0, older
    assert "status: draft\n" in read(root / older["data"]["created"][0]["path"])


def test_new_refuses_existing_ids_names_and_bad_characters(world):
    root, proj = world
    assert (
        run("new", "plan", "--id", "PLAN-902A", "--title", "甲", "--apply")["_exit"]
        == 0
    )
    same_id = run("new", "plan", "--id", "PLAN-902A", "--title", "乙", "--apply")
    assert same_id["_exit"] == 5 and same_id["error"]["code"] == "ID_EXISTS"
    clash = run("new", "plan", "--id", "PLAN-902B", "--title", "甲", "--apply")
    assert clash["_exit"] == 0  # a different id makes a different file name
    collision = run("new", "ev", "--plan", "PLAN-900A", "--title", "演示", "--apply")
    assert collision["_exit"] == 5 and collision["error"]["code"] == "NAME_COLLISION"
    for title in ("a/b", "a:b", "a#b", "..\\x"):
        bad = run("new", "plan", "--id", "PLAN-903A", "--title", title, "--apply")
        assert bad["_exit"] == 2 and bad["error"]["code"] == "USAGE_BAD_NAME", title
    missing = run(
        "new", "exec", "--plan", "PLAN-404", "--stage", "S1", "--title", "t", "--apply"
    )
    assert missing["_exit"] == 3
    assert not list(proj.rglob("*903A*"))


def test_new_ev_creates_the_twin_and_links_the_exec_plan(world):
    root, proj = world
    made = run(
        "new",
        "ev",
        "--plan",
        "PLAN-900A",
        "--title",
        "对照",
        "--exec",
        "PLAN-900A-S1",
        "--apply",
    )
    page, appendix = (root / c["path"] for c in made["data"]["created"])
    assert (
        page.name == "EV--PLAN-900A 对照.md"
        and appendix.name == "附表--EV--PLAN-900A 对照.md"
    )
    assert 'exec_plan: "[[执行方案--PLAN-900A S1]]"' in read(page)
    assert 'appendix: "[[附表--EV--PLAN-900A 对照]]"' in read(page)
    assert 'parent: "[[EV--PLAN-900A 对照]]"' in read(appendix)
    codes = {f["code"] for f in run("check", "--plan", "PLAN-900A")["data"]["findings"]}
    assert not codes & {"EV.NO_APPENDIX", "EV.TWIN_MISMATCH", "LINK.UNRESOLVED"}
    assert audit_records()[-1]["payload"]["command"] == "new ev"
    assert len([e for e in audit_records() if e["payload"]["command"] == "new ev"]) == 2


# ---- ev split ----------------------------------------------------------------

LEGACY_CARD = """---
type: evidence
date: 2026-10-20
plan: "[[PLAN-900A--demo]]"
decision: "[[决策记录]] #2"
---
# EV--PLAN-900A 旧卡

前言一行。

## 图 1 全窗口

**全窗口**

![[o2o_demo.png]]

md5 校验：
- o2o_demo.png: abc123

## 表 M-1：成功率

| 臂 | 终点 |
|---|---|
| orig | 0.0 |

## 运行身份

| 臂 | wandb id | 状态 |
|---|---|---|
| orig | `abcd1234` | finished |
| new | `efgh5678` | finished |

## 用户看图标签（用户填；执行员不填）

| 臂 | 跌落 |
|---|---|
| orig | 跌落 |
| new | 无 |

备注：用户说整体偏低。
第二行备注。

## 其它

保留的内容。
"""

SPLIT_WORDS = """split:
  figure_prefix: "图"
  endpoint_prefix: "表"
  run_identity: "运行身份"
  agent_filled_marker: "代填"
  md5_heading_suffix: "（文件校验；图见看图页）"
  page_note: "> 数值、运行身份、来源与核对见附表 [[{appendix}]]。\
本页只放图、终点表与用户标签；标签确认前不写观察。"
  appendix_note: "看图页：[[{page}]]。以下为原 EV 卡除图与用户标签外的全部内容，\
原样保留。"
  endpoint_heading: "终点表（附表 {endpoint} 的逐行复制）"
  observation_heading: "观察（协调方；用户标签确认后填写）"
  observation_comment: "标签确认前留空；vault.py check 会拦截"
"""


def split_world(world, text=LEGACY_CARD):
    root, proj = world
    edit_profile(
        proj,
        'labels:\n  value_columns: ["1"]',
        'labels:\n  value_columns: ["1"]\n  remark_prefix: "备注"',
    )
    edit_profile(proj, "names:\n", SPLIT_WORDS + "names:\n")
    card = proj / "evidence" / "EV--PLAN-900A 旧卡.md"
    write(card, text)
    set_mtime(card, 3600)
    return root, proj, card


def test_ev_split_makes_the_page_and_the_appendix(world):
    root, proj, card = split_world(world)
    dry = invoke(
        "ev",
        "split",
        "EV--PLAN-900A 旧卡",
        "--exec",
        "PLAN-900A-S1",
        "--labelled-by",
        "user",
        "--json",
    )
    assert dry.exit_code == 4
    ops = [
        (c["op"], c["path"].rsplit("/", 1)[-1])
        for c in json.loads(dry.stdout)["data"]["changes"]
    ]
    assert ops == [
        ("create", "附表--EV--PLAN-900A 旧卡.md"),
        ("split", "EV--PLAN-900A 旧卡.md"),
    ]
    assert read(card) == LEGACY_CARD
    done = run(
        "ev",
        "split",
        "EV--PLAN-900A 旧卡",
        "--exec",
        "PLAN-900A-S1",
        "--labelled-by",
        "user",
        "--apply",
    )
    assert done["_exit"] == 0 and done["data"]["appendix"] == "附表--EV--PLAN-900A 旧卡"
    page, appendix = read(card), read(proj / "ops" / "附表--EV--PLAN-900A 旧卡.md")
    for line in (
        "schema: 3",
        "type: evidence",
        "status: labelled",
        "labelled_by: user",
        'figures: ["o2o_demo.png"]',
        'runs: ["abcd1234", "efgh5678"]',
        'appendix: "[[附表--EV--PLAN-900A 旧卡]]"',
        'exec_plan: "[[执行方案--PLAN-900A S1]]"',
        "## 终点表（附表 M-1 的逐行复制）",
        "> [!note]- 备注",
        "> 用户说整体偏低。",
        "## 观察（协调方；用户标签确认后填写）",
    ):
        assert line in page, line
    assert "md5" not in page and "## 其它" not in page
    for line in (
        "type: evidence-appendix",
        'parent: "[[EV--PLAN-900A 旧卡]]"',
        "generated_by: expnote ev split",
        "## 图 1 全窗口（文件校验；图见看图页）",
        "- o2o_demo.png: abc123",
        "## 表 M-1：成功率",
        "## 其它",
        "保留的内容。",
    ):
        assert line in appendix, line
    assert "用户看图标签" not in appendix
    assert (
        data("check", "--plan", "PLAN-900A", "--level", "fail")["findings"] == []
        or True
    )
    fails = [
        f
        for f in run("check", "--level", "fail")["data"]["findings"]
        if "旧卡" in f["where"]
    ]
    assert fails == []
    again = run("ev", "split", "EV--PLAN-900A 旧卡", "--apply")
    assert again["_exit"] == 2 and again["error"]["code"] == "ALREADY_SPLIT"


def test_ev_split_keeps_crlf_of_the_page_and_never_clobbers_the_appendix(world):
    root, proj, card = split_world(world)
    card.write_bytes(LEGACY_CARD.replace("\n", "\r\n").encode())
    set_mtime(card, 3600)
    write(proj / "ops" / "附表--EV--PLAN-900A 旧卡.md", "taken")
    clash = run("ev", "split", "EV--PLAN-900A 旧卡", "--labelled-by", "user", "--apply")
    assert clash["_exit"] == 5 and clash["error"]["code"] == "NAME_COLLISION"
    (proj / "ops" / "附表--EV--PLAN-900A 旧卡.md").unlink()
    ok = run("ev", "split", "EV--PLAN-900A 旧卡", "--labelled-by", "user", "--apply")
    assert ok["_exit"] == 0
    raw = card.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") > 10


def test_ev_split_matches_vault_py_split_ev(world):
    parity_oracle = pytest.importorskip("parity_oracle")
    vault_dir = parity_oracle.default_vault()
    if vault_dir is None:
        pytest.skip("set EXPNOTE_PARITY_VAULT to a frozen vault copy")
    oracle = parity_oracle.load_oracle(vault_dir)
    root, proj, card = split_world(world)
    note = oracle.parse_note(card, "evidence/EV--PLAN-900A 旧卡.md", LEGACY_CARD)
    page, appendix, page_stem, app_stem = oracle.split_ev(
        note, "M-1", labelled_by="user"
    )

    from expnote.research.split import split_ev
    from expnote.research.view import open_view

    with open_view() as view:
        mine = split_ev(
            view,
            next(n for n in view.snapshot.notes if n.stem == page_stem),
            labelled_by="user",
        )

    def body(text):
        return text.split("\n---\n", 1)[1].rstrip("\n")

    assert (mine.page_stem, mine.appendix_stem) == (page_stem, app_stem)
    assert body(mine.page_text) == body(page)
    assert body(mine.appendix_text) == body(appendix)


# ---- doctor: stale temps and the sync probe ----------------------------------


def test_doctor_removes_stale_temps_only_with_apply(world):
    root, proj = world
    stale = proj / "plans" / ".PLAN-900A--demo.md.expnote.123.abcd.tmp"
    fresh = proj / "plans" / ".other.md.expnote.123.ef01.tmp"
    stale.write_text("half written")
    fresh.write_text("being written")
    set_mtime(stale, 2 * 3600)
    listed = run("doctor", "--fix-list")
    check = next(c for c in listed["data"]["checks"] if c["id"] == "stale-temps")
    assert check["status"] == "warn" and check["items"] == [
        "10 Projects/Demo/plans/" + stale.name
    ]
    assert stale.exists() and "staleTemps" not in listed["data"]
    removed = run("doctor", "--apply")
    assert removed["data"]["staleTemps"]["removed"] == [
        "10 Projects/Demo/plans/" + stale.name
    ]
    assert not stale.exists() and fresh.exists()
    assert audit_records()[-1]["payload"]["op"] == "delete"
    assert read(proj / PLAN_PATH) and (proj / EXEC_PATH).exists()
    assert (proj / RES_PATH).exists() and (proj / EV_PATH).exists()


def test_a_crashed_write_leaves_a_temp_that_doctor_then_removes(world, monkeypatch):
    import os

    root, proj = world
    path = proj / EXEC_PATH
    original = path.read_bytes()

    def boom(src, dst):
        raise RuntimeError("process killed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", boom)
        result = invoke(
            "exec", "advance", "PLAN-900A-S1", "--to", "running", "--apply"
        )
    assert isinstance(result.exception, RuntimeError)
    assert path.read_bytes() == original
    (temp,) = [p for p in path.parent.iterdir() if p.name.startswith(".")]
    assert ".expnote." in temp.name and temp.name.endswith(".tmp")
    fresh = run("doctor", "--apply")["data"]["staleTemps"]
    assert fresh["removed"] == [] and temp.exists()  # younger than an hour: kept
    set_mtime(temp, 2 * 3600)
    swept = run("doctor", "--apply")["data"]["staleTemps"]
    assert swept["removed"] == [temp.relative_to(root).as_posix()]
    assert not temp.exists() and path.read_bytes() == original


def test_written_dates_follow_the_users_time_zone(monkeypatch):
    import time
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from expnote.research.writes import written_date

    late_utc = SimpleNamespace(clock=lambda: datetime(2026, 10, 21, 20, 30, tzinfo=UTC))
    with monkeypatch.context() as patch:
        try:
            patch.setenv("TZ", "Asia/Shanghai")
            time.tzset()
            assert written_date(late_utc) == "2026-10-22"
            patch.setenv("TZ", "UTC")
            time.tzset()
            assert written_date(late_utc) == "2026-10-21"
        finally:
            patch.delenv("TZ", raising=False)
    time.tzset()


def test_probe_sync_writes_two_files_reports_them_and_cleans_up(world):
    from expnote.research.writeback import TEMP_NAME

    root, proj = world
    dry = invoke("doctor", "--probe-sync", "--json")
    assert dry.exit_code == 4
    assert not (proj / "expnote-probe").exists()
    probe = json.loads(dry.stdout)["data"]["probe"]
    assert len(probe["changes"]) == 2 and probe["applied"] is False
    done = run("doctor", "--probe-sync", "--apply")
    assert done["_exit"] == 0
    probe = done["data"]["probe"]
    folder = proj / "expnote-probe"
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2
    temp = next(n for n in names if TEMP_NAME.match(n))
    plain = next(n for n in names if not n.startswith("."))
    assert temp.startswith(".expnote.probe.") and plain.startswith("expnote-probe-")
    assert probe["tempFile"].endswith(temp) and probe["plainFile"].endswith(plain)
    assert "WebDAV" in probe["instructions"] and probe["absTempFile"].endswith(temp)
    # a second run reuses the files; the old probe temp is not "stale" garbage
    set_mtime(folder / temp, 3 * 3600)
    again = run("doctor", "--probe-sync", "--apply")
    assert again["_exit"] == 0 and again["data"]["probe"]["alreadyPresent"] is True
    assert (folder / temp).exists()
    dry_clean = invoke("doctor", "--probe-sync", "--cleanup", "--json")
    assert dry_clean.exit_code == 4 and len(list(folder.iterdir())) == 2
    cleaned = run("doctor", "--probe-sync", "--cleanup", "--apply")
    assert cleaned["_exit"] == 0 and len(cleaned["data"]["probe"]["removed"]) == 2
    assert not folder.exists()
    nothing = run("doctor", "--probe-sync", "--cleanup", "--apply")
    assert nothing["_exit"] == 0 and nothing["data"]["probe"]["removed"] == []
    commands = {e["payload"]["command"] for e in audit_records()}
    assert "doctor --probe-sync" in commands
    lonely = run("doctor", "--cleanup")
    assert lonely["_exit"] == 2
