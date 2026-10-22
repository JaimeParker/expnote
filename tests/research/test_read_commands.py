"""Read commands over a mini vault: search, lists, shows, bundle, guide."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from decisionkit import seed
from minivault import (
    EV_PATH,
    EXEC_PATH,
    PLAN_PATH,
    RES_PATH,
    build_vault,
    make_workspace,
    write,
)
from readkit import data, invoke, payload, register, run
from test_checks_schema3 import v3_plan

from expnote.cli import _AGENT_GUIDE
from expnote.research.bundle import SECTIONS

THEORY = (
    '---\ntype: theory\nplan: "[[PLAN-900A--demo]]"\nstatus: current\n'
    "description: 理论笔记说明。\n---\n# 理论\n\n正文。\n"
)
LOG = (
    "---\ntype: execution-log\n---\n# 日志\n\n"
    "记录了 [[PLAN-900A--demo]] 的执行。第二句。\n"
)


@pytest.fixture
def world(tmp_path):
    root, proj = build_vault(tmp_path)
    write(proj / "theory/理论.md", THEORY)
    write(proj / "ops/执行日志.md", LOG)
    make_workspace(
        "w1",
        runs=[
            {
                "id": "0012345",
                "topic": "主题",
                "status": "finished",
                "result": "终点 0.1\n第二行",
            }
        ],
        docs=[
            {
                "id": "doc00001",
                "title": "分析一",
                "body": "# 分析\n\n结论一句。",
                "runs": ["0012345"],
            }
        ],
    )
    register(root, proj, "--workspace", "w1")
    return root, proj


def test_guide_agent_is_unchanged_and_research_is_an_envelope():
    agent = payload(invoke("guide", "agent", "--json"))
    assert agent == _AGENT_GUIDE
    body = run("guide", "research")
    assert body["ok"] and body["command"] == "guide research"
    assert body["data"]["topic"] == "research" and body["data"]["exitCodes"]["7"]
    text = invoke("guide", "research").stdout
    assert "expnote next" in text and "not implemented yet" not in text
    assert text.index("expnote project init") < text.index("expnote new plan")
    runs = [s["run"] for w in body["data"]["workflows"].values() for s in w]
    assert any(r.startswith("expnote project init") for r in runs)
    assert invoke("guide", "nonsense").exit_code != 0


def test_plan_list_and_show(world):
    plans = data("plan", "list")["plans"]
    by_id = {p["id"]: p for p in plans}
    assert by_id["PLAN-900A"]["status"] == "closed"
    assert (
        by_id["PLAN-900A"]["evCount"] == 1
        and by_id["PLAN-900A"]["resStatus"] == "confirmed"
    )
    assert by_id["PLAN-900A"]["decisionCount"] >= 4
    assert [p["id"] for p in data("plan", "list", "--status", "closed")["plans"]] == [
        "PLAN-900A"
    ]
    assert data("plan", "list", "--legacy")["plans"] != []
    shown = data("plan", "show", "PLAN-900A")
    assert shown["plan"]["sha256"] and shown["plan"]["body"] is None
    assert shown["frontmatter"]["status"] == "closed"
    assert shown["gates"]["current"] == "closed"
    with_body = data("plan", "show", "PLAN-900A", "--include-body")
    assert "人写正文" in with_body["plan"]["body"]
    missing = run("plan", "show", "PLAN-NOPE")
    assert missing["_exit"] == 3 and missing["error"]["code"] == "PLAN_NOT_FOUND"


def test_fields_and_cursor_on_lists(world):
    only = data("decision", "list", "--fields", "id,status")["decisions"]
    assert set(only[0]) == {"id", "status"}
    first = run("decision", "list", "--limit", "3", "--fields", "number")
    assert [d["number"] for d in first["data"]["decisions"]] == [1, 2, 3]
    cursor = first["meta"]["nextCursor"]
    second = run(
        "decision", "list", "--limit", "3", "--cursor", cursor, "--fields", "number"
    )
    assert [d["number"] for d in second["data"]["decisions"]] == [4, 5, 6]
    bad = run("decision", "list", "--fields", "nope")
    assert bad["_exit"] == 2 and bad["error"]["code"] == "USAGE_BAD_FIELDS"


def test_decisions_from_the_legacy_table(world):
    rows = data("decision", "list")["decisions"]
    assert [d["number"] for d in rows] == list(range(1, 9))
    assert {d["source"] for d in rows} == {"table"}
    approved = next(d for d in rows if d["number"] == 2)
    assert approved["id"] == "O2O-DEC-0002" and approved["status"] == "confirmed"
    assert approved["plans"] == ["PLAN-900A"] and approved["title"].startswith(
        "PLAN-900A 批准"
    )
    old = next(d for d in rows if d["number"] == 7)
    assert old["status"] == "superseded" and old["supersededBy"] == "O2O-DEC-0008"
    assert [
        d["number"]
        for d in data("decision", "list", "--plan", "PLAN-900A")["decisions"]
    ] == [2, 3, 4, 5]
    assert [
        d["number"] for d in data("decision", "list", "--layer", "工程")["decisions"]
    ] == [6]
    assert [
        d["number"]
        for d in data("decision", "list", "--since", "2026-10-06")["decisions"]
    ] == [7, 8]
    shown = data("decision", "show", "#2", "--include-body")["decision"]
    assert shown["number"] == 2 and "用户原话" in shown["body"]
    assert data("decision", "show", "O2O-DEC-0003")["decision"]["number"] == 3
    assert run("decision", "show", "999")["_exit"] == 3
    assert run("decision", "show", "abc")["_exit"] == 2


def test_store_decisions_join_the_table_and_show_their_sections(world):
    seed(125, status="proposed", plans=["PLAN-900B"], lands="落到 [[别的笔记]]")
    rows = data("decision", "list")["decisions"]
    assert [d["number"] for d in rows][-2:] == [8, 125]
    new = rows[-1]
    assert (
        new["source"] == "db"
        and new["status"] == "proposed"
        and new["plans"] == ["PLAN-900B"]
        and new["version"] == 1
    )
    detail = data("decision", "show", "O2O-DEC-0125", "--include-body")["decision"]
    assert detail["sections"]["reason"] == "因为 B。"
    assert detail["history"][0]["action"] == "import"
    assert "做 A。" in detail["body"] and "因为 B。" in detail["body"]
    only_store = data("decision", "list", "--status", "proposed")["decisions"]
    assert [d["number"] for d in only_store] == [125]


def test_decision_show_lists_who_cites_it(world):
    root, proj = world
    write(proj / "引用.md", "见 [[决策记录]] #2、#3")
    refs = data("decision", "show", "2")["refsIn"]
    stems = {(r["stem"], r["form"]) for r in refs}
    assert ("引用", "hub") in stems and ("PLAN-900A--demo", "field") in stems


def test_exec_ev_res_deviation_lists(world):
    execs = data("exec", "list")["execPlans"]
    assert [(e["id"], e["frozen"], e["rulesShaMatches"]) for e in execs] == [
        ("PLAN-900A-S1", True, True)
    ]
    shown = data("exec", "show", "PLAN-900A-S1")["exec"]
    assert (
        shown["rulesSha256Stored"] == shown["rulesSha256Computed"]
        and shown["hasRulesSection"]
    )
    assert (
        data("exec", "show", "执行方案--PLAN-900A S1")["exec"]["id"] == "PLAN-900A-S1"
    )
    assert run("exec", "show", "nope")["_exit"] == 3
    evs = data("ev", "list")["ev"]
    assert [(e["plan"], e["status"], e["labelState"]) for e in evs] == [
        ("PLAN-900A", "labelled", "filled")
    ]
    assert evs[0]["appendix"]["stem"].startswith("附表--EV") and evs[0]["figures"] == [
        "o2o_demo.png"
    ]
    assert data("ev", "list", "--label-state", "empty")["ev"] == []
    results = data("res", "list")["results"]
    assert [
        (r["id"], r["status"], r["confirmedDecision"], r["deviationImpact"])
        for r in results
    ] == [("RES-PLAN-900A", "confirmed", "O2O-DEC-0004", "weak")]
    assert results[0]["evidence"] == ["EV--PLAN-900A 演示"]
    devs = data("deviation", "list")["deviations"]
    assert [(d["id"], d["status"]) for d in devs] == [
        ("D-01", "closed"),
        ("D-02", "open"),
    ]
    assert [
        d["id"] for d in data("deviation", "list", "--status", "open")["deviations"]
    ] == ["D-02"]
    one = data("deviation", "show", "D-02")["deviation"]
    assert one["plans"] == ["PLAN-900A"] and one["disposition"] == "维持"
    assert run("deviation", "show", "D-99")["_exit"] == 3
    assert run("ev", "list", "--plan", "PLAN-NOPE")["_exit"] == 3


def test_search_filters_and_pagination(world):
    root, proj = world
    by_type = data("search", "记录", "--type", "execution-log")["hits"]
    assert [h["stem"] for h in by_type] == ["执行日志"]
    assert by_type[0]["type"] == "execution-log"
    by_plan = data("search", "", "--plan", "PLAN-900A")["hits"]
    assert {h["stem"] for h in by_plan} >= {"EV--PLAN-900A 演示", "PLAN-900A--demo"}
    assert {h["entityId"] for h in by_plan} >= {"PLAN-900A", "RES-PLAN-900A"}
    assert [h["stem"] for h in data("search", "", "--status", "closed")["hits"]] == [
        "PLAN-900A--demo"
    ]
    back = data("search", "", "--backlinks-of", "PLAN-900A--demo")["hits"]
    assert {h["stem"] for h in back} >= {"执行日志", "理论", "EV--PLAN-900A 演示"}
    log = next(h for h in back if h["stem"] == "执行日志")
    assert log["line"] == 6 and "PLAN-900A--demo" in log["snippet"]
    linked = data("search", "第二句", "--linked-to", "PLAN-900A--demo")["hits"]
    assert [h["stem"] for h in linked] == ["执行日志"]
    images = data("search", "demo", "--images")
    assert images["matchMode"] == "name" and images["hits"][0]["embeddedBy"] >= 1
    page = run("search", "PLAN-900A", "--limit", "2")
    assert len(page["data"]["hits"]) == 2 and page["meta"]["nextCursor"] == "2"
    assert page["meta"]["total"] > 2
    rest = run("search", "PLAN-900A", "--limit", "2", "--cursor", "2")
    assert not {h["path"] for h in rest["data"]["hits"]} & {
        h["path"] for h in page["data"]["hits"]
    }
    assert run("search")["error"]["code"] == "USAGE_EMPTY_SEARCH"
    assert data("search", "PLAN-900A", "--fields", "stem")["hits"][0].keys() == {"stem"}


def test_search_is_project_scoped_unless_asked(world):
    root, proj = world
    write(root / "30 Zettelkasten" / "外部.md", "# 外部\n\n独有外部词汇")
    assert not data("search", "独有外部词汇")["hits"]
    assert data("search", "独有外部词汇", "--all-vault")["hits"][0]["stem"] == "外部"


def test_bundle_has_the_documented_shape(world):
    root, proj = world
    body = run("plan", "bundle", "PLAN-900A")
    assert body["ok"] and body["command"] == "plan bundle"
    d = body["data"]
    assert set(d) == set(SECTIONS)  # stdout keys are sorted; meta.sections keeps order
    assert d["links"] is None
    assert (
        d["plan"]["id"] == "PLAN-900A"
        and d["plan"]["approvedDecision"] == "O2O-DEC-0002"
    )
    assert (
        d["plan"]["closedDecision"] == "O2O-DEC-0005"
        and d["plan"]["result"] == "RES-PLAN-900A"
    )
    assert d["plan"]["summary"] == "人写正文。" and d["plan"]["body"] is None
    assert (
        d["gates"]["current"] == "closed"
        and d["gates"]["next"][-1]["to"] == "withdrawn"
    )
    roles = {x["number"]: x["role"] for x in d["decisions"]}
    assert roles == {2: "approval", 3: "rules", 4: "result", 5: "closing"}
    assert (
        d["execPlans"][0]["id"] == "PLAN-900A-S1"
        and d["execPlans"][0]["rulesShaMatches"] is True
    )
    assert d["evidence"][0]["labelState"] == "filled" and d["evidence"][0]["runs"] == [
        "0012345",
        "abc12345",
    ]
    assert d["results"][0]["status"] == "confirmed"
    assert [x["id"] for x in d["deviations"]] == ["D-01", "D-02"]
    related = {r["stem"]: r for r in d["related"]}
    assert set(related) == {"理论", "执行日志"}
    assert (
        related["理论"]["via"] == "plan-field"
        and related["理论"]["oneLine"] == "理论笔记说明。"
    )
    assert related["执行日志"]["via"] == "inbound-link"
    assert related["执行日志"]["oneLine"] == "记录了 [[PLAN-900A--demo]] 的执行。"
    assert d["analysisDocs"] == [
        {
            "id": "doc00001",
            "workspace": "w1",
            "title": "分析一",
            "oneLine": "结论一句。",
            "linkedRuns": ["0012345"],
        }
    ]
    assert [(r["id"], r["status"], r["result"], r["topic"]) for r in d["runs"]] == [
        ("0012345", "finished", "终点 0.1", "主题")
    ]
    assert set(d["runs"][0]["citedBy"]) == {
        "EV--PLAN-900A 演示",
        "附表--EV--PLAN-900A 演示",
    }
    assert [i["kind"] for i in d["openItems"]] == ["deviation-open"]
    assert d["findings"]["fail"] == 0 and isinstance(d["findings"]["items"], list)
    meta = body["meta"]
    assert (
        meta["truncated"] is False
        and meta["spillFile"] is None
        and meta["nextCursor"] is None
    )
    assert meta["sections"] == list(SECTIONS) and meta["maxBytes"] == 65536
    assert meta["bytes"] == len(
        json.dumps(d, ensure_ascii=False, sort_keys=True).encode()
    )
    assert body["warnings"] == []


def test_bundle_warns_when_cited_runs_have_no_workspace(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    body = run("plan", "bundle", "PLAN-900A")
    assert [w["code"] for w in body["warnings"]] == ["RUNS_NOT_LINKED"]
    assert body["data"]["runs"] == [] and body["data"]["analysisDocs"] == []


def test_bundle_depth_two_and_body_and_sections(world):
    root, proj = world
    write(proj / "引用.md", "指向 [[PLAN-900A--demo]]")
    write(proj / "被引用.md", "# 被引用\n\n被计划页引用的笔记。")
    plan_path = proj / PLAN_PATH
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8")
        + "\n另见 [[被引用]] 与 [[不存在的笔记]]\n",
        encoding="utf-8",
    )
    deep = data("plan", "bundle", "PLAN-900A", "--depth", "2")
    assert {x["stem"] for x in deep["links"]["inbound"]} >= {"引用", "执行日志", "理论"}
    dead = [u for u in deep["links"]["unresolved"] if u["target"] == "不存在的笔记"]
    assert dead and dead[0]["from"] == "PLAN-900A--demo" and dead[0]["line"] > 0
    assert {r["stem"]: r["via"] for r in deep["related"]}["被引用"] == "cited"
    shallow = data("plan", "bundle", "PLAN-900A")
    assert "被引用" not in {r["stem"] for r in shallow["related"]}
    full = data("plan", "bundle", "PLAN-900A", "--include-body")
    assert "人写正文" in full["plan"]["body"]
    assert "body" in full["execPlans"][0] and "body" in full["related"][0]
    assert "body" not in data("plan", "bundle", "PLAN-900A")["execPlans"][0]
    only = run("plan", "bundle", "PLAN-900A", "--sections", "gates,plan")
    assert set(only["data"]) == {"plan", "gates"} and only["meta"]["sections"] == [
        "plan",
        "gates",
    ]
    narrowed = run(
        "plan", "bundle", "PLAN-900A", "--sections", "gates,plan", "--fields", "gates"
    )
    assert list(narrowed["data"]) == ["gates"]
    bad = run("plan", "bundle", "PLAN-900A", "--sections", "nonsense")
    assert bad["_exit"] == 2 and bad["error"]["code"] == "USAGE_BAD_SECTIONS"
    assert run("plan", "bundle", "PLAN-NOPE")["_exit"] == 3


def test_bundle_spills_when_over_budget_and_pages_by_section(world, tmp_path):
    root, proj = world
    small = run("plan", "bundle", "PLAN-900A", "--max-bytes", "2500")
    meta = small["meta"]
    assert meta["truncated"] is True and meta["nextCursor"] in SECTIONS
    assert (
        set(small["data"]) == set(meta["sections"]) and "findings" not in small["data"]
    )
    spill = Path(meta["spillFile"])
    assert spill.is_file() and "research/spill" in spill.as_posix()
    assert str(spill).startswith(str(tmp_path))  # never inside the vault
    full = json.loads(spill.read_text(encoding="utf-8"))
    assert list(full["data"]) == list(SECTIONS) and full["meta"]["truncated"] is False
    seen = list(meta["sections"])
    cursor = meta["nextCursor"]
    while cursor:
        page = run(
            "plan", "bundle", "PLAN-900A", "--max-bytes", "2500", "--cursor", cursor
        )
        assert page["_exit"] == 0 and page["meta"]["sections"][0] == cursor
        seen += page["meta"]["sections"]
        cursor = page["meta"]["nextCursor"]
    assert seen == list(SECTIONS)
    assert (
        run("plan", "bundle", "PLAN-900A", "--cursor", "nonsense")["error"]["code"]
        == "USAGE_BAD_CURSOR"
    )


def test_bundle_out_writes_the_full_envelope(world, tmp_path):
    out = tmp_path / "out" / "bundle.json"
    body = run("plan", "bundle", "PLAN-900A", "--out", out)
    assert body["data"] == {} and body["meta"]["spillFile"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["ok"] and list(written["data"]) == list(SECTIONS)


def test_bundle_unavailable_workspace_is_a_warning(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj, "--workspace", "gone")
    body = run("plan", "bundle", "PLAN-900A")
    assert body["_exit"] == 0
    assert "RUNS_UNAVAILABLE" in [w["code"] for w in body["warnings"]]


def test_bundle_gates_explain_what_blocks_the_next_step(world):
    root, proj = world
    write(
        proj / PLAN_PATH,
        v3_plan(
            status="draft", approved_decision=None, closed_decision=None, result=None
        ),
    )
    gates = data("plan", "bundle", "PLAN-900A", "--sections", "gates")["gates"]
    approve = next(s for s in gates["next"] if s["to"] == "approved")
    assert (
        approve["ok"] is False
        and approve["unmet"][0]["code"] == "LIFE.PLAN_NO_APPROVAL"
    )
    approved = v3_plan(
        status="approved",
        approved_decision='"O2O-DEC-0002"',
        closed_decision=None,
        result=None,
    )
    write(proj / PLAN_PATH, approved)
    gates = data("plan", "bundle", "PLAN-900A", "--sections", "gates")["gates"]
    assert [s["to"] for s in gates["next"]] == ["running", "superseded", "withdrawn"]
    assert gates["next"][0]["ok"] is True  # the frozen exec-plan exists
    (proj / EXEC_PATH).unlink()
    gates = data("plan", "bundle", "PLAN-900A", "--sections", "gates")["gates"]
    running = gates["next"][0]
    assert running["ok"] is False
    assert [u["code"] for u in running["unmet"]] == ["LIFE.PLAN_NO_FROZEN_EXEC"]


def test_unused_paths_stay_referenced():
    assert EV_PATH and EXEC_PATH and RES_PATH


ITEM_KEYS = {
    "plan": {
        "id", "title", "status", "legacy", "path", "absPath", "sha256", "owner",
        "approvedDecision", "closedDecision", "result", "summary", "outline", "body",
    },
    "gates": {"current", "known", "currentUnmet", "next"},
    "decisions": {
        "id", "number", "date", "status", "statusRaw", "layer", "title", "plans",
        "confirmedBy", "confirmedOn", "version", "recordedVia", "legacy", "source",
        "path", "supersedes", "supersededBy", "role", "why",
    },
    "execPlans": {
        "id", "stem", "plan", "stage", "status", "legacy", "frozen",
        "rulesShaMatches", "rulesDecision", "amendedBy", "path",
    },
    "evidence": {
        "stem", "status", "labelState", "labelledBy", "labelAuthorization", "plan",
        "execPlan", "figures", "appendix", "runs", "legacy", "path",
    },
    "results": {
        "id", "stem", "plan", "status", "statusRaw", "confirmedDecision",
        "deviationImpact", "supersededBy", "evidence", "legacy", "path",
    },
    "deviations": {
        "id", "date", "status", "found", "impact", "experiment", "cards", "summary",
    },
    "related": {"stem", "type", "status", "path", "title", "oneLine", "via"},
    "analysisDocs": {"id", "workspace", "title", "oneLine", "linkedRuns"},
    "runs": {"id", "workspace", "topic", "status", "result", "updatedAt", "citedBy"},
    "openItems": {
        "id", "kind", "owner", "severity", "message", "ref", "action", "command",
    },
}  # fmt: skip


def test_bundle_item_shapes_are_stable(world):
    bundle = data("plan", "bundle", "PLAN-900A")
    for section, keys in ITEM_KEYS.items():
        entries = bundle[section]
        for entry in entries if isinstance(entries, list) else [entries]:
            assert set(entry) == keys, section
    assert set(bundle["findings"]) == {"fail", "warn", "items"}
    assert set(bundle["gates"]["next"][0]) == {"to", "ok", "unmet"}


def test_plan_sha256_is_the_file_sha(world):
    import hashlib

    root, proj = world
    expected = hashlib.sha256((proj / PLAN_PATH).read_bytes()).hexdigest()
    assert data("plan", "show", "PLAN-900A")["plan"]["sha256"] == expected
    assert (
        data("plan", "bundle", "PLAN-900A", "--sections", "plan")["plan"]["sha256"]
        == expected
    )
