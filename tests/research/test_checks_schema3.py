"""Schema 3: decision files, plan terminal states, header-named deviation columns."""

from __future__ import annotations

import dataclasses

import pytest
from decisionkit import reseed, seed
from minivault import (
    DECISION_HEADER,
    DECISIONS_PATH,
    DEVIATIONS_PATH,
    build_vault,
    decision_rows,
    make_context,
    write,
)

from expnote.research.checks import run_checks
from expnote.research.snapshot import scan_vault


def v3_plan(**overrides) -> str:
    fields = {
        "schema": "3",
        "type": "plan",
        "id": "PLAN-900A",
        "title": '"PLAN-900A"',
        "status": "closed",
        "approved_decision": '"O2O-DEC-0125"',
        "closed_decision": '"O2O-DEC-0126"',
        "result": '"[[RES--PLAN-900A 判读]]"',
    }
    fields.update(overrides)
    return (
        "---\n"
        + "".join(f"{k}: {v}\n" for k, v in fields.items() if v is not None)
        + "---\n# 方案\n"
    )


@pytest.fixture
def vault(tmp_path):
    root, proj = build_vault(tmp_path)
    # schema-3 world: store decisions (125, 126) alongside the legacy table (1..8)
    write(proj / "plans/PLAN-900A--demo.md", v3_plan())
    seed(125, 126)
    return root, proj


def check(root, proj, plan_id=None):
    return run_checks(scan_vault(make_context(root, proj)), plan_id)


def found(result, code):
    return [f for f in result.findings if f.code == code]


def test_schema3_plan_with_stored_decisions_has_no_fail(vault):
    root, proj = vault
    result = check(root, proj)
    assert [f for f in result.findings if f.level == "FAIL"] == []


def test_decision_ids_in_every_accepted_form(vault):
    root, proj = vault
    for form in ('"O2O-DEC-0125"', '"#125"', '"125"'):
        write(proj / "plans/PLAN-900A--demo.md", v3_plan(approved_decision=form))
        assert not [f for f in check(root, proj).findings if f.level == "FAIL"], form


def test_plan_approval_by_unknown_or_proposed_decision(vault):
    root, proj = vault
    write(
        proj / "plans/PLAN-900A--demo.md", v3_plan(approved_decision='"O2O-DEC-0199"')
    )
    assert found(check(root, proj), "LIFE.PLAN_NO_APPROVAL")
    write(proj / "plans/PLAN-900A--demo.md", v3_plan())
    reseed(125, status="proposed")
    assert found(check(root, proj), "DEC.NOT_CONFIRMED")


def test_store_rows_win_over_a_legacy_table_row_of_the_same_number(vault):
    root, proj = vault
    write(
        proj / DECISIONS_PATH,
        DECISION_HEADER
        + decision_rows(
            (125, "2026-10-20", "表里也有 125", "x", "y", "confirmed", "研究")
        ),
    )
    snap = scan_vault(make_context(root, proj))
    assert snap.decisions[125].source == "db"
    assert not found(check(root, proj), "DEC.NUMBER_DUPLICATE")


def test_superseded_decision_needs_backreference(vault):
    root, proj = vault
    reseed(125, status="superseded", superseded_by=126)
    result = check(root, proj)
    backref = found(result, "DEC.NO_BACKREF")
    assert backref and backref[0].level == "WARN"  # successor is past the cutover
    reseed(126, supersedes=[125])
    assert not found(check(root, proj), "DEC.NO_BACKREF")


def test_superseded_decision_without_successor_is_fail(vault):
    root, proj = vault
    reseed(125, status="superseded", superseded_by=190)
    assert found(check(root, proj), "DEC.SUPERSEDED_NO_SUCCESSOR")


def test_stored_decision_gates(vault):
    root, proj = vault
    reseed(125, reason="", options="")
    result = check(root, proj)
    assert found(result, "DEC.SECTION_MISSING")
    assert found(result, "DEC.NO_OPTIONS")[0].level == "WARN"  # research layer
    reseed(125, layer="工程", options="")
    assert found(check(root, proj), "DEC.NO_OPTIONS")[0].level == "INFO"


