"""Derived and manual TODOs, `next`, `doctor` and the read-only guarantee."""

from __future__ import annotations

import os
import shutil
import time

import pytest
from decisionkit import seed
from minivault import (
    EV_PATH,
    EXEC_BODY,
    EXEC_FM,
    PLAN_PATH,
    RES,
    build_vault,
    make_workspace,
    snapshot_tree,
    write,
)
from readkit import data, invoke, register, run
from test_checks_schema3 import v3_plan

TODO_FILE = (
    "---\ntype: todo-list\n---\n# 待办\n\n"
    "- [ ] 用户事项 [[别的笔记]] [id:: T-0007] [plan:: PLAN-900A] "
    "[due:: 2026-10-25] [by:: agent]\n"
    "  续行说明\n"
    "- [x] 已完成事项 [id:: T-0003] [done:: 2026-10-21]\n"
    "- [ ] 没有编号的一行\n"
    "- [ ] 给代理的事项 [id:: T-0009] [owner:: agent]\n"
)


@pytest.fixture
def vault(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    return root, proj


def plan_c(**overrides) -> str:
    fields = {
        "id": "PLAN-900C",
        "title": '"PLAN-900C"',
        "status": "draft",
        "approved_decision": None,
        "closed_decision": None,
        "result": None,
    }
    fields.update(overrides)
    return v3_plan(**fields)


def exec_c() -> str:
    head = EXEC_FM.replace("PLAN-900A-S1", "PLAN-900C-S1").replace(
        "[[PLAN-900A--demo]]", "[[PLAN-900C--new]]"
    )
    return head + EXEC_BODY


def res_c(status="first-read", extra="") -> str:
    return (
        RES.replace("RES-PLAN-900A", "RES-PLAN-900C")
        .replace("[[PLAN-900A--demo]]", "[[PLAN-900C--new]]")
        .replace("status: confirmed", f"status: {status}")
        .replace("confirmed_decision: 4\n", "")
        .replace("deviation_impact: weak\n", extra)
    )


def todos(**filters):
    args = ["todo", "list"]
    for key, value in filters.items():
        args += [f"--{key}", value]
    return data(*args)["todos"]


def kinds(**filters):
    return {(t["kind"], t["ref"]): t for t in todos(**filters)}


def test_decision_proposed_and_awaiting_approval(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900C--new.md", plan_c())
    seed(125, status="proposed", plans=["PLAN-900C"])
    found = kinds()
    proposed = found[("decision-proposed", "O2O-DEC-0125")]
    assert proposed["owner"] == "user" and proposed["severity"] == "action"
    assert (
        proposed["plan"] == "PLAN-900C"
        and proposed["id"] == "derived:decision-proposed:O2O-DEC-0125"
    )
    awaiting = found[("plan-awaiting-approval", "PLAN-900C")]
    assert awaiting["owner"] == "user" and "O2O-DEC-0125" in awaiting["message"]
    assert awaiting["decisions"] == ["O2O-DEC-0125"]  # the GUI offers them inline


def test_ev_unlabelled_and_ev_unmapped(vault):
    root, proj = vault
    path = proj / EV_PATH
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "status: labelled", "status: rendered"
        ),
        encoding="utf-8",
    )
    write(proj / "evidence/EV--旧卡.md", "---\ntype: evidence\n---\n# 旧卡\n")
    found = kinds()
    assert found[("ev-unlabelled", "EV--PLAN-900A 演示")]["owner"] == "user"
    assert found[("ev-unlabelled", "EV--PLAN-900A 演示")]["plan"] == "PLAN-900A"
    unmapped = found[("ev-unmapped", "EV--旧卡")]
    assert unmapped["owner"] == "agent" and unmapped["severity"] == "info"


