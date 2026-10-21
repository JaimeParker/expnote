"""P2 fix-ups of the read layer: archived/untyped plans, index stats, doctor."""

from __future__ import annotations

import pytest
from minivault import build_vault, write
from readkit import data, register, run

ARCHIVED_COPY = "---\ntype: plan\nid: PLAN-900A\nstatus: draft\n---\n# 旧稿\n"


@pytest.fixture
def vault(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    return root, proj


def listed(*extra):
    return [(p["id"], p["archived"]) for p in data("plan", "list", *extra)["plans"]]


def test_archived_plan_copies_are_hidden_unless_asked(vault):
    root, proj = vault
    write(proj / "plans/归档/PLAN-900A--旧稿.md", ARCHIVED_COPY)
    assert listed() == [("PLAN-900A", False), ("PLAN-900B", False)]
    assert listed("--include-archived") == [
        ("PLAN-900A", False),
        ("PLAN-900A", True),
        ("PLAN-900B", False),
    ]
    shown = data("plan", "show", "PLAN-900A")["plan"]
    assert shown["path"].endswith("plans/PLAN-900A--demo.md")
    bundle = data("plan", "bundle", "PLAN-900A")["plan"]
    assert bundle["path"] == shown["path"]


def test_a_plan_that_exists_only_in_the_archive_is_still_found(vault):
    root, proj = vault
    write(proj / "plans/归档/PLAN-900Z.md", ARCHIVED_COPY.replace("900A", "900Z"))
    assert ("PLAN-900Z", True) not in listed()
    assert data("plan", "show", "PLAN-900Z")["plan"]["path"].endswith("900Z.md")


def test_two_live_plan_pages_with_one_id_are_ambiguous(vault):
    root, proj = vault
    write(proj / "plans/PLAN-900A--zweite.md", ARCHIVED_COPY)
    for command in (("plan", "show"), ("plan", "bundle")):
        body = run(*command, "PLAN-900A")
        assert body["_exit"] == 3 and body["error"]["code"] == "AMBIGUOUS_ID"
        candidates = body["error"]["details"]["candidates"]
        assert len(candidates) == 2 and all(c.endswith(".md") for c in candidates)
    assert [p for p, _ in listed()].count("PLAN-900A") == 2


def test_type_plan_notes_without_a_plan_id_are_left_out_and_reported(vault):
    root, proj = vault
    write(proj / "ops/验收表.md", "---\nid: 验收表\ntype: plan\n---\n# 验收\n")
    write(proj / "ops/无编号.md", "---\ntype: plan\n---\n# 无编号\n")
    write(proj / "templates/PLAN-xxx.md", "---\ntype: plan\nid: PLAN-xxx\n---\n")
    assert [p for p, _ in listed()] == ["PLAN-900A", "PLAN-900B"]
    missing = run("plan", "show", "验收表")
    assert missing["_exit"] == 3 and missing["error"]["code"] == "PLAN_NOT_FOUND"
    checks = {c["id"]: c for c in run("doctor")["data"]["checks"]}
    odd = checks["untyped-plans"]
    assert odd["status"] == "warn" and odd["remediation"]
    assert odd["items"] == [
        "10 Projects/Demo/ops/无编号.md",
        "10 Projects/Demo/ops/验收表.md",
    ]


def test_project_show_reports_index_stats(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    before = data("project", "show", "off2on")["index"]
    assert before["built"] is False and before["path"].endswith("index.sqlite")
    run("reindex")
    index = data("project", "show", "off2on")["index"]
    assert index["built"] is True and index["files"] > 5 and index["notes"] >= 5
    assert index["linksResolved"] + index["linksUnresolved"] > 0
    assert index["builtAt"] and index["conflictFiles"] == 0


def test_doctor_flags_generated_notes_named_by_the_profile(vault):
    root, proj = vault
    checks = {c["id"]: c for c in run("doctor")["data"]["checks"]}
    assert checks["generated-notes"]["status"] == "ok"
    assert checks["untyped-plans"]["status"] == "ok"
    write(proj / "决策记录--研究视图.md", "# 视图\n")
    checks = {c["id"]: c for c in run("doctor")["data"]["checks"]}
    assert checks["generated-notes"]["status"] == "warn"
    assert checks["generated-notes"]["items"] == [
        "10 Projects/Demo/决策记录--研究视图.md"
    ]