def test_agent_for_user_confirmation(vault):
    root, proj = vault
    reseed(125, confirmed_by="agent-for-user")
    assert found(check(root, proj), "DEC.AGENT_NO_AUTH")[0].level == "FAIL"
    reseed(125, confirmed_by="agent-for-user", authorization="ok")
    assert found(check(root, proj), "DEC.AUTH_WEAK")
    quote = "用户在对话中明确说：同意第一步执行"
    reseed(125, confirmed_by="agent-for-user", authorization=quote)
    result = check(root, proj)
    assert not found(result, "DEC.AUTH_WEAK") and not found(result, "DEC.AGENT_NO_AUTH")


def test_stored_decision_layer_must_be_in_the_vocabulary(vault):
    root, proj = vault
    reseed(125, layer="其他")
    assert any(
        "layer" in f.message for f in found(check(root, proj), "SCHEMA.BAD_VALUE")
    )


def test_confirmed_decision_needs_who_and_when(vault):
    root, proj = vault
    reseed(125, confirmed_by=None, confirmed_on=None)
    fields = {f.data["field"] for f in found(check(root, proj), "SCHEMA.MISSING_FIELD")}
    assert fields == {"confirmed_by", "confirmed_on"}


def test_legacy_stored_decision_is_exempt_from_section_rules(vault):
    root, proj = vault
    seed(5, legacy=True, reason="", options="", plans=[], status_raw="有效")
    write(proj / DECISIONS_PATH, DECISION_HEADER)
    result = check(root, proj)
    assert not found(result, "DEC.SECTION_MISSING")


def test_a_wikilink_to_a_stored_decision_id_resolves(vault):
    root, proj = vault
    write(proj / "别的笔记.md", "see [[O2O-DEC-0125]] and [[O2O-DEC-0199]]")
    messages = [f.message for f in found(check(root, proj), "LINK.UNRESOLVED")]
    assert any("O2O-DEC-0199" in m for m in messages)
    assert not any("O2O-DEC-0125" in m for m in messages)


