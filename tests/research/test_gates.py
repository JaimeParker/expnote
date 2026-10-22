"""Every row of the lifecycle gate table (design 7.1) as a pass/fail pair.

Rows with a write command (`plan advance`, `exec freeze|advance`, `res confirm`,
`ev label commit`, `decision set`) are driven through the command: the failing case
exits 1 `GATE_FAILED` (or 7 for user-only acts) with the gate code in
`error.details.gates`, the passing case writes. Static rows have no writer, so
they are paired through `check` on the scenario vaults.
"""

from __future__ import annotations

import pytest
from decisionkit import reseed
from minivault import (
    DECISIONS_PATH,
    EXEC_BODY,
    PROFILE_FIXTURE,
    SCENARIOS,
    build_vault,
    scenario_vault,
    write,
)
from readkit import register
from writekit import edit_profile, run, set_mtime

from expnote.research.checks import rules_sha256
from expnote.research.decision_store import read_rows
from expnote.research.profile import read_profile_note

PLAN = "PLAN-910A"
PLAN_STEM = "PLAN-910A--t"
CONFIRMED = "已确认（用户）"
PROPOSED = "待用户决定"


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj, "--quiet-window-seconds", "0")
    add_decision(proj, 9, CONFIRMED, f"[[{PLAN_STEM}]]")
    add_decision(proj, 10, PROPOSED, f"[[{PLAN_STEM}]]")
    put(proj, f"plans/{PLAN_STEM}.md", plan_text("draft"))
    return root, proj


def put(proj, rel, text):
    path = proj / rel
    write(path, text)
    set_mtime(path, 3600)
    return path


def add_decision(proj, number, status, lands, text="决定"):
    path = proj / DECISIONS_PATH
    row = f"| {number} | 2026-10-20 | {text} | 理由 | {lands} | {status} | 研究 |\n"
    path.write_text(read(path) + row, encoding="utf-8")
    set_mtime(path, 3600)


def plan_text(status, legacy=False, **fm):
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    flag = "legacy: true\n" if legacy else ""
    return (
        f'---\nschema: 3\ntype: plan\nid: {PLAN}\ntitle: "t"\nstatus: {status}\n'
        f"{flag}{extra}---\n# {PLAN}\n"
    )


def exec_text(status="frozen", rules=True, decision='"O2O-DEC-0009"', sha=None):
    profile = read_profile_note(PROFILE_FIXTURE).profile
    body = EXEC_BODY if rules else "# 执行方案\n\n无。\n"
    lines = [
        "schema: 3",
        "type: exec-plan",
        f"id: {PLAN}-S1",
        f'plan: "[[{PLAN_STEM}]]"',
        'stage: "S1"',
        f"status: {status}",
    ]
    if decision:
        lines.append(f"rules_decision: {decision}")
    if status != "draft":
        digest = sha or rules_sha256(body, profile) or "0" * 64
        lines += [f"rules_sha256: {digest}", "frozen_on: 2026-10-03"]
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def result_text(status="confirmed", rid="RES-PLAN-910A", extra=""):
    return (
        f'---\nschema: 3\ntype: result\nid: {rid}\nplan: "[[{PLAN_STEM}]]"\n'
        f"status: {status}\nevidence: []\n{extra}---\n# {rid}\n"
    )


def ev_text(status):
    return (
        f'---\nschema: 3\ntype: evidence\nplan: "[[{PLAN_STEM}]]"\nstatus: {status}\n'
        "figures: []\n---\n# EV\n\n## 用户看图标签\n\n| 臂 | 跌落 |\n|---|---|\n"
        f"| a | {'跌落' if status == 'labelled' else ''} |\n"
    )


def advance(to, *args, plan=PLAN):
    return run("plan", "advance", plan, "--to", to, *args, "--apply")


def gate_codes(body):
    assert body["_exit"] == 1 and body["error"]["code"] == "GATE_FAILED", body
    return [g["code"] for g in body["error"]["details"]["gates"]]


def set_plan(proj, status, **kw):
    put(proj, f"plans/{PLAN_STEM}.md", plan_text(status, **kw))


# ---- plan: draft -> approved ----------------------------------------------------


