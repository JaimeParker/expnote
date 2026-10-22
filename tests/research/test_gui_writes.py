"""The GUI write set (design 7.2): token/origin gate, dry-run, CAS, quiet, audit."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from minivault import DEVIATIONS_9COL, DEVIATIONS_PATH, snapshot_tree
from writekit import (
    audit_records,
    edit_profile,
    registered_vault,
    run,
    set_mtime,
)

from expnote.research.decision_store import read_rows
from expnote.research.web_routes import create_research_app

BASE = "http://127.0.0.1:8812"
P = "/api/research/projects/off2on"
EV_STEM = "EV--PLAN-900A 新卡"
NOTE = '用户说 "可以"：<script>alert(1)</script> # 备注: x'


def new_audit(world):
    """Audit records written by the GUI under test (setup used the CLI)."""
    return audit_records()[world.baseline :]


def read(path):
    return path.read_text(encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_world(tmp_path, quiet=0) -> SimpleNamespace:
    root, proj = registered_vault(tmp_path, quiet=quiet)
    edit_profile(
        proj,
        '  value_columns: ["1"]',
        '  value_columns: ["1"]\n  remark_prefix: "备注"',
    )
    made = run(
        "decision", "new", "--title", "PLAN-900B 批准：开始", "--layer", "研究",
        "--decided", "批准 PLAN-900B。", "--reason", "用户要求。",
        "--plans", "PLAN-900B", "--apply",
    )  # fmt: skip
    assert made["_exit"] == 0, made
    made_ev = run(
        "new", "ev", "--plan", "PLAN-900A", "--title", "新卡", "--exec", "PLAN-900A-S1",
        "--apply",
    )  # fmt: skip
    assert made_ev["_exit"] == 0, made_ev
    ev = root / made_ev["data"]["created"][0]["path"]
    app = create_research_app()
    app.state.research.refresh_interval = 0
    client = TestClient(app, base_url=BASE)
    return SimpleNamespace(
        baseline=len(audit_records()),
        root=root,
        proj=proj,
        export=proj / "ops" / "decisions.jsonl",
        decision_id=made["data"]["id"],
        ev=ev,
        todo=proj / "待办.md",
        client=client,
        token=app.state.research.write_token,
        app=app,
    )


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def post(world, path, body=None, *, token=True, headers=None):
    sent = {"X-Expnote-Token": world.token} if token is True else {}
    if isinstance(token, str):
        sent["X-Expnote-Token"] = token
    sent.update(headers or {})
    return world.client.post(f"{P}{path}", json=body or {}, headers=sent)


def decision_row(world):
    return next(r for r in read_rows("off2on")[0] if r["id"] == world.decision_id)


def decision_events(world):
    number = decision_row(world)["number"]
    return [e for e in read_rows("off2on")[1] if e["number"] == number]


def approve(world, **extra):
    return post(
        world,
        f"/decisions/{quote(world.decision_id)}/approve",
        {"version": 1, **extra},
    )


def ev_labels(world, rows=None, **extra):
    body = {"expectedSha256": sha(world.ev), "rows": rows or [], **extra}
    return post(world, f"/ev/{quote(EV_STEM)}/labels", body)


def error_of(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False and body["error"]["code"] == code, body
    return body["error"]


# ---- token, Origin and Host --------------------------------------------------------


def test_index_page_carries_the_token_and_the_csp_is_unchanged(world):
    page = world.client.get("/research/")
    assert f'<meta name="expnote-token" content="{world.token}">' in page.text
    assert page.headers["content-security-policy"] == (
        "default-src 'self'; img-src 'self' data:"
    )
    assert page.headers["cache-control"] == "no-store"
    assert world.token not in world.client.get("/research/research.js").text
    other = create_research_app()
    assert other.state.research.write_token != world.token


def test_a_write_without_the_right_token_is_refused_and_writes_nothing(world):
    before = snapshot_tree(world.root)
    path = f"/decisions/{quote(world.decision_id)}/approve"
    body = {"version": 1}
    for token in (False, "wrong", ""):
        error_of(post(world, path, body, token=token), 403, "FORBIDDEN_TOKEN")
    assert snapshot_tree(world.root) == before and new_audit(world) == []


def test_origin_and_host_must_be_the_local_server(world):
    path = f"/decisions/{quote(world.decision_id)}/approve"
    body = {"version": 1, "dryRun": True}
    before = snapshot_tree(world.root)
    for origin in (
        "http://evil.example",
        "http://localhost:8812",
        "null",
        "https://127.0.0.1:8812",
    ):
        response = post(world, path, body, headers={"Origin": origin})
        error_of(response, 403, "FORBIDDEN_ORIGIN")
    rebinding = TestClient(world.app, base_url="http://evil.example:8812")
    response = rebinding.post(
        f"{P}{path}", json=body, headers={"X-Expnote-Token": world.token}
    )
    error_of(response, 403, "FORBIDDEN_ORIGIN")
    assert post(world, path, body, headers={"Origin": BASE}).status_code == 200
    localhost = TestClient(world.app, base_url="http://localhost:8812")
    ok = localhost.post(
        f"{P}{path}",
        json=body,
        headers={"X-Expnote-Token": world.token, "Origin": "http://localhost:8812"},
    )
    assert ok.status_code == 200
    assert snapshot_tree(world.root) == before


# ---- the write set is exactly 7.2 --------------------------------------------------


def test_only_the_section_7_2_endpoints_can_write(world):
    writable = {
        (method.upper(), path)
        for path, methods in world.app.openapi()["paths"].items()
        for method in methods
        if method.upper() not in ("GET", "HEAD", "OPTIONS")
    }
    prefix = "/api/research/projects/{project_id}"
    assert writable == {
        ("POST", f"{prefix}/decisions/{{ident}}/approve"),
        ("POST", f"{prefix}/decisions/{{ident}}/reject"),
        ("POST", f"{prefix}/plans/{{plan_id}}/close"),
        ("POST", f"{prefix}/deviations/{{ident}}/close"),
        ("POST", f"{prefix}/ev/{{stem}}/labels"),
        ("POST", f"{prefix}/todos"),
        ("POST", f"{prefix}/todos/{{todo_id}}/done"),
    }
    for method in ("put", "patch", "delete"):
        for path in ("/decisions/2", "/todos", f"/ev/{quote(EV_STEM)}/labels"):
            response = getattr(world.client, method)(
                f"{P}{path}", headers={"X-Expnote-Token": world.token}
            )
            assert response.status_code in (404, 405)
    for path in ("/plans/PLAN-900A", "/decisions/2/supersede", "/ev/x/commit"):
        assert post(world, path, {}).status_code in (404, 405), path


# ---- decisions ---------------------------------------------------------------------


def test_dry_run_returns_the_diff_and_writes_nothing(world):
    before = snapshot_tree(world.root)
    rows = read_rows("off2on")
    response = approve(world, dryRun=True)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dryRun"] is True and data["applied"] is False
    store, export = data["changes"]
    assert "-status: proposed" in store["unifiedDiff"]
    assert "+status: confirmed" in store["unifiedDiff"]
    assert "+confirmed_by: user" in store["unifiedDiff"]
    assert export["path"].endswith("ops/decisions.jsonl")
    assert '+{"actor":' not in export["unifiedDiff"]  # sorted keys, one line each
    assert '"status": "confirmed"' in export["unifiedDiff"]
    assert snapshot_tree(world.root) == before and new_audit(world) == []
    assert read_rows("off2on") == rows


def test_approve_records_the_user_and_rewrites_the_export(world):
    old_export = world.export.read_bytes()
    response = approve(world)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["applied"] is True and data["from"] == "proposed"
    row = decision_row(world)
    assert row["status"] == "confirmed" and row["confirmed_by"] == "user"
    assert row["authorization"] is None and row["version"] == 2
    assert row["decided"] == "批准 PLAN-900B。"  # the text never moves
    first, second = decision_events(world)
    assert (first["action"], second["action"]) == ("propose", "approve")
    assert second["actor"] == "gui" and second["before"]["status"] == "proposed"
    assert world.export.read_bytes() != old_export
    assert '"status":"confirmed"' in world.export.read_text(encoding="utf-8").replace(
        ": ", ":"
    )
    (record,) = new_audit(world)
    assert record["payload"]["actor"] == "gui"
    assert record["payload"]["command"] == "decision set"
    assert record["payload"]["path"].endswith("ops/decisions.jsonl")
    findings = run("check", "--level", "FAIL")["data"]["findings"]
    assert not [f for f in findings if f["code"].startswith("DEC.")]


def test_reject_and_the_optional_note(world):
    response = post(
        world,
        f"/decisions/{quote(world.decision_id)}/reject",
        {"version": 1, "note": NOTE},
    )
    assert response.status_code == 200, response.text
    row = decision_row(world)
    assert row["status"] == "rejected" and row["confirmed_by"] == "user"
    assert row["authorization"] == NOTE
    again = run("decision", "show", world.decision_id)
    assert again["data"]["decision"]["status"] == "rejected"
    assert again["data"]["decision"]["history"][-1]["action"] == "reject"


def test_the_note_is_data_in_the_json_diff(world):
    response = approve(world, dryRun=True, note=NOTE)
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    diff = response.json()["data"]["changes"][0]["unifiedDiff"]
    assert "<script>alert(1)</script>" in diff  # escaping is the page's job (smoke.mjs)


def test_a_second_identical_approve_is_a_no_op(world):
    assert approve(world).status_code == 200
    after_first = world.export.read_bytes()
    events = len(new_audit(world))
    stamp = world.export.stat().st_mtime_ns
    # the page still holds the old version: the state already holds, nothing happens
    path = f"/decisions/{quote(world.decision_id)}/approve"
    second = post(world, path, {"version": 1})
    assert second.status_code == 200
    data = second.json()["data"]
    assert data["changes"] == [] and data["applied"] is False
    assert world.export.read_bytes() == after_first
    assert world.export.stat().st_mtime_ns == stamp
    assert len(new_audit(world)) == events and decision_row(world)["version"] == 2


def test_approving_something_that_cannot_be_approved(world):
    assert approve(world).status_code == 200
    reject = post(
        world, f"/decisions/{quote(world.decision_id)}/reject", {"version": 2}
    )
    error = error_of(reject, 422, "GATE_FAILED")
    assert error["details"]["gates"]
    error_of(
        post(world, "/decisions/O2O-DEC-9999/approve", {"version": 1}),
        404,
        "NOT_FOUND",
    )
    legacy = post(world, "/decisions/2/approve", {"version": 1})
    assert legacy.status_code in (404, 422)


def test_version_is_required_and_a_stale_one_is_a_conflict(world):
    path = f"/decisions/{quote(world.decision_id)}/approve"
    error_of(post(world, path, {}), 422, "USAGE_MISSING_OPTION")
    for bad in ("abc", 0, True, 1.5):
        error_of(post(world, path, {"version": bad}), 422, "USAGE_MISSING_OPTION")
    error_of(
        post(world, path, {"version": 1, "dryRun": "yes"}), 422, "USAGE_BAD_VALUE"
    )
    for raw in (b"[1]", b"not json"):
        response = world.client.post(
            f"{P}{path}", content=raw, headers={"X-Expnote-Token": world.token}
        )
        error_of(response, 422, "USAGE_BAD_VALUE")
    # somebody else decided meanwhile: the page's version 1 is stale
    other = run(
        "decision", "set", world.decision_id, "--status", "rejected",
        "--authorization", "用户在聊天里说不行", "--apply",
    )  # fmt: skip
    assert other["_exit"] == 0
    error = error_of(post(world, path, {"version": 1}), 409, "CONFLICT")
    assert error["details"] == {"expectedVersion": 1, "currentVersion": 2}
    assert decision_row(world)["status"] == "rejected"


def test_a_hand_edited_export_is_reported_not_overwritten(world):
    world.export.write_text("# edited by hand\n", encoding="utf-8")
    response = approve(world)
    assert response.status_code == 200, response.text
    codes = [w["code"] for w in response.json()["warnings"]]
    assert codes == ["EXPORT_DIVERGED"]
    assert world.export.read_text(encoding="utf-8") == "# edited by hand\n"
    assert decision_row(world)["status"] == "confirmed"  # the decision is committed
    forced = run("decision", "export", "--force", "--apply")
    assert forced["_exit"] == 0 and forced["data"]["applied"] is True
    assert "edited by hand" not in world.export.read_text(encoding="utf-8")


def test_a_busy_export_leaves_the_decision_committed(world, monkeypatch):
    import expnote.research.writeback as writeback

    def busy(source, target):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(writeback.os, "replace", busy)
    monkeypatch.setattr(writeback, "REPLACE_BACKOFF_SECONDS", 0)
    response = approve(world)
    assert response.status_code == 200, response.text
    assert [w["code"] for w in response.json()["warnings"]] == ["FILE_BUSY"]
    assert decision_row(world)["status"] == "confirmed"


def test_every_audit_record_names_the_gui_even_if_the_environment_says_otherwise(
    world, monkeypatch
):
    monkeypatch.setenv("EXPNOTE_ACTOR", "claude")
    assert approve(world).status_code == 200
    assert (
        ev_labels(world, [{"key": "...", "values": {"Value": "跌落"}}]).status_code
        == 200
    )
    assert {r["payload"]["actor"] for r in new_audit(world)} == {"gui"}


# ---- close a plan ------------------------------------------------------------------


def open_plan(world):
    made = run("new", "plan", "--id", "PLAN-901A", "--title", "演示二", "--apply")
    assert made["_exit"] == 0, made
    path = world.root / made["data"]["created"][0]["path"]
    set_mtime(path, 3600)
    world.baseline = len(audit_records())  # the page under test starts here
    return path


def close_plan(world, path, plan="PLAN-901A", **extra):
    body = {"expectedSha256": sha(path), "reason": "用户决定结项", **extra}
    return post(world, f"/plans/{plan}/close", body)


def test_close_plan_needs_the_token_origin_sha_and_reason(world):
    path = open_plan(world)
    before = snapshot_tree(world.root)
    url = f"{P}/plans/PLAN-901A/close"
    good = {"expectedSha256": sha(path), "reason": "r", "force": True, "dryRun": True}
    for token in (False, "wrong"):
        response = post(world, "/plans/PLAN-901A/close", good, token=token)
        error_of(response, 403, "FORBIDDEN_TOKEN")
    evil = post(world, "/plans/PLAN-901A/close", good, headers={"Origin": "http://evil"})
    error_of(evil, 403, "FORBIDDEN_ORIGIN")
    rebinding = TestClient(world.app, base_url="http://evil.example:8812")
    response = rebinding.post(
        url, json=good, headers={"X-Expnote-Token": world.token}
    )
    error_of(response, 403, "FORBIDDEN_ORIGIN")
    no_sha = {k: v for k, v in good.items() if k != "expectedSha256"}
    error_of(post(world, "/plans/PLAN-901A/close", no_sha), 422, "USAGE_MISSING_OPTION")
    no_reason = {k: v for k, v in good.items() if k != "reason"}
    error_of(post(world, "/plans/PLAN-901A/close", no_reason), 422, "USAGE_BAD_VALUE")
    blank = {**good, "reason": "  "}
    error_of(post(world, "/plans/PLAN-901A/close", blank), 422, "USAGE_EMPTY_TEXT")
    bad_force = {**good, "force": "yes"}
    error_of(post(world, "/plans/PLAN-901A/close", bad_force), 422, "USAGE_BAD_VALUE")
    assert snapshot_tree(world.root) == before and new_audit(world) == []
    local = post(world, "/plans/PLAN-901A/close", good, headers={"Origin": BASE})
    assert local.status_code == 200


def test_close_plan_shows_the_failed_gates_before_anything_is_forced(world):
    path = open_plan(world)
    before = snapshot_tree(world.root)
    refused = close_plan(world, path, dryRun=True)
    error = error_of(refused, 422, "GATE_FAILED")
    codes = {g["code"] for g in error["details"]["gates"]}
    assert "LIFE.TRANSITION" in codes and "--force" in error["remediation"]
    forced = close_plan(world, path, force=True, dryRun=True)
    assert forced.status_code == 200, forced.text
    data = forced.json()["data"]
    assert data["dryRun"] is True and data["forced"] is True
    store, plan_file, export = data["changes"]
    assert store["op"] == "decision-new"
    assert "+status: confirmed" in store["unifiedDiff"]
    assert "+forced_close: true" in plan_file["unifiedDiff"]
    assert "+status: closed" in plan_file["unifiedDiff"]
    assert export["path"].endswith("ops/decisions.jsonl")
    assert snapshot_tree(world.root) == before and new_audit(world) == []


def test_closing_without_the_final_res_is_the_users_decision(world):
    path = open_plan(world)
    stale = sha(path)
    path.write_text(read(path) + "\n手改一行\n", encoding="utf-8")
    set_mtime(path, 3600)
    body = {"expectedSha256": stale, "reason": "r", "force": True}
    error_of(post(world, "/plans/PLAN-901A/close", body), 409, "CAS_MISMATCH")
    rows = read_rows("off2on")[0]
    assert [r for r in rows if r["plans"] == ["PLAN-901A"]] == []  # nothing recorded
    path.write_text(read(path).replace("\n手改一行\n", ""), encoding="utf-8")
    set_mtime(path, 3600)
    done = close_plan(world, path, force=True, note="用户点了关闭")  # the sha is fresh
    assert done.status_code == 200, done.text
    text = read(path)
    assert "status: closed" in text and "forced_close: true" in text
    row = next(r for r in read_rows("off2on")[0] if r["plans"] == ["PLAN-901A"])
    assert row["status"] == "confirmed" and row["confirmed_by"] == "user"
    assert row["source"] == "gui" and row["reason"] == "用户决定结项"
    assert row["authorization"] == "用户点了关闭"
    assert f'closed_decision: "{row["id"]}"' in text
    assert {r["payload"]["actor"] for r in new_audit(world)} == {"gui"}
    levels = {
        f["code"]: f["level"]
        for f in run("check", "--plan", "PLAN-901A")["data"]["newCodes"]
    }
    assert levels["LIFE.PLAN_FORCED_CLOSE"] == "INFO"
    fresh = post(
        world,
        "/plans/PLAN-901A/close",
        {"expectedSha256": sha(path), "reason": "again", "force": True},
    )
    assert fresh.status_code == 200 and fresh.json()["data"]["changes"] == []


def test_a_confirmed_plan_closes_normally_from_the_gui(world):
    row = {"key": "...", "values": {"Value": "跌落"}}
    assert ev_labels(world, [row], commit=True).status_code == 200
    plan = world.proj / "plans/PLAN-900A--demo.md"
    plan.write_text(read(plan).replace("status: closed", "status: confirmed"), "utf-8")
    set_mtime(plan, 3600)
    response = post(
        world,
        "/plans/PLAN-900A/close",
        {"expectedSha256": sha(plan), "reason": "做完了"},
    )
    assert response.status_code == 200, response.text
    text = read(plan)
    assert "status: closed" in text and "forced_close" not in text
    assert "closed_decision: 10" in text  # a schema-2 plan keeps its number form
    assert response.json()["data"]["gates"][0]["ok"] is True


# ---- evidence labels ---------------------------------------------------------------


def test_ev_labels_batch_commit(world):
    before = read(world.ev)
    assert "status: rendered" in before and "| ... |  |" in before
    dry = ev_labels(
        world,
        [{"key": "...", "values": {"Value": "跌落 | 严重"}}],
        remark="用户说：整体偏低",
        commit=True,
        dryRun=True,
    )
    assert dry.status_code == 200, dry.text
    diff = dry.json()["data"]["changes"][0]["unifiedDiff"]
    assert "+status: labelled" in diff and "+labelled_by: user" in diff
    assert read(world.ev) == before and new_audit(world) == []
    done = ev_labels(
        world,
        [{"key": "...", "values": {"Value": "跌落 | 严重"}}],
        remark="用户说：整体偏低",
        commit=True,
    )
    assert done.status_code == 200, done.text
    assert len(done.json()["data"]["changes"]) == 1  # one file, one edit
    after = read(world.ev)
    assert "| ... | 跌落 \\| 严重 |" in after and "备注: 用户说：整体偏低" in after
    assert "status: labelled" in after and "labelled_by: user" in after
    assert "labelled_on: " in after and "label_authorization" not in after
    assert (
        len(new_audit(world)) == 1 and new_audit(world)[0]["payload"]["actor"] == "gui"
    )
    findings = run("check", "--level", "FAIL")["data"]["findings"]
    assert not [f for f in findings if f["code"].startswith("EV.")]


def test_ev_labels_save_without_commit_then_commit_alone(world):
    saved = ev_labels(world, [{"key": "...", "values": {"Value": "跌落"}}])
    assert saved.status_code == 200
    assert "status: rendered" in read(world.ev) and "| ... | 跌落 |" in read(world.ev)
    identical = ev_labels(world, [{"key": "...", "values": {"Value": "跌落"}}])
    assert identical.json()["data"]["changes"] == []
    committed = ev_labels(world, commit=True)
    assert committed.status_code == 200
    assert "labelled_by: user" in read(world.ev)
    again = ev_labels(world, commit=True)
    assert again.status_code == 200 and again.json()["data"]["changes"] == []


def test_ev_labels_refusals(world):
    row = {"key": "...", "values": {"Value": "x"}}
    error_of(ev_labels(world, commit=True), 422, "GATE_FAILED")
    error_of(
        ev_labels(world, [{"key": "nope", "values": {"Value": "x"}}]),
        422,
        "ADDRESS_NOT_FOUND",
    )
    error_of(
        ev_labels(world, [{"key": "...", "values": {"臂": "x"}}]),
        422,
        "ADDRESS_NOT_FOUND",
    )
    error_of(
        ev_labels(world, [{"key": "...", "values": {"Nope": "x"}}]),
        422,
        "ADDRESS_NOT_FOUND",
    )
    error_of(
        ev_labels(world, [{"key": "...", "values": {"Value": "a\nb"}}]),
        422,
        "USAGE_MULTILINE_VALUE",
    )
    error_of(ev_labels(world, [{"key": "..."}]), 422, "USAGE_BAD_VALUE")
    error_of(ev_labels(world, []), 422, "USAGE_MISSING_OPTION")
    error_of(ev_labels(world, remark="alone"), 422, "USAGE_MISSING_OPTION")
    error_of(
        post(world, f"/ev/{quote(EV_STEM)}/labels", {"rows": [row]}),
        422,
        "USAGE_MISSING_OPTION",
    )
    error_of(
        post(world, "/ev/没有/labels", {"expectedSha256": "0" * 64, "rows": [row]}),
        404,
        "NOT_FOUND",
    )
    assert "| ... |  |" in read(world.ev) and new_audit(world) == []
    assert ev_labels(world, [row]).status_code == 200
    assert ev_labels(world, commit=True).status_code == 200
    error_of(
        ev_labels(world, [row]), 422, "GATE_FAILED"
    )  # labelled pages are not edited


def test_ev_detail_tells_the_page_whether_a_remark_can_be_saved(world):
    shown = world.client.get(f"{P}/ev/{quote(EV_STEM)}").json()["data"]
    assert shown["write"] == {"remark": True}
    assert shown["ev"]["labelTable"]["valueColumns"] == [1]


# ---- manual TODOs ------------------------------------------------------------------


def todo_sha(world):
    listing = world.client.get(f"{P}/todos", params={"source": "manual"}).json()
    return listing["data"]["manualFile"]["sha256"]


def test_todo_add_creates_the_file_then_appends_for_the_chosen_owner(world):
    assert not world.todo.exists()
    first = post(world, "/todos", {"text": "回看 [[别的笔记]]", "plan": "PLAN-900A"})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["id"] == "T-0001"
    assert read(world.todo).endswith(
        "- [ ] 回看 [[别的笔记]] [id:: T-0001] [plan:: PLAN-900A] "
        "[by:: user] [owner:: user]\n"
    )
    stale = todo_sha(world)
    second = post(
        world, "/todos", {"text": "给执行员", "owner": "agent", "expectedSha256": stale}
    )
    assert second.status_code == 200
    assert read(world.todo).endswith(
        "- [ ] 给执行员 [id:: T-0002] [by:: user] [owner:: agent]\n"
    )
    listed = world.client.get(f"{P}/todos", params={"source": "manual"}).json()["data"][
        "todos"
    ]
    assert {t["id"]: t["owner"] for t in listed} == {
        "T-0001": "user",
        "T-0002": "agent",
    }
    set_mtime(world.todo, 3600)
    error_of(
        post(world, "/todos", {"text": "晚了", "expectedSha256": stale}),
        409,
        "CAS_MISMATCH",
    )
    error_of(post(world, "/todos", {"text": "缺 sha"}), 422, "USAGE_MISSING_OPTION")
    assert {r["payload"]["actor"] for r in new_audit(world)} == {"gui"}


def test_todo_add_validation(world):
    for body in (
        {"text": ""},
        {"text": "x [id:: T-0099]"},
        {"text": "two\nlines"},
        {"text": "x", "owner": "root"},
        {"text": "x", "due": "soon"},
        {"text": 5},
        {},
    ):
        response = post(world, "/todos", body)
        assert response.status_code == 422, (body, response.text)
    assert not world.todo.exists()
    dry = post(world, "/todos", {"text": "试一试", "dryRun": True})
    assert dry.status_code == 200 and not world.todo.exists()
    assert "+- [ ] 试一试" in dry.json()["data"]["changes"][0]["unifiedDiff"]


def test_todo_tick_untick_and_the_second_tick_is_a_no_op(world):
    post(world, "/todos", {"text": "一件事"})
    set_mtime(world.todo, 3600)
    path = "/todos/T-0001/done"
    ticked = post(world, path, {"done": True, "expectedSha256": sha(world.todo)})
    assert ticked.status_code == 200, ticked.text
    line = read(world.todo).splitlines()[-1]
    assert line.startswith("- [x] 一件事 [id:: T-0001]") and "[done:: " in line
    events, stamp = len(new_audit(world)), world.todo.stat().st_mtime_ns
    again = post(world, path, {"done": True, "expectedSha256": sha(world.todo)})
    assert again.status_code == 200 and again.json()["data"]["changes"] == []
    assert len(new_audit(world)) == events and world.todo.stat().st_mtime_ns == stamp
    undone = post(world, path, {"done": False, "expectedSha256": sha(world.todo)})
    assert undone.status_code == 200 and "[done::" not in read(world.todo)
    error_of(
        post(
            world,
            "/todos/T-0404/done",
            {"done": True, "expectedSha256": sha(world.todo)},
        ),
        422,
        "ADDRESS_NOT_FOUND",
    )
    error_of(
        post(world, path, {"expectedSha256": sha(world.todo)}), 422, "USAGE_BAD_VALUE"
    )
    error_of(
        post(world, path, {"done": True, "expectedSha256": "1" * 64}),
        409,
        "CAS_MISMATCH",
    )


# ---- deviation register ------------------------------------------------------------


def register_sha(world):
    return sha(world.proj / DEVIATIONS_PATH)


def register_row(world, ident="D-02"):
    lines = read(world.proj / DEVIATIONS_PATH).split("\n")
    return next(ln for ln in lines if ln.startswith(f"| {ident} "))


def close_deviation(world, ident="D-02", **extra):
    body = {"expectedSha256": register_sha(world), **extra}
    return post(world, f"/deviations/{ident}/close", body)


def test_deviation_detail_carries_the_whole_row_and_the_sha_to_close_at(world):
    shown = world.client.get(f"{P}/deviations/D-01").json()["data"]["deviation"]
    assert shown["id"] == "D-01" and shown["status"] == "closed"
    assert shown["disposition"] == "已关闭" and shown["experiment"] == "PLAN-900A S1"
    assert shown["registerSha256"] == register_sha(world)
    assert set(shown["html"]) == {
        "experiment",
        "content",
        "impact",
        "disposition",
        "cards",
    }
    assert 'href="#/p/off2on/' in shown["html"]["cards"]  # related cards are links
    error_of(world.client.get(f"{P}/deviations/D-99"), 404, "NOT_FOUND")


def test_deviation_close_needs_the_token_origin_and_sha(world):
    before = snapshot_tree(world.root)
    good = {"expectedSha256": register_sha(world), "dryRun": True}
    for token in (False, "wrong", ""):
        response = post(world, "/deviations/D-02/close", good, token=token)
        error_of(response, 403, "FORBIDDEN_TOKEN")
    evil = post(world, "/deviations/D-02/close", good, headers={"Origin": "http://evil"})
    error_of(evil, 403, "FORBIDDEN_ORIGIN")
    rebinding = TestClient(world.app, base_url="http://evil.example:8812")
    response = rebinding.post(
        f"{P}/deviations/D-02/close",
        json=good,
        headers={"X-Expnote-Token": world.token},
    )
    error_of(response, 403, "FORBIDDEN_ORIGIN")
    no_sha = {"dryRun": True}
    error_of(post(world, "/deviations/D-02/close", no_sha), 422, "USAGE_MISSING_OPTION")
    bad_text = {**good, "disposition": 5}
    error_of(post(world, "/deviations/D-02/close", bad_text), 422, "USAGE_BAD_VALUE")
    multiline = {**good, "disposition": "a\nb"}
    error_of(
        post(world, "/deviations/D-02/close", multiline), 422, "USAGE_MULTILINE_VALUE"
    )
    error_of(close_deviation(world, "D-99"), 404, "NOT_FOUND")
    assert snapshot_tree(world.root) == before and new_audit(world) == []
    local = post(world, "/deviations/D-02/close", good, headers={"Origin": BASE})
    assert local.status_code == 200


def test_deviation_close_replaces_the_disposition_and_leaves_an_audit_trail(world):
    dry = close_deviation(world, disposition="已处理：见 PLAN-900A", dryRun=True)
    assert dry.status_code == 200 and dry.json()["data"]["changes"]
    assert "维持" in register_row(world) and new_audit(world) == []
    done = close_deviation(world, disposition="已处理：见 PLAN-900A")
    assert done.status_code == 200, done.text
    row = register_row(world)
    assert "已处理：见 PLAN-900A 关闭" in row and "维持" not in row
    shown = world.client.get(f"{P}/deviations/D-02").json()["data"]["deviation"]
    assert shown["status"] == "closed"
    todos = world.client.get(f"{P}/todos", params={"kind": "deviation-open"})
    assert todos.json()["data"]["todos"] == []
    records = new_audit(world)
    assert len(records) == 1
    assert records[0]["payload"]["actor"] == "gui"
    assert records[0]["payload"]["command"] == "deviation close"
    assert close_deviation(world, disposition="已处理：见 PLAN-900A").json()["data"][
        "changes"
    ] == []  # closing twice is a no-op


def test_deviation_close_with_a_status_column_sets_it_and_the_disposition(world):
    path = world.proj / DEVIATIONS_PATH
    path.write_text(DEVIATIONS_9COL, encoding="utf-8")
    set_mtime(path, 3600)
    done = close_deviation(world, disposition="已处理")
    assert done.status_code == 200, done.text
    row = register_row(world)
    assert "| 已处理 |" in row and "| closed |" in row


def test_deviation_close_on_a_changed_register_is_a_conflict(world):
    stale = register_sha(world)
    path = world.proj / DEVIATIONS_PATH
    path.write_text(read(path) + "\n", encoding="utf-8")
    set_mtime(path, 3600)
    before = snapshot_tree(world.root)
    response = post(world, "/deviations/D-02/close", {"expectedSha256": stale})
    error_of(response, 409, "CAS_MISMATCH")
    assert snapshot_tree(world.root) == before and new_audit(world) == []