def test_exec_rules_unconfirmed_and_res_awaiting_confirmation(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900C--new.md", plan_c(status="read-out"))
    write(proj / "ops/执行方案--PLAN-900C S1.md", exec_c())
    write(proj / "results/RES--PLAN-900C 判读.md", res_c())
    seed(
        125, status="proposed", plans=["PLAN-900C"],
        targets=["[[执行方案--PLAN-900C S1]]"],
    )  # fmt: skip
    seed(
        126, status="proposed", plans=["PLAN-900C"],
        targets=["[[RES--PLAN-900C 判读]]"],
    )  # fmt: skip
    found = kinds()
    rules = found[("exec-rules-unconfirmed", "PLAN-900C-S1")]
    assert rules["owner"] == "user"
    assert rules["decision"] == "O2O-DEC-0125"
    assert rules["stem"] == "执行方案--PLAN-900C S1"
    result = found[("res-awaiting-confirmation", "RES-PLAN-900C")]
    assert result["plan"] == "PLAN-900C"
    assert result["decision"] == "O2O-DEC-0126"
    assert result["stem"] == "RES--PLAN-900C 判读"


def test_confirmed_decisions_not_yet_applied_carry_the_exact_command(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900C--new.md", plan_c())
    write(proj / "ops/执行方案--PLAN-900C S1.md", exec_c())
    write(proj / "results/RES--PLAN-900C 判读.md", res_c())
    for number, target in (
        (125, "PLAN-900C--new"),
        (126, "执行方案--PLAN-900C S1"),
        (127, "RES--PLAN-900C 判读"),
    ):
        seed(number, plans=["PLAN-900C"], targets=[f"[[{target}]]"])
    found = kinds(owner="agent")
    approval = found[("decision-confirmed-not-applied", "O2O-DEC-0125:PLAN-900C")]
    assert approval["role"] == "approval" and approval["owner"] == "agent"
    assert approval["command"] == (
        "expnote plan advance PLAN-900C --to approved --decision O2O-DEC-0125 "
        "--apply --project off2on --json"
    )
    rules = found[("decision-confirmed-not-applied", "O2O-DEC-0126:PLAN-900C-S1")]
    assert rules["command"].startswith(
        "expnote exec freeze PLAN-900C-S1 --rules-decision O2O-DEC-0126"
    )
    result = found[("decision-confirmed-not-applied", "O2O-DEC-0127:RES-PLAN-900C")]
    assert "res confirm RES-PLAN-900C --decision O2O-DEC-0127" in result["command"]


def test_deviation_open_gate_blocked_and_legacy_no_final_res(vault):
    root, proj = vault
    found = kinds()
    assert ("deviation-open", "D-02") in found and (
        "deviation-open",
        "D-01",
    ) not in found
    assert found[("deviation-open", "D-02")]["plan"] == "PLAN-900A"
    write(proj / "plans/PLAN-900C--new.md", plan_c(status="approved"))
    write(
        proj / "plans/PLAN-900D--old.md",
        plan_c(id="PLAN-900D", title='"PLAN-900D"', status="closed", legacy="true"),
    )
    write(proj / "results/RES--PLAN-900D.md", res_c().replace("PLAN-900C", "PLAN-900D"))
    found = kinds()
    blocked = found[("plan-gate-blocked", "PLAN-900C")]
    assert blocked["owner"] == "agent" and "LIFE.PLAN_NO_APPROVAL" in blocked["message"]
    assert (
        blocked["command"] == "expnote check --plan PLAN-900C --project off2on --json"
    )
    assert ("legacy-no-final-res", "PLAN-900D") in found
    assert ("legacy-no-final-res", "PLAN-900A") not in found  # its result is confirmed


def test_decision_review_due_is_date_driven(vault):
    root, proj = vault
    seed(125, review_by="2020-01-01")
    seed(126, review_by="2999-01-01")
    found = kinds()
    assert ("decision-review-due", "O2O-DEC-0125") in found
    assert ("decision-review-due", "O2O-DEC-0126") not in found


def test_stale_running_runs_come_from_the_workspace(tmp_path):
    root, proj = build_vault(tmp_path)
    make_workspace(
        "w1",
        runs=[
            {
                "id": "stale001",
                "topic": "T",
                "status": "running",
                "started_at": "2020-01-01T00:00:00+00:00",
            },
            {
                "id": "fresh001",
                "topic": "T",
                "status": "running",
                "started_at": "2999-01-01T00:00:00+00:00",
            },
            {
                "id": "done0001",
                "topic": "T",
                "status": "finished",
                "started_at": "2020-01-01T00:00:00+00:00",
            },
        ],
    )
    register(root, proj, "--workspace", "w1")
    item = kinds()[("stale-running-runs", "w1")]
    assert item["runs"] == ["stale001"] and item["owner"] == "agent"
    assert item["command"] == "expnote validate --workspace w1 --json"


def test_manual_todos_follow_the_grammar(vault):
    root, proj = vault
    write(proj / "待办.md", TODO_FILE)
    manual = todos(source="manual")
    assert [t["id"] for t in manual][:2] == ["T-0007", manual[1]["id"]]
    by_id = {t["id"]: t for t in manual}
    first = by_id["T-0007"]
    assert first["message"] == "用户事项 [[别的笔记]] 续行说明"
    assert (first["plan"], first["due"], first["by"], first["owner"]) == (
        "PLAN-900A",
        "2026-10-25",
        "agent",
        "user",
    )
    assert (
        first["source"] == "manual"
        and first["line"] == 6
        and first["synthetic"] is False
    )
    synthetic = [t for t in manual if t["synthetic"]]
    assert (
        len(synthetic) == 1
        and synthetic[0]["id"].startswith("T~")
        and synthetic[0]["message"] == "没有编号的一行"
    )
    assert "T-0003" not in by_id  # ticked items are hidden by default
    shown = data("todo", "list", "--source", "manual", "--include-done")["todos"]
    assert {t["id"]: t["state"] for t in shown}["T-0003"] == "done"
    assert by_id["T-0009"]["owner"] == "agent"
    assert [t["id"] for t in todos(source="manual", plan="PLAN-900A")] == ["T-0007"]
    assert [t["id"] for t in todos(source="manual", owner="agent")] == ["T-0009"]
    assert all(t["source"] == "derived" for t in todos(source="derived"))


def test_todo_ordering_puts_the_user_first(vault):
    root, proj = vault
    write(proj / "待办.md", TODO_FILE)
    seed(125, status="proposed")
    owners = [t["owner"] for t in todos()]
    assert owners == sorted(owners, key=lambda o: o != "user")
    assert todos()[0]["kind"] == "decision-proposed"


def test_todo_list_rejects_bad_options(vault):
    bad = [
        ("todo", "list", "--owner", "robot"),
        ("todo", "list", "--source", "x"),
        ("next", "--owner", "robot"),
    ]
    for args in bad:
        body = run(*args)
        assert body["_exit"] == 2 and body["error"]["code"] == "USAGE_BAD_OPTION"


def test_next_lists_agent_steps_with_commands(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900C--new.md", plan_c())
    seed(125, plans=["PLAN-900C"], targets=["[[PLAN-900C--new]]"])
    seed(126, status="proposed")
    body = data("next")
    assert body["owner"] == "agent"
    steps = body["steps"]
    assert [s["order"] for s in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["kind"] == "decision-confirmed-not-applied"
    assert steps[0]["command"].startswith(
        "expnote plan advance PLAN-900C --to approved"
    )
    assert any(s["kind"] == "deviation-open" for s in steps)
    assert body["waiting"]["owner"] == "user" and body["waiting"]["count"] >= 1
    assert body["waiting"]["items"][0]["kind"] == "decision-proposed"
    user = data("next", "--owner", "user")
    assert (
        user["steps"][0]["kind"] == "decision-proposed"
        and user["waiting"]["owner"] == "agent"
    )
    assert "$ expnote plan advance" in invoke("next").stdout


def doctor_checks(*extra):
    body = run("doctor", *extra)
    return body, {c["id"]: c for c in body["data"]["checks"]}


def test_doctor_is_clean_on_a_tidy_vault(vault):
    body, checks = doctor_checks()
    assert body["_exit"] == 0 and body["data"]["summary"]["fail"] == 0
    assert body["data"]["summary"]["warn"] == 0
    quiet = (
        "vault-reachable profile-note pyyaml fts-trigram index profile-sha "
        "sync-conflicts code-files dot-directories status-markers "
        "projection-folder windows-filenames"
    ).split()
    for name in quiet:
        assert checks[name]["status"] == "ok", name
    assert checks["workspaces"]["status"] == "info"
    assert (
        checks["recently-modified"]["status"] == "info"
    )  # the fixture was just written


def test_doctor_reports_hygiene_problems(tmp_path):
    root, proj = build_vault(tmp_path)
    ws = make_workspace("w1")
    (ws / "config.toml").write_text(
        f'obsidian_root = "{root}"\nnotes_dir = "50 Archive/Expnote/w1/runs"\n'
        'docs_dir = "50 Archive/Expnote/w1/analyses"\n',
        encoding="utf-8",
    )
    register(root, proj, "--workspace", "w1")
    write(root / "50 Archive/Expnote/w1/runs/abc12345.md", "# run")
    write(proj / "templates/vault.py", "print(1)")
    write(proj / "results/.aws/credentials.md", "secret")
    write(proj / "别的笔记.sync-conflict-1.md", "x")
    write(proj / "bad:name.md", "x")
    old = proj / ".别的笔记.expnote.123.abc.tmp"
    write(old, "x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    plan = proj / PLAN_PATH
    plan.write_text(
        plan.read_text(encoding="utf-8") + "\n<!-- vault:status:start -->\n",
        encoding="utf-8",
    )
    body, checks = doctor_checks()
    assert body["_exit"] == 0  # hygiene findings warn, they do not fail
    assert checks["code-files"]["items"] == ["10 Projects/Demo/templates/vault.py"]
    assert checks["dot-directories"]["items"] == ["10 Projects/Demo/results/.aws"]
    assert checks["sync-conflicts"]["count"] == 1
    assert checks["stale-temps"]["items"] == [
        "10 Projects/Demo/.别的笔记.expnote.123.abc.tmp"
    ]
    assert checks["status-markers"]["items"] == [
        "10 Projects/Demo/plans/PLAN-900A--demo.md"
    ]
    assert checks["windows-filenames"]["items"] == ["10 Projects/Demo/bad:name.md"]
    assert checks["projection-folder"]["status"] == "warn"
    assert "50 Archive/Expnote/w1/runs" in checks["projection-folder"]["items"][0]
    assert checks["workspaces"]["status"] == "ok"
    assert all(c["remediation"] for c in checks.values() if c["status"] == "warn")


def test_doctor_fix_list_shows_every_path(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    for index in range(25):
        write(proj / f"templates/c{index}.py", "x")
    short = doctor_checks()[1]["code-files"]
    assert (
        short["count"] == 25
        and len(short["items"]) == 20
        and short["truncated"] is True
    )
    full = doctor_checks("--fix-list")[1]["code-files"]
    assert len(full["items"]) == 25 and "truncated" not in full


def test_doctor_no_refresh_reports_pending_changes(vault):
    root, proj = vault
    run("reindex")
    assert doctor_checks("--no-refresh")[1]["index-fresh"]["status"] == "ok"
    write(proj / "新.md", "x")
    stale = doctor_checks("--no-refresh")[1]["index-fresh"]
    assert stale["status"] == "warn" and "1 file" in stale["message"]


def test_doctor_unreachable_vault_is_a_failed_check_not_a_crash(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    shutil.move(str(root), str(tmp_path / "moved"))
    body, checks = doctor_checks()
    assert body["_exit"] == 1 and checks["vault-reachable"]["status"] == "fail"
    assert "project rebind" in checks["vault-reachable"]["remediation"]


def test_doctor_without_a_project_is_not_found(tmp_path):
    body = run("doctor")
    assert body["_exit"] == 3 and body["error"]["code"] == "PROJECT_NOT_REGISTERED"


def test_doctor_flags_a_changed_profile_note(vault):
    root, proj = vault
    run("reindex")
    profile = proj / "expnote 项目配置.md"
    profile.write_text(
        profile.read_text(encoding="utf-8") + "\n追加说明。\n", encoding="utf-8"
    )
    body, checks = doctor_checks("--no-refresh")
    assert checks["profile-sha"]["status"] == "warn"
    assert doctor_checks()[1]["profile-sha"]["status"] == "ok"


READ_COMMANDS = [
    ["check"],
    ["reindex"],
    ["reindex", "--full"],
    ["search", "PLAN-900A"],
    ["search", "熵", "--all-vault"],
    ["search", "", "--backlinks-of", "PLAN-900A--demo"],
    ["search", "demo", "--images"],
    ["plan", "list"],
    ["plan", "show", "PLAN-900A", "--include-body"],
    ["plan", "bundle", "PLAN-900A", "--depth", "2", "--include-body"],
    ["plan", "bundle", "PLAN-900A", "--max-bytes", "2000"],
    ["decision", "list"],
    ["decision", "show", "2", "--include-body"],
    ["exec", "list"],
    ["exec", "show", "PLAN-900A-S1"],
    ["ev", "list"],
    ["res", "list"],
    ["deviation", "list"],
    ["deviation", "show", "D-02"],
    ["todo", "list"],
    ["next"],
    ["doctor", "--fix-list"],
    ["guide", "research"],
]


def test_read_commands_never_touch_the_vault(tmp_path):
    root, proj = build_vault(tmp_path)
    write(proj / "待办.md", TODO_FILE)
    seed(125, status="proposed")
    write(proj / "别的笔记.sync-conflict-1.md", "x")
    make_workspace("w1", runs=[{"id": "0012345", "topic": "T"}])
    register(root, proj, "--workspace", "w1")
    time.sleep(0.02)
    before = snapshot_tree(root)
    for command in READ_COMMANDS:
        result = (
            invoke(*command, "--json") if command[0] != "guide" else invoke(*command)
        )
        assert result.exit_code in (0, 1), (command, result.output)
        assert snapshot_tree(root) == before, command
    assert (
        snapshot_tree(root.parent / ".cfg") is not None
    )  # config home lives outside the vault


def test_review_due_follows_the_injected_clock(tmp_path):
    from datetime import datetime

    from minivault import make_context

    from expnote.research.todos import derived_todos
    from expnote.research.view import open_view

    root, proj = build_vault(tmp_path)
    seed(125, review_by="2026-06-01")
    context = make_context(root, proj)

    def due(now):
        with open_view(context=context, clock=lambda: now) as view:
            items, _ = derived_todos(view)
        return [i["ref"] for i in items if i["kind"] == "decision-review-due"]

    assert due(datetime(2026, 5, 31)) == []
    assert due(datetime(2026, 6, 1)) == ["O2O-DEC-0125"]