def test_plan_draft_to_approved(world):
    root, proj = world
    assert gate_codes(advance("approved", "--decision", "10")) == ["DEC.NOT_CONFIRMED"]
    assert advance("approved")["_exit"] == 2  # no decision named at all
    assert advance("approved", "--decision", "404")["_exit"] == 3
    ok = advance("approved", "--decision", "9")
    assert ok["_exit"] == 0
    assert 'approved_decision: "O2O-DEC-0009"' in read(
        proj / "plans" / f"{PLAN_STEM}.md"
    )


# ---- plan: approved -> running -----------------------------------------------


def test_plan_approved_to_running(world):
    root, proj = world
    set_plan(proj, "approved", approved_decision='"O2O-DEC-0009"')
    assert gate_codes(advance("running")) == ["LIFE.PLAN_NO_FROZEN_EXEC"]
    put(proj, "ops/exec.md", exec_text("draft"))
    assert gate_codes(advance("running")) == ["LIFE.PLAN_NO_FROZEN_EXEC"]
    put(proj, "ops/exec.md", exec_text("frozen"))
    assert advance("running")["_exit"] == 0


def test_legacy_plans_skip_the_frozen_exec_gate(world):
    root, proj = world
    set_plan(proj, "approved", legacy=True, approved_decision='"O2O-DEC-0009"')
    assert advance("running")["_exit"] == 0


# ---- plan: running -> read-out -----------------------------------------------


def test_plan_running_to_read_out(world):
    root, proj = world
    set_plan(proj, "running", approved_decision='"O2O-DEC-0009"')
    put(proj, "ops/exec.md", exec_text("running"))
    assert gate_codes(advance("read-out")) == ["LIFE.PLAN_RES_COUNT"]
    put(proj, "results/r1.md", result_text("first-read"))
    put(proj, "evidence/e1.md", ev_text("rendered"))
    assert gate_codes(advance("read-out")) == ["LIFE.PLAN_EV_UNLABELLED"]
    put(proj, "results/r2.md", result_text("first-read", "RES-PLAN-910A-b"))
    assert gate_codes(advance("read-out")) == [
        "LIFE.RES_MULTIPLE_ACTIVE",
        "LIFE.PLAN_RES_COUNT",
        "LIFE.PLAN_EV_UNLABELLED",
    ]
    (proj / "results" / "r2.md").unlink()
    put(proj, "evidence/e1.md", ev_text("labelled"))
    assert advance("read-out")["_exit"] == 0


def test_legacy_plans_skip_the_labelled_evidence_gate(world):
    root, proj = world
    set_plan(proj, "running", legacy=True, approved_decision='"O2O-DEC-0009"')
    put(proj, "results/r1.md", result_text("first-read"))
    put(proj, "evidence/e1.md", ev_text("rendered"))
    assert advance("read-out")["_exit"] == 0


# ---- plan: read-out -> confirmed ---------------------------------------------


def test_plan_read_out_to_confirmed(world):
    root, proj = world
    set_plan(proj, "read-out", approved_decision='"O2O-DEC-0009"')
    put(proj, "ops/exec.md", exec_text("done"))
    assert gate_codes(advance("confirmed")) == [
        "LIFE.PLAN_RES_COUNT",
        "LIFE.PLAN_RESULT_MISSING",
    ]
    put(proj, "results/r1.md", result_text("first-read"))
    assert gate_codes(advance("confirmed")) == ["LIFE.PLAN_RESULT_NOT_CONFIRMED"]
    put(proj, "results/r1.md", result_text("confirmed"))
    assert advance("confirmed")["_exit"] == 0
    assert 'result: "[[r1]]"' in read(proj / "plans" / f"{PLAN_STEM}.md")


def test_legacy_plan_may_be_confirmed_while_the_result_is_first_read(world):
    root, proj = world
    set_plan(proj, "read-out", legacy=True, approved_decision='"O2O-DEC-0009"')
    put(proj, "results/r1.md", result_text("first-read"))
    assert advance("confirmed")["_exit"] == 0  # WARN, not FAIL, for legacy


# ---- plan: confirmed -> closed -----------------------------------------------


