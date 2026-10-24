"""Next steps for approved plans, plan cycles and `plan reopen`."""

from __future__ import annotations

import re

import pytest
from minivault import build_vault
from readkit import register
from test_gates import (
    CONFIRMED,
    PLAN,
    PLAN_STEM,
    PROPOSED,
    add_decision,
    advance,
    ev_text,
    exec_text,
    gate_codes,
    plan_findings,
    plan_text,
    put,
    read,
    result_text,
    set_plan,
    stored_row,
)
from writekit import audit_records, run

from expnote.research.todos import NEXT_PRIORITY

PLAN_FILE = f"plans/{PLAN_STEM}.md"
APPROVED = {"approved_decision": '"O2O-DEC-0009"'}


@pytest.fixture
def world(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj, "--quiet-window-seconds", "0")
    add_decision(proj, 9, CONFIRMED, f"[[{PLAN_STEM}]]")
    add_decision(proj, 10, PROPOSED, f"[[{PLAN_STEM}]]")
    put(proj, PLAN_FILE, plan_text("draft"))
    return root, proj


def todos(kind):
    body = run("todo", "list", "--kind", kind, "--plan", PLAN)
    return body["data"]["todos"]


def kinds():
    body = run("todo", "list", "--plan", PLAN)
    # decision 10 of the fixture stays proposed: it is not what these tests look at
    return {t["kind"] for t in body["data"]["todos"]} - {"decision-proposed"}


def finding_codes(plan=PLAN):
    body = run("check", "--plan", plan)
    return {f["code"] for f in [*body["data"]["findings"], *body["data"]["newCodes"]]}


# ---- derived next steps for approved plans --------------------------------------


