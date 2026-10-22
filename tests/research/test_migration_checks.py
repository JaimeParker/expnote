"""Legacy plans waiting for their final result: WARN codes and a derived TODO."""

from __future__ import annotations

import pytest
from minivault import RES, build_vault, write
from readkit import data, register, run
from test_checks_schema3 import v3_plan

RESULT_COUNT_CODES = {
    "LIFE.RES_MULTIPLE_ACTIVE",
    "LIFE.PLAN_RES_COUNT",
    "LIFE.PLAN_RESULT_MISSING",
}


def second_card() -> str:
    return RES.replace("id: RES-PLAN-900A", "id: RES-PLAN-900A-2").replace(
        "status: confirmed", "status: first-read"
    )


def build(tmp_path, *, legacy: bool, second: bool = True):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    plan = v3_plan(result=None, legacy='"true"' if legacy else None)
    write(proj / "plans/PLAN-900A--demo.md", plan.replace('"true"', "true"))
    if second:
        write(proj / "results/RES--PLAN-900A 第二张.md", second_card())
    return proj


def levels(plan="PLAN-900A") -> dict[str, str]:
    body = run("check", "--plan", plan)["data"]  # other gates FAIL: exit 1
    found = {}
    for finding in [*body["findings"], *body["newCodes"]]:
        if finding["code"] in RESULT_COUNT_CODES:
            found[finding["code"]] = finding["level"]
    return found


def pending_todos():
    items = data("todo", "list", "--kind", "final-res-pending")["todos"]
    return {t["ref"]: t for t in items}


def test_schema3_plans_that_are_not_legacy_keep_failing(tmp_path):
    build(tmp_path, legacy=False)
    assert levels() == {code: "FAIL" for code in RESULT_COUNT_CODES}
    assert "PLAN-900A" not in pending_todos()  # only legacy plans wait


def test_legacy_plans_report_the_same_codes_as_warn(tmp_path):
    build(tmp_path, legacy=True)
    assert levels() == {code: "WARN" for code in RESULT_COUNT_CODES}
    fails = [
        f["code"]
        for f in run("check", "--plan", "PLAN-900A")["data"]["findings"]
        if f["level"] == "FAIL"
    ]
    assert not RESULT_COUNT_CODES & set(fails)  # the other gates still FAIL


def test_final_res_pending_todo_is_one_per_legacy_plan(tmp_path):
    build(tmp_path, legacy=True)
    todo = pending_todos()["PLAN-900A"]
    assert todo["owner"] == "agent" and todo["plan"] == "PLAN-900A"
    assert "2 active result(s)" in todo["message"]
    assert todo["command"].startswith("expnote new res --plan PLAN-900A")


@pytest.mark.parametrize("legacy", [True, False])
def test_exactly_one_active_result_needs_no_todo(tmp_path, legacy):
    build(tmp_path, legacy=legacy, second=False)
    assert "PLAN-900A" not in pending_todos()