def test_plan_confirmed_to_closed(world):
    root, proj = world
    set_plan(
        proj,
        "confirmed",
        approved_decision='"O2O-DEC-0009"',
        result='"[[r1]]"',
        legacy=False,
    )
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "results/r1.md", result_text("confirmed"))
    assert gate_codes(advance("closed", "--decision", "10")) == ["DEC.NOT_CONFIRMED"]
    assert advance("closed", "--decision", "9")["_exit"] == 0


# ---- plan: any -> superseded / withdrawn -------------------------------------


def test_plan_terminal_states(world):
    root, proj = world
    assert gate_codes(advance("withdrawn", "--decision", "10")) == ["DEC.NOT_CONFIRMED"]
    assert advance("withdrawn")["_exit"] == 2
    assert advance("withdrawn", "--decision", "9")["_exit"] == 0
    set_plan(proj, "draft")
    put(proj, "plans/other.md", plan_text("draft").replace(PLAN, "PLAN-911A"))
    assert advance("superseded", "--decision", "9")["_exit"] == 2  # needs the successor
    ok = advance("superseded", "--decision", "9", "--superseded-by", "PLAN-911A")
    assert ok["_exit"] == 0
    assert 'superseded_by: "[[other]]"' in read(proj / "plans" / f"{PLAN_STEM}.md")


def test_a_plan_only_moves_one_step_forward(world):
    root, proj = world
    skip = advance("running")
    assert gate_codes(skip) == ["LIFE.TRANSITION"]
    set_plan(proj, "closed", approved_decision='"O2O-DEC-0009"')
    assert gate_codes(advance("draft")) == ["LIFE.TRANSITION"]
    assert advance("closed")["data"]["changes"] == []  # already there: a no-op


# ---- plan close: the user's shortcut ------------------------------------------

QUOTE = "用户在对话里说：这个计划到此为止"


def close(*args, plan=PLAN, reason="做完了", quote=QUOTE):
    extra = ["--authorization", quote] if quote else []
    return run("plan", "close", plan, "--reason", reason, *extra, *args, "--apply")


def plan_findings(plan=PLAN):
    """(level by code, FAIL codes) of the lifecycle and decision findings."""
    body = run("check", "--plan", plan)["data"]
    found = [*body["findings"], *body["newCodes"]]
    ours = [f for f in found if f["code"].startswith(("LIFE.", "DEC."))]
    fails = [f["code"] for f in ours if f["level"] == "FAIL"]
    return {f["code"]: f["level"] for f in ours}, fails


def confirmed_plan(proj, result="confirmed"):
    set_plan(proj, "confirmed", approved_decision='"O2O-DEC-0009"', result='"[[r1]]"')
    put(proj, "ops/exec.md", exec_text("done"))
    put(proj, "results/r1.md", result_text(result))


def test_plan_close_records_a_confirmed_decision_and_closes_the_plan(world):
    root, proj = world
    confirmed_plan(proj)
    done = close()
    assert done["_exit"] == 0 and done["data"]["decision"] == "O2O-DEC-0011"
    text = read(proj / "plans" / f"{PLAN_STEM}.md")
    assert "status: closed" in text and 'closed_decision: "O2O-DEC-0011"' in text
    assert "forced_close" not in text
    row = stored_row(11)
    assert row["status"] == "confirmed" and row["source"] == "chat"
    assert row["confirmed_by"] == "agent-for-user" and row["authorization"] == QUOTE
    assert (row["plans"], row["reason"]) == ([PLAN], "做完了")
    assert row["targets"] == [f"[[{PLAN_STEM}]]"] and row["decided"]
    export = read(proj / "ops" / "decisions.jsonl")
    assert '"id": "O2O-DEC-0011"' in export
    assert plan_findings()[1] == []
    again = close()
    assert again["_exit"] == 0 and again["data"]["changes"] == []  # already closed


def test_plan_close_by_an_agent_needs_the_users_words(world):
    root, proj = world
    confirmed_plan(proj)
    before = read(proj / "plans" / f"{PLAN_STEM}.md")
    refused = close(quote=None)
    assert refused["_exit"] == 7 and refused["error"]["code"] == "USER_ONLY"
    assert close(quote="  ")["_exit"] == 7
    assert read(proj / "plans" / f"{PLAN_STEM}.md") == before
    assert read_rows("off2on") is None