def test_plan_terminal_states(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900A--demo.md", v3_plan(status="superseded", result=None))
    assert found(check(root, proj), "LIFE.PLAN_TERMINAL")
    write(
        proj / "plans/PLAN-900A--demo.md",
        v3_plan(
            status="superseded", result=None, superseded_by='"[[PLAN-900B--other]]"'
        ),
    )
    assert not found(check(root, proj), "LIFE.PLAN_TERMINAL")
    write(proj / "plans/PLAN-900A--demo.md", v3_plan(status="withdrawn", result=None))
    assert not found(check(root, proj), "LIFE.PLAN_TERMINAL")


def test_schema3_legacy_plan_demotes_unconfirmed_result_to_warn(vault):
    root, proj = vault
    res = proj / "results/RES--PLAN-900A 判读.md"
    res.write_text(
        res.read_text(encoding="utf-8").replace(
            "status: confirmed", "status: first-read"
        ),
        encoding="utf-8",
    )
    failing = found(check(root, proj), "LIFE.PLAN_RESULT_NOT_CONFIRMED")
    assert failing and failing[0].level == "FAIL"
    write(proj / "plans/PLAN-900A--demo.md", v3_plan(legacy="true"))
    assert found(check(root, proj), "LIFE.PLAN_RESULT_NOT_CONFIRMED")[0].level == "WARN"


def test_schema3_legacy_result_needs_no_decision(vault):
    root, proj = vault
    text = (
        '---\nschema: 3\ntype: result\nid: RES-X\nplan: "[[PLAN-900A--demo]]"\n'
        'status: confirmed\nevidence: ["[[EV--PLAN-900A 演示]]"]\n'
        "legacy: true\n---\nbody\n"
    )
    write(proj / "results/RES--旧.md", text)
    result = check(root, proj)
    assert found(result, "RES.LEGACY_UNCONFIRMED")
    assert not found(result, "RES.CONFIRM_NO_DECISION")


def test_schema2_result_keeps_strict_rules_even_with_legacy_key(vault):
    root, proj = vault
    res = proj / "results/RES--PLAN-900A 判读.md"
    res.write_text(
        res.read_text(encoding="utf-8").replace(
            "confirmed_decision: 4\n", "legacy: true\n"
        ),
        encoding="utf-8",
    )
    assert found(check(root, proj), "RES.CONFIRM_NO_DECISION")


def test_schema2_rejects_prefixed_decision_ids(vault):
    root, proj = vault
    plan = v3_plan(schema="2", approved_decision='"O2O-DEC-0125"')
    write(proj / "plans/PLAN-900A--demo.md", plan)
    result = check(root, proj)
    assert found(result, "SCHEMA.BAD_VALUE") and found(result, "LIFE.PLAN_NO_APPROVAL")


def test_deviation_columns_are_matched_by_header_name(vault):
    root, proj = vault
    write(
        proj / DEVIATIONS_PATH,
        "| 发现时点 | 状态 | 编号 | 日期 | 实验 | 偏离内容 | 对结论有效性的影响 "
        "| 处置 | 相关实验卡 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| 见数据后 | closed | D-01 | 2026-10-03 | PLAN-900A S1 | x | 无 | 已关闭 "
        "| y |\n"
        "| 随便 | open | D-02 | 2026-10-04 | PLAN-900A S1 | x | 弱 | 维持 | z |\n",
    )
    snapshot = scan_vault(make_context(root, proj))
    assert snapshot.deviations["D-01"].status == "closed"
    assert snapshot.deviations["D-02"].found == "随便"
    result = run_checks(snapshot, "PLAN-900A")
    assert [f.where for f in found(result, "DEV.FOUND_VOCAB")] == ["D-02"]
    assert [f.where for f in found(result, "DEV.OPEN_FOR_PLAN")] == ["D-02"]


def test_decision_table_columns_matched_by_header_name(vault):
    root, proj = vault
    write(
        proj / DECISIONS_PATH,
        "| 日期 | # | 决定 | 理由 | 落到 | 层 | 状态 |\n|---|---|---|---|---|---|---|\n"
        "| 2026-10-01 | 1 | a | b | [[别的笔记]] | 研究 | 有效 |\n",
    )
    snapshot = scan_vault(make_context(root, proj))
    assert (
        snapshot.decisions[1].status == "confirmed"
        and snapshot.decisions[1].layer == "研究"
    )


def test_decision_table_missing_header_column_is_one_finding(vault):
    root, proj = vault
    write(proj / DECISIONS_PATH, "| # | 日期 |\n|---|---|\n| 1 | x |\n")
    result = check(root, proj)
    assert [f.code for f in result.findings if f.code == "DEC.TABLE_COLUMNS"] == [
        "DEC.TABLE_COLUMNS"
    ]


def test_escaped_pipe_alias_in_decision_table_keeps_cell_count(vault):
    root, proj = vault
    rows = decision_rows(
        (
            9,
            "2026-10-01",
            "see [[PLAN-900A--demo\\|plan]]",
            "x",
            "[[别的笔记]]",
            "有效",
            "研究",
        )
    )
    write(proj / DECISIONS_PATH, DECISION_HEADER + rows)
    assert not found(check(root, proj), "DEC.TABLE_COLUMNS")


def test_status_tail_level_follows_profile(vault):
    root, proj = vault
    plan = proj / "plans/PLAN-900A--demo.md"
    plan.write_text(
        plan.read_text(encoding="utf-8") + "\n<!-- vault:status:start -->\n",
        encoding="utf-8",
    )
    context = make_context(root, proj)
    warned = run_checks(scan_vault(context))
    assert found(warned, "GEN.STATUS_TAIL_PRESENT")[0].level == "WARN"
    strict = dataclasses.replace(context.profile, forbidden_marker_level="fail")
    context = dataclasses.replace(context, profile=strict)
    failed = run_checks(scan_vault(context))
    assert found(failed, "GEN.STATUS_TAIL_PRESENT")[0].level == "FAIL"