def test_approved_plan_without_exec_needs_one(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    (item,) = todos("plan-needs-exec")
    assert item["owner"] == "agent" and item["severity"] == "action"
    assert f"expnote new exec --plan {PLAN}" in item["command"]
    assert kinds() == {"plan-needs-exec"}


def test_no_next_step_for_other_statuses(world):
    root, proj = world
    for status in ("draft", "running", "closed"):
        set_plan(proj, status, **APPROVED)
        assert not kinds() & {"plan-needs-exec", "plan-ready-to-run"}, status


def test_draft_exec_is_awaiting_freeze_and_no_duplicates(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    put(proj, "ops/exec.md", exec_text("draft", decision=None))
    (item,) = todos("exec-awaiting-freeze")
    assert item["command"].startswith(
        f"expnote exec freeze {PLAN}-S1 --self-approve --no-new-rules --apply"
    )
    assert item["altCommand"].startswith("expnote decision new ")
    assert f"--plans {PLAN}" in item["altCommand"] and "[[exec]]" in item["altCommand"]
    assert "plan-needs-exec" not in kinds()
    add_decision(proj, 11, PROPOSED, "[[exec]]")
    assert kinds() == {"exec-rules-unconfirmed", "plan-prep-while-rules-pending"}
    (prep,) = todos("plan-prep-while-rules-pending")
    assert prep["severity"] == "info" and prep["command"] is None
    assert "engineering preparation" in prep["message"]
    add_decision(proj, 12, CONFIRMED, "[[exec]]")
    assert "exec-awaiting-freeze" not in kinds()
    assert "decision-confirmed-not-applied" in kinds()


def test_new_rules_exec_gets_only_the_user_path(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    text = exec_text("draft", decision=None).replace(
        "status: draft", "status: draft\nnew_rules: true"
    )
    put(proj, "ops/exec.md", text)
    (item,) = todos("exec-awaiting-freeze")
    assert (
        item["command"].startswith("expnote decision new ")
        and item["altCommand"] is None
    )


def freeze_self(*args, apply=True):
    tail = ["--apply"] if apply else []
    return run("exec", "freeze", f"{PLAN}-S1", *args, *tail)


def test_self_approve_records_a_decision_and_freezes(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    path = put(proj, "ops/exec.md", exec_text("draft", decision=None))
    before = read(path)
    dry = freeze_self("--self-approve", "--no-new-rules", apply=False)
    assert dry["_exit"] == 4 and read(path) == before
    shown = "".join(str(c) for c in dry["data"]["changes"])
    assert "O2O-DEC-0011" in shown and "approved_by: agent" in shown
    done = freeze_self("--self-approve", "--no-new-rules")
    assert done["_exit"] == 0 and done["data"]["decision"] == "O2O-DEC-0011"
    text = read(path)
    for line in ("status: frozen", "approved_by: agent", "new_rules: false"):
        assert line in text
    assert 'rules_decision: "O2O-DEC-0011"' in text
    row = stored_row(11)
    assert row["status"] == "confirmed" and row["confirmed_by"] == "agent"
    assert row["plans"] == [PLAN] and row["targets"] == ["[[exec]]"]
    assert plan_findings()[1] == []
    assert "plan-ready-to-run" in kinds()
    shown = run("exec", "show", f"{PLAN}-S1")["data"]["exec"]
    assert shown["approvedBy"] == "agent" and shown["newRules"] is False
    assert shown["frozenOn"]
    bundle = run("plan", "bundle", PLAN)["data"]
    assert bundle["execPlans"][0]["approvedBy"] == "agent"
    users = run("next", "--owner", "user")["data"]["steps"]
    assert not [s for s in users if s["ref"] == "O2O-DEC-0011"]


def test_self_approve_refusals(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    path = put(proj, "ops/exec.md", exec_text("draft", decision=None))
    assert freeze_self("--self-approve")["_exit"] == 2  # needs --no-new-rules
    assert (
        freeze_self("--self-approve", "--no-new-rules", "--rules-decision", "9")[
            "_exit"
        ]
        == 2
    )
    path.write_text(
        read(path).replace("status: draft", "status: draft\nnew_rules: true"),
        encoding="utf-8",
    )
    refused = freeze_self("--self-approve", "--no-new-rules")
    assert gate_codes(refused) == ["LIFE.EXEC_RULES_NOT_USER"]
    assert "status: draft" in read(path)


def test_user_path_writes_approved_by_user(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    path = put(proj, "ops/exec.md", exec_text("draft", decision=None))
    assert freeze_self("--rules-decision", "9")["_exit"] == 0
    text = read(path)
    assert "approved_by: user" in text and "new_rules: true" in text


def test_agent_decision_cannot_back_new_rules(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    put(proj, "ops/exec.md", exec_text("draft", decision=None))
    assert freeze_self("--self-approve", "--no-new-rules")["_exit"] == 0
    path = proj / "ops" / "exec.md"
    path.write_text(
        read(path).replace("new_rules: false", "new_rules: true"), encoding="utf-8"
    )
    assert "LIFE.EXEC_RULES_NOT_USER" in plan_findings()[1]


def test_aborted_exec_counts_as_no_exec(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    put(
        proj,
        "ops/exec.md",
        exec_text("draft", decision=None).replace("draft", "aborted"),
    )
    assert "plan-needs-exec" in kinds()


def test_frozen_exec_means_ready_to_run(world):
    root, proj = world
    set_plan(proj, "approved", **APPROVED)
    put(proj, "ops/exec.md", exec_text("frozen"))
    (item,) = todos("plan-ready-to-run")
    assert item["command"].startswith(
        f"expnote plan advance {PLAN} --to running --apply"
    )
    assert [e["id"] for e in item["execPlans"]] == [f"{PLAN}-S1"]
    assert item["execPlans"][0]["path"].endswith("ops/exec.md")
    assert not kinds() & {"plan-needs-exec", "exec-awaiting-freeze"}
    steps = run("next")["data"]["steps"]
    assert any(s["kind"] == "plan-ready-to-run" for s in steps)


def test_next_priority_order():
    order = list(NEXT_PRIORITY)
    wanted = [
        "decision-confirmed-not-applied",
        "plan-gate-blocked",
        "plan-reopen-unmarked",
        "plan-ready-to-run",
        "plan-needs-exec",
        "exec-awaiting-freeze",
        "plan-prep-while-rules-pending",
    ]
    assert order[: len(wanted)] == wanted
    assert order.index("legacy-no-final-res") > order.index(
        "plan-prep-while-rules-pending"
    )


# ---- plan cycles ------------------------------------------------------------------


def reopened_plan(proj, status="approved", cycle='["[[exec]]", "[[r1]]"]', **fm):
    set_plan(
        proj,
        status,
        reopened_on="2026-10-21",
        earlier_cycle=cycle,
        **{**APPROVED, **fm},
    )


def test_earlier_cycle_exec_does_not_satisfy_the_running_gate(world):
    root, proj = world
    reopened_plan(proj)
    put(proj, "ops/exec.md", exec_text("done"))
    assert gate_codes(advance("running")) == ["LIFE.PLAN_NO_FROZEN_EXEC"]
    assert "plan-needs-exec" in kinds()
    put(proj, "ops/exec2.md", exec_text("frozen").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    assert advance("running")["_exit"] == 0


def test_ready_message_names_the_reopen_and_current_execs_only(world):
    root, proj = world
    reopened_plan(proj)
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "ops/exec2.md", exec_text("frozen").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    (item,) = todos("plan-ready-to-run")
    assert "reopened 2026-10-21" in item["message"]
    assert [e["id"] for e in item["execPlans"]] == [f"{PLAN}-S2"]


def test_earlier_cycle_result_does_not_count_for_the_new_cycle(world):
    root, proj = world
    reopened_plan(proj, "read-out")
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "ops/exec2.md", exec_text("frozen").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    put(proj, "results/r1.md", result_text("confirmed"))
    put(proj, "results/r2.md", result_text("first-read", "RES-NEW"))
    found = finding_codes()
    assert "LIFE.RES_MULTIPLE_ACTIVE" not in found
    assert "LIFE.PLAN_RES_COUNT" not in found
    assert "LIFE.REOPEN_RES_NOT_MERGED" in found  # r2 does not link r1
    put(
        proj,
        "results/r2.md",
        result_text("first-read", "RES-NEW").replace(
            "# RES-NEW", "# RES-NEW\n\nMerges [[r1]]."
        ),
    )
    assert "LIFE.REOPEN_RES_NOT_MERGED" not in finding_codes()


def test_passing_the_review_of_the_new_result_supersedes_the_earlier_one(world):
    root, proj = world
    reopened_plan(proj, "read-out")
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "ops/exec2.md", exec_text("done").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    put(proj, "results/r1.md", result_text("confirmed"))
    new_text = result_text("first-read", "RES-NEW").replace(
        "# RES-NEW", "# RES-NEW\n\n[[r1]]"
    )
    put(proj, "results/r2.md", new_text)
    words = ["--authorization", "用户原话：通过"]
    dry = run(
        "res", "review", "RES-NEW", "--verdict", "pass", "--deviation-impact", "none",
        *words,
    )  # fmt: skip
    assert dry["_exit"] == 4
    assert {c["path"].split("/")[-1] for c in dry["data"]["changes"]} == {
        "r1.md",
        "r2.md",
    }
    done = run(
        "res", "review", "RES-NEW", "--verdict", "pass", "--deviation-impact", "none",
        *words, "--apply",
    )  # fmt: skip
    assert done["_exit"] == 0 and done["data"]["supersedes"] == ["RES-PLAN-910A"]
    old = read(proj / "results" / "r1.md")
    assert "status: superseded" in old and 'superseded_by: "[[r2]]"' in old
    assert "status: confirmed" in read(proj / "results" / "r2.md")
    assert plan_findings()[1] == []


# ---- `new res` in a later cycle ------------------------------------------------


def new_res(title="新一轮"):
    return run("new", "res", "--plan", PLAN, "--title", title, "--apply")


def test_new_res_ids_follow_the_cycle(world):
    root, proj = world
    set_plan(proj, "running", **APPROVED)
    first = new_res("第一轮")
    assert first["data"]["id"] == f"RES-{PLAN}" and first["data"]["cycle"] == 1
    # reopened once: the finished cycle's plain-id result is earlier
    put(proj, "results/r1.md", result_text("confirmed"))
    reopened_plan(proj, "approved")
    second = new_res("第二轮")
    assert second["data"]["id"] == f"RES-{PLAN}-cycle2" and second["data"]["cycle"] == 2
    created = read(root / second["data"]["created"][0]["path"])
    assert f'id: "RES-{PLAN}-cycle2"' in created and "status: draft" in created
    # the same cycle cannot take a second final result id
    assert new_res("再来")["error"]["code"] == "ID_EXISTS"
    # reopened twice: both earlier results are listed, so this is cycle 3
    stem = second["data"]["created"][0]["path"].rsplit("/", 1)[-1][: -len(".md")]
    reopened_plan(proj, "approved", cycle=f'["[[exec]]", "[[r1]]", "[[{stem}]]"]')
    third = new_res("第三轮")
    assert third["data"]["id"] == f"RES-{PLAN}-cycle3" and third["data"]["cycle"] == 3


def test_new_res_refuses_an_id_that_exists(world):
    root, proj = world
    put(proj, "results/r1.md", result_text("confirmed"))
    put(proj, "results/r2.md", result_text("first-read", f"RES-{PLAN}-cycle2"))
    reopened_plan(proj, "approved")
    clash = new_res()
    assert clash["_exit"] == 5 and clash["error"]["code"] == "ID_EXISTS"


def test_a_reopened_plan_gets_a_submittable_final_result(world):
    root, proj = world
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "results/r1.md", result_text("confirmed"))
    put(proj, "ops/exec2.md", exec_text("running").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    put(proj, "evidence/e1.md", ev_text("labelled"))
    reopened_plan(proj, "running")
    made = new_res()
    assert made["data"]["id"] == f"RES-{PLAN}-cycle2"
    submitted = run(
        "res", "submit", f"RES-{PLAN}-cycle2", "--deviation-impact", "none", "--apply"
    )
    assert submitted["_exit"] == 0 and submitted["data"]["planAdvanced"] is True
    assert "status: read-out" in read(proj / PLAN_FILE)
    assert "status: first-read" in read(root / made["data"]["created"][0]["path"])


# ---- plan reopen ------------------------------------------------------------------


def reopen(*args, plan=PLAN, decision="11", apply=True):
    tail = ["--apply"] if apply else []
    return run("plan", "reopen", plan, "--decision", decision, *args, *tail)


def closed_plan(proj):
    set_plan(
        proj,
        "closed",
        approved_decision='"O2O-DEC-0009"',
        closed_decision='"O2O-DEC-0009"',
        result='"[[r1]]"',
        forced_close="true",
    )
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "results/r1.md", result_text("confirmed"))


def test_reopen_refusals(world):
    root, proj = world
    add_decision(proj, 11, CONFIRMED, f"[[{PLAN_STEM}]]")
    add_decision(proj, 12, CONFIRMED, "[[别的笔记]]")
    for status in ("draft", "approved", "superseded", "withdrawn"):
        set_plan(proj, status, **APPROVED)
        assert gate_codes(reopen()) == ["LIFE.REOPEN_STATUS"], status
    closed_plan(proj)
    before = read(proj / PLAN_FILE)
    assert gate_codes(reopen(decision="10")) == ["LIFE.REOPEN_DECISION_NOT_CONFIRMED"]
    assert gate_codes(reopen(decision="12")) == ["LIFE.REOPEN_DECISION_UNRELATED"]
    assert reopen(decision="404")["_exit"] == 3
    assert read(proj / PLAN_FILE) == before


def test_reopen_dry_run_then_apply(world):
    root, proj = world
    add_decision(proj, 11, CONFIRMED, f"[[{PLAN_STEM}]]")
    closed_plan(proj)
    path = proj / PLAN_FILE
    before = read(path)
    dry = reopen(apply=False)
    assert dry["_exit"] == 4 and dry["data"]["dryRun"] is True
    assert read(path) == before
    done = reopen()
    assert done["_exit"] == 0 and done["data"]["to"] == "approved"
    text = read(path)
    assert "status: approved" in text
    assert 'approved_decision: "O2O-DEC-0011"' in text
    assert 'closed_decision: ""' in text and 'result: ""' in text
    assert "forced_close" not in text
    assert re.search(r"^reopened_on: \d{4}-\d{2}-\d{2}$", text, re.M)
    assert text.split("---\n", 2)[2] == before.split("---\n", 2)[2]  # body untouched
    cycle = re.search(r"^earlier_cycle: (.*)$", text, re.M).group(1)
    assert '"[[exec]]"' in cycle and '"[[r1]]"' in cycle
    assert [r["payload"]["command"] for r in audit_records()][-1] == "plan reopen"
    assert plan_findings()[1] == []
    assert "plan-needs-exec" in kinds()


def test_repeated_reopen_accumulates_the_earlier_cycle(world):
    root, proj = world
    add_decision(proj, 11, CONFIRMED, f"[[{PLAN_STEM}]]")
    add_decision(proj, 12, CONFIRMED, f"[[{PLAN_STEM}]]")
    closed_plan(proj)
    assert reopen()["_exit"] == 0
    put(proj, "ops/exec2.md", exec_text("done").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    path = proj / PLAN_FILE
    path.write_text(
        read(path).replace("status: approved", "status: closed", 1), encoding="utf-8"
    )
    from writekit import set_mtime

    set_mtime(path, 3600)
    assert reopen(decision="12")["_exit"] == 0
    cycle = re.search(r"^earlier_cycle: (.*)$", read(path), re.M).group(1)
    assert [cycle.count(f'"[[{s}]]"') for s in ("exec", "r1", "exec2")] == [1, 1, 1]


def test_bundle_marks_earlier_cycle_members(world):
    root, proj = world
    reopened_plan(proj)
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "ops/exec2.md", exec_text("frozen").replace(f"{PLAN}-S1", f"{PLAN}-S2"))
    put(proj, "results/r1.md", result_text("confirmed"))
    bundle = run("plan", "bundle", PLAN)["data"]
    assert bundle["plan"]["reopenedOn"] == "2026-10-21"
    assert bundle["plan"]["earlierCycle"] == ["exec", "r1"]
    flags = {e["id"]: e["earlierCycle"] for e in bundle["execPlans"]}
    assert flags == {f"{PLAN}-S1": True, f"{PLAN}-S2": False}
    assert [r["earlierCycle"] for r in bundle["results"]] == [True]
    plain = run("plan", "show", PLAN)["data"]["plan"]
    assert plain["reopenedOn"] == "2026-10-21"


# ---- --mark-existing --------------------------------------------------------------


def hand_reopened(proj):
    set_plan(proj, "approved", **APPROVED)
    put(proj, "ops/exec.md", exec_text("frozen"))  # frozen_on 2026-10-03
    put(proj, "results/r1.md", result_text("confirmed", extra="created: 2026-10-01\n"))
    put(
        proj,
        "results/r2.md",
        result_text("first-read", "RES-NEW", extra="created: 2026-10-20\n"),
    )
    put(proj, "evidence/e1.md", ev_text("labelled"))  # no date


def test_unmarked_reopen_is_flagged_then_marked(world):
    root, proj = world
    hand_reopened(proj)
    assert "LIFE.PLAN_REOPEN_UNMARKED" in finding_codes()
    (item,) = todos("plan-reopen-unmarked")
    assert item["command"].startswith(
        f"expnote plan reopen {PLAN} --mark-existing --decision O2O-DEC-0009"
    )
    refused = reopen("--mark-existing", decision="9", apply=False)
    assert gate_codes(refused) == ["LIFE.REOPEN_MARK_UNDATED"]
    assert "--earlier" in refused["error"]["remediation"]
    dry = reopen("--mark-existing", "--earlier", "e1", decision="9", apply=False)
    assert dry["_exit"] == 4
    split = dry["data"]["split"]
    assert {m["stem"] for m in split["earlier"]} == {"exec", "r1", "e1"}
    assert {m["stem"] for m in split["current"]} == {
        "r2"
    }  # same-day member stays current
    done = reopen("--mark-existing", "--earlier", "e1", decision="9")
    assert done["_exit"] == 0
    text = read(proj / PLAN_FILE)
    assert "reopened_on: 2026-10-20" in text and "status: approved" in text
    found = finding_codes()
    assert "LIFE.PLAN_REOPEN_UNMARKED" not in found
    assert "LIFE.REOPEN_RES_NOT_MERGED" in found
    assert not todos("plan-reopen-unmarked")
    assert reopen("--mark-existing", decision="9")["data"]["changes"] == []  # no-op


def test_mark_existing_needs_the_approving_decision(world):
    root, proj = world
    hand_reopened(proj)
    add_decision(proj, 11, CONFIRMED, f"[[{PLAN_STEM}]]")
    assert gate_codes(reopen("--mark-existing", decision="11")) == [
        "LIFE.REOPEN_MARK_STATE"
    ]
    set_plan(proj, "closed", **APPROVED)
    assert gate_codes(reopen("--mark-existing", decision="9")) == [
        "LIFE.REOPEN_MARK_STATE"
    ]


def other_member(proj, name="check-report"):
    put(
        proj,
        f"ops/{name}.md",
        f'---\ntype: report\nplan: "[[{PLAN_STEM}]]"\n---\n# report\n',
    )


def test_only_lifecycle_members_are_split_by_cycle(world):
    root, proj = world
    hand_reopened(proj)
    other_member(proj)
    dry = reopen("--mark-existing", "--earlier", "e1", decision="9", apply=False)
    assert dry["_exit"] == 4
    stems = {m["stem"] for k in ("earlier", "current") for m in dry["data"]["split"][k]}
    assert "check-report" not in stems
    assert "check-report" not in " ".join(dry["data"]["earlierCycle"])
    add_decision(proj, 11, CONFIRMED, f"[[{PLAN_STEM}]]")
    closed_plan(proj)
    assert reopen()["_exit"] == 0
    cycle = re.search(r"^earlier_cycle: (.*)$", read(proj / PLAN_FILE), re.M).group(1)
    assert "check-report" not in cycle and "[[r1]]" in cycle


def test_unmarked_reopen_suppresses_other_plan_items_and_lists_undated(world):
    root, proj = world
    hand_reopened(proj)
    put(proj, "ops/exec.md", exec_text("draft", decision=None))
    found = kinds()
    assert "plan-reopen-unmarked" in found
    assert not found & {
        "plan-ready-to-run",
        "plan-needs-exec",
        "exec-awaiting-freeze",
        "plan-prep-while-rules-pending",
    }
    (item,) = todos("plan-reopen-unmarked")
    assert item["undated"] == ["e1", "exec"] and "--earlier <stem>" in item["message"]
    assert item["command"].startswith(f"expnote plan reopen {PLAN} --mark-existing")
    assert "--apply" not in item["command"]