def test_plan_close_without_force_needs_the_closed_gates(world):
    root, proj = world
    set_plan(proj, "running", approved_decision='"O2O-DEC-0009"')
    blocked = close()
    assert gate_codes(blocked)[0] == "LIFE.TRANSITION"
    assert "--force" in blocked["error"]["remediation"]
    confirmed_plan(proj, result="first-read")
    assert gate_codes(close()) == ["LIFE.PLAN_RESULT_NOT_CONFIRMED"]
    assert "status: confirmed" in read(proj / "plans" / f"{PLAN_STEM}.md")
    assert read_rows("off2on") is None  # a refused close records nothing


def test_forced_close_skips_the_gates_and_check_keeps_warnings(world):
    root, proj = world
    set_plan(proj, "draft")
    done = close("--force", reason="用户决定不做了")
    assert done["_exit"] == 0 and done["data"]["forced"] is True
    assert "LIFE.TRANSITION" in {g["code"] for g in done["data"]["waivedGates"]}
    text = read(proj / "plans" / f"{PLAN_STEM}.md")
    assert "status: closed" in text and "forced_close: true" in text
    assert 'closed_decision: "O2O-DEC-0011"' in text
    assert stored_row(11)["reason"] == "用户决定不做了"
    by_code, fails = plan_findings()
    assert by_code["LIFE.PLAN_FORCED_CLOSE"] == "INFO"
    assert by_code["LIFE.PLAN_RES_COUNT"] == "WARN"  # a missing final RES stays visible
    assert by_code["LIFE.PLAN_RESULT_MISSING"] == "WARN"
    assert by_code["LIFE.PLAN_NO_APPROVAL"] == "WARN"
    assert fails == []
    assert close("--force")["data"]["changes"] == []


def test_forced_close_from_a_confirmed_plan_with_an_unconfirmed_result(world):
    root, proj = world
    confirmed_plan(proj, result="first-read")
    assert close("--force")["_exit"] == 0
    by_code, fails = plan_findings()
    assert by_code["LIFE.PLAN_RESULT_NOT_CONFIRMED"] == "WARN" and fails == []


def test_plan_close_dry_run_and_validation(world):
    root, proj = world
    set_plan(proj, "draft")
    before = read(proj / "plans" / f"{PLAN_STEM}.md")
    dry = run(
        "plan", "close", PLAN, "--reason", "r", "--force", "--authorization", QUOTE
    )
    assert dry["_exit"] == 4 and dry["data"]["dryRun"] is True
    ops = [c["op"] for c in dry["data"]["changes"]]
    assert ops == ["decision-new", "set-key", "export"]
    assert "+forced_close: true" in dry["data"]["changes"][1]["unifiedDiff"]
    assert read(proj / "plans" / f"{PLAN_STEM}.md") == before
    assert read_rows("off2on") is None
    blank = run("plan", "close", PLAN, "--reason", " ", "--authorization", QUOTE)
    assert blank["_exit"] == 2 and blank["error"]["code"] == "USAGE_EMPTY_TEXT"
    set_plan(proj, "superseded")
    assert gate_codes(close("--force")) == ["LIFE.TRANSITION"]
    assert run("plan", "close", "PLAN-404", "--reason", "r", "--authorization", QUOTE)[
        "_exit"
    ] == 3


def test_plan_close_wording_comes_from_the_profile(world):
    root, proj = world
    edit_profile(
        proj,
        '  title_delimiters: "：。；"',
        '  title_delimiters: "：。；"\n  close_title: "关闭 {plan}"\n'
        '  close_decided: "关闭 {plan}。"',
    )
    confirmed_plan(proj)
    assert close()["_exit"] == 0
    row = stored_row(11)
    assert row["title"] == f"关闭 {PLAN}" and row["decided"] == f"关闭 {PLAN}。"


# ---- exec-plan: draft -> frozen ----------------------------------------------


def freeze(*args):
    return run("exec", "freeze", f"{PLAN}-S1", *args, "--apply")


def test_exec_draft_to_frozen(world):
    root, proj = world
    put(proj, "ops/exec.md", exec_text("draft", rules=False, decision=None))
    assert gate_codes(freeze("--rules-decision", "9")) == ["EXEC.NO_RULES_SECTION"]
    put(proj, "ops/exec.md", exec_text("draft", decision=None))
    assert gate_codes(freeze()) == ["LIFE.EXEC_NO_RULES_DECISION"]
    assert gate_codes(freeze("--rules-decision", "10")) == ["DEC.NOT_CONFIRMED"]
    ok = freeze("--rules-decision", "9")
    assert ok["_exit"] == 0 and ok["data"]["matchesPrevious"] is False
    text = read(proj / "ops" / "exec.md")
    assert "status: frozen" in text and 'rules_decision: "O2O-DEC-0009"' in text


# ---- exec-plan: frozen (static hash) -----------------------------------------


def test_frozen_rules_changed_needs_a_registered_deviation(world):
    root, proj = world
    put(proj, "ops/exec.md", exec_text("frozen"))
    path = proj / "ops" / "exec.md"
    path.write_text(read(path).replace("A > B", "A ≥ B"), encoding="utf-8")
    assert gate_codes(freeze()) == ["EXEC.RULES_CHANGED"]
    assert gate_codes(freeze("--amend", "D-99")) == ["EXEC.AMEND_UNREGISTERED"]
    ok = freeze("--amend", "D-02")
    assert ok["_exit"] == 0 and 'rules_amended_by: ["D-02"]' in read(path)
    assert freeze("--amend", "D-02")["data"]["changes"] == []


# ---- exec-plan: frozen -> running -> done / aborted --------------------------


@pytest.mark.parametrize(
    ("start", "to", "ok"),
    [
        ("draft", "running", False),
        ("frozen", "done", False),
        ("done", "running", False),
        ("aborted", "done", False),
        ("frozen", "running", True),
        ("running", "done", True),
        ("frozen", "aborted", True),
        ("running", "aborted", True),
    ],
)
def test_exec_transitions(world, start, to, ok):
    root, proj = world
    put(proj, "ops/exec.md", exec_text(start))
    result = run("exec", "advance", f"{PLAN}-S1", "--to", to, "--apply")
    if ok:
        assert result["_exit"] == 0 and f"status: {to}" in read(
            proj / "ops" / "exec.md"
        )
    else:
        assert gate_codes(result) == ["LIFE.EXEC_TRANSITION"]


# ---- evidence: rendered -> labelled ------------------------------------------


def commit(auth="用户原话：标好了"):
    args = ["ev", "label", "commit", "e1"]
    if auth:
        args += ["--authorization", auth]
    return run(*args, "--apply")


def test_evidence_rendered_to_labelled(world):
    root, proj = world
    put(proj, "evidence/e1.md", ev_text("rendered"))
    assert commit(None)["_exit"] == 7  # user-only: no authorization, no label
    assert gate_codes(commit()) == ["EV.LABEL_NOT_FILLED"]
    path = proj / "evidence" / "e1.md"
    path.write_text(read(path).replace("| a |  |", "| a | 跌落 |"), encoding="utf-8")
    assert gate_codes(commit("10")) == ["DEC.NOT_CONFIRMED"]
    ok = commit("9")
    assert ok["_exit"] == 0
    text = read(path)
    assert "status: labelled" in text and "labelled_by: agent-for-user" in text
    assert commit()["data"]["changes"] == []


# ---- result: first-read -> confirmed -----------------------------------------


def confirm_result(*args):
    return run("res", "confirm", "RES-PLAN-910A", *args, "--apply")


def test_result_first_read_to_confirmed(world):
    root, proj = world
    put(proj, "results/r1.md", result_text("first-read"))
    add_decision(proj, 11, CONFIRMED, "[[别的笔记]]")
    add_decision(proj, 12, CONFIRMED, "[[r1]]")
    add_decision(proj, 13, PROPOSED, "[[r1]]")
    assert (
        confirm_result("--decision", "12", "--deviation-impact", "huge")["_exit"] == 2
    )
    asked = confirm_result("--decision", "13", "--deviation-impact", "none")
    assert asked["_exit"] == 7 and asked["error"]["code"] == "USER_ONLY"
    unlinked = confirm_result("--decision", "11", "--deviation-impact", "none")
    assert gate_codes(unlinked) == ["RES.DECISION_NOT_LINKED"]
    ok = confirm_result("--decision", "12", "--deviation-impact", "weak")
    assert ok["_exit"] == 0
    text = read(proj / "results" / "r1.md")
    assert "status: confirmed" in text and 'confirmed_decision: "O2O-DEC-0012"' in text
    assert "deviation_impact: weak" in text
    assert (
        confirm_result("--decision", "12", "--deviation-impact", "weak")["data"][
            "changes"
        ]
        == []
    )


def test_a_withdrawn_result_cannot_be_confirmed(world):
    root, proj = world
    put(proj, "results/r1.md", result_text("withdrawn"))
    add_decision(proj, 12, CONFIRMED, "[[r1]]")
    result = confirm_result("--decision", "12", "--deviation-impact", "none")
    assert gate_codes(result) == ["RES.TRANSITION"]


# ---- decision: proposed -> confirmed / rejected; confirmed -> superseded -----


def stored(number, status, **extra):
    """A decision in the project's store (replacing a row of that number)."""
    reseed(number, status=status, **extra)


def test_decision_confirm_and_reject_are_user_only(world):
    stored(20, "proposed")
    for status in ("confirmed", "rejected"):
        refused = run("decision", "set", "O2O-DEC-0020", "--status", status, "--apply")
        assert refused["_exit"] == 7 and refused["error"]["code"] == "USER_ONLY"
    ok = run(
        "decision",
        "set",
        "O2O-DEC-0020",
        "--status",
        "rejected",
        "--authorization",
        "用户说不行",
        "--apply",
    )
    assert ok["_exit"] == 0
    row = stored_row(20)
    assert row["status"] == "rejected" and row["confirmed_by"] == "agent-for-user"
    assert row["authorization"] == "用户说不行" and row["version"] == 2


def test_decision_confirmed_to_superseded(world):
    stored(20, "confirmed")
    stored(21, "confirmed")
    no_back = run(
        "decision",
        "set",
        "O2O-DEC-0020",
        "--status",
        "superseded",
        "--by",
        "O2O-DEC-0021",
        "--apply",
    )
    assert gate_codes(no_back) == ["DEC.NO_BACKREF"]
    stored(21, "confirmed", supersedes=[20])
    ok = run(
        "decision",
        "set",
        "O2O-DEC-0020",
        "--status",
        "superseded",
        "--by",
        "O2O-DEC-0021",
        "--apply",
    )
    assert ok["_exit"] == 0 and stored_row(20)["superseded_by"] == 21
    # a legacy successor only earns a warning
    stored(22, "confirmed")
    legacy = run(
        "decision",
        "set",
        "O2O-DEC-0022",
        "--status",
        "superseded",
        "--by",
        "8",
        "--apply",
    )
    assert legacy["_exit"] == 0
    assert legacy["warnings"][0]["code"] == "DEC.NO_BACKREF"


def test_a_decision_moves_only_along_the_lifecycle(world):
    stored(20, "rejected")
    again = run(
        "decision", "set", "O2O-DEC-0020", "--status", "confirmed",
        "--authorization", "用户原话", "--apply",
    )  # fmt: skip
    assert gate_codes(again) == ["DEC.TRANSITION"]
    stored(21, "proposed")
    skipped = run(
        "decision", "set", "O2O-DEC-0021", "--status", "superseded", "--by", "8",
        "--apply",
    )  # fmt: skip
    assert gate_codes(skipped) == ["DEC.TRANSITION"]


def stored_row(number):
    return next(r for r in read_rows("off2on")[0] if r["number"] == number)


# ---- static rows (no writer): paired through `check` on the scenario vaults ----

STATIC_ROWS = [
    (
        "plan: draft->approved needs an approval",
        "plan_approved_without_decision",
        {"LIFE.PLAN_NO_APPROVAL"},
    ),
    (
        "plan: confirmed->closed needs a closing decision",
        "plan_closed_without_decision",
        {"LIFE.PLAN_NO_CLOSING"},
    ),
    (
        "result: confirmed needs its decision",
        "res_no_decision",
        {"RES.CONFIRM_NO_DECISION"},
    ),
    (
        "result: confirmed needs deviation_impact",
        "res_no_impact",
        {"RES.NO_IMPACT"},
    ),
    (
        "plan: at most one active RES",
        "two_active_results",
        {"LIFE.RES_MULTIPLE_ACTIVE"},
    ),
    ("exec: frozen rules hash", "frozen_rules_edited", {"EXEC.RULES_CHANGED"}),
    (
        "exec: amendment registered",
        "frozen_rules_amended_unregistered",
        {"EXEC.AMEND_UNREGISTERED"},
    ),
    (
        "evidence: observation after labels",
        "ev_observation_before_label_and_extra_table",
        {"EV.OBSERVATION_BEFORE_LABEL", "EV.EXTRA_TABLES"},
    ),
    ("evidence: appendix twin exists", "ev_no_appendix", {"EV.NO_APPENDIX"}),
    ("evidence: twin points back", "appendix_twin_mismatch", {"EV.TWIN_MISMATCH"}),
    (
        "evidence: figures resolve and are embedded",
        "ev_figure_missing_and_not_embedded",
        {"EV.FIGURE_UNRESOLVED", "EV.FIGURE_NOT_EMBEDDED"},
    ),
    (
        "evidence: label-na has no labels",
        "ev_label_na_but_filled",
        {"EV.LABEL_NA_BUT_FILLED"},
    ),
    (
        "result: superseded_by is a sibling result",
        "superseded_result_invalid",
        {"RES.SUPERSEDED_INVALID"},
    ),
    (
        "result: evidence links resolve",
        "res_evidence_unresolved",
        {"RES.EVIDENCE_UNRESOLVED"},
    ),
    (
        "decision: superseded has a successor",
        "decision_superseded_without_successor",
        {"DEC.SUPERSEDED_NO_SUCCESSOR"},
    ),
    (
        "global: filename uniqueness",
        "casefold_duplicate_stem_and_missing_image",
        {"NAME.DUPLICATE_STEM", "LINK.IMAGE_UNRESOLVED"},
    ),
    (
        "global: reserved characters",
        "reserved_char_filename",
        {"NAME.RESERVED_CHAR", "LINK.UNRESOLVED"},
    ),
    ("global: duplicate ids", "duplicate_plan_id", {"SCHEMA.ID_DUPLICATE"}),
    (
        "global: required-prefix images in images_dir",
        "image_prefix_outside_dir",
        {"LINK.IMAGE_OUTSIDE_DIR"},
    ),
    ("global: links resolve", "unresolved_link_in_code_ignored", {"LINK.UNRESOLVED"}),
]

PASSING_PAIRS = {
    "frozen_rules_edited": "frozen_rules_amended",
    "superseded_result_invalid": "superseded_result_allowed",
}


def check_codes(tmp_path, scenario):
    root, proj = scenario_vault(tmp_path, scenario)
    register(root, proj, "--quiet-window-seconds", "0")
    body = run("check")
    return {f["code"] for f in [*body["data"]["findings"], *body["data"]["newCodes"]]}


@pytest.mark.parametrize(
    ("row", "scenario", "codes"), STATIC_ROWS, ids=lambda v: str(v)[:40]
)
def test_static_gate_rows_fail_in_the_scenario_and_pass_in_the_base(
    tmp_path_factory, row, scenario, codes
):
    assert codes <= SCENARIOS[scenario][1]
    failing = check_codes(tmp_path_factory.mktemp("fail"), scenario)
    assert codes <= failing, (row, failing)
    passing = check_codes(
        tmp_path_factory.mktemp("pass"), PASSING_PAIRS.get(scenario, "base")
    )
    assert not codes & passing, (row, passing)


def test_agent_for_user_authorization_rows_pair_through_check(tmp_path_factory):
    missing = check_codes(
        tmp_path_factory.mktemp("a"), "ev_agent_without_authorization"
    )
    assert "EV.AUTHORIZATION_MISSING" in missing
    assert "EV.AUTHORIZATION_MISSING" not in check_codes(
        tmp_path_factory.mktemp("b"), "ev_agent_authorization_quote"
    )
    unresolved = check_codes(
        tmp_path_factory.mktemp("c"), "ev_agent_authorization_row99"
    )
    assert "EV.AUTHORIZATION_UNRESOLVED" in unresolved
    assert "EV.AUTHORIZATION_UNRESOLVED" not in check_codes(
        tmp_path_factory.mktemp("d"), "ev_agent_authorization_row4"
    )


# ---- schema 2 notes keep numeric decision references -------------------------


def test_schema_2_notes_get_numeric_decision_references(world):
    root, proj = world
    plan = proj / "plans" / f"{PLAN_STEM}.md"
    put(
        proj,
        f"plans/{PLAN_STEM}.md",
        plan_text("draft").replace("schema: 3", "schema: 2"),
    )
    assert advance("approved", "--decision", "O2O-DEC-0009")["_exit"] == 0
    assert "approved_decision: 9\n" in read(
        plan
    )  # not a quoted id: schema 2 is int-only
    ok = run("check", "--code", "SCHEMA.BAD_VALUE")
    assert ok["data"]["findings"] == []
    exec_note = exec_text("draft", decision=None).replace("schema: 3", "schema: 2")
    put(proj, "ops/exec.md", exec_note)
    assert freeze("--rules-decision", "9")["_exit"] == 0
    assert "rules_decision: 9\n" in read(proj / "ops" / "exec.md")
    put(
        proj,
        "results/r1.md",
        result_text("first-read").replace("schema: 3", "schema: 2"),
    )
    add_decision(proj, 12, CONFIRMED, "[[r1]]")
    assert (
        confirm_result("--decision", "12", "--deviation-impact", "none")["_exit"] == 0
    )
    assert "confirmed_decision: 12\n" in read(proj / "results" / "r1.md")
    assert run("check", "--code", "SCHEMA.BAD_VALUE")["data"]["findings"] == []


def test_a_gate_decided_on_stale_text_is_refused_at_write_time(world):
    root, proj = world
    path = proj / "plans" / f"{PLAN_STEM}.md"
    seen = run("plan", "show", PLAN)["data"]["plan"]["sha256"]
    path.write_text(read(path) + "\nhuman edit\n", encoding="utf-8")
    set_mtime(path, 3600)
    stale = advance("approved", "--decision", "9", "--expected-sha256", seen)
    assert stale["_exit"] == 5 and stale["error"]["code"] == "CAS_MISMATCH"
    assert "status: draft" in read(path)


# ---- rows whose command stops earlier on usage: paired through `check` ---------


def finding_codes(*args):
    body = run("check", *args)
    return {f["code"] for f in [*body["data"]["findings"], *body["data"]["newCodes"]]}


def test_terminal_plan_needs_a_recorded_decision(world):
    root, proj = world
    set_plan(proj, "withdrawn")
    assert "LIFE.PLAN_TERMINAL" in finding_codes()
    set_plan(proj, "withdrawn", closed_decision='"O2O-DEC-0009"')
    assert "LIFE.PLAN_TERMINAL" not in finding_codes()
    set_plan(proj, "superseded", closed_decision='"O2O-DEC-0009"')
    assert "LIFE.PLAN_TERMINAL" in finding_codes()  # no superseded_by yet
    put(proj, "plans/other.md", plan_text("draft").replace(PLAN, "PLAN-911A"))
    set_plan(
        proj,
        "superseded",
        closed_decision='"O2O-DEC-0009"',
        superseded_by='"[[other]]"',
    )
    assert "LIFE.PLAN_TERMINAL" not in finding_codes()


def test_labelled_evidence_names_who_labelled_it(world):
    root, proj = world
    put(proj, "evidence/e1.md", ev_text("labelled"))
    assert "EV.LABELLED_BY_MISSING" in finding_codes()
    path = proj / "evidence" / "e1.md"
    path.write_text(
        read(path).replace("status: labelled", "status: labelled\nlabelled_by: user"),
        encoding="utf-8",
    )
    assert "EV.LABELLED_BY_MISSING" not in finding_codes()


def test_an_agent_confirmed_decision_carries_an_authorization(world):
    stored(20, "confirmed", confirmed_by="agent-for-user")
    assert "DEC.AGENT_NO_AUTH" in finding_codes()
    words = "用户原话：同意"
    stored(20, "confirmed", confirmed_by="agent-for-user", authorization=words)
    assert "DEC.AGENT_NO_AUTH" not in finding_codes()
