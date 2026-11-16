import json
from pathlib import Path

from test_write_commands import confirm
from writekit import run

from expnote.research import lifecycle
from expnote.research.runs import RunRow, RunsJoin
from expnote.research.view import open_view


def _ok(*args):
    response = run(*args, "--apply")
    assert response["_exit"] == 0, response
    return response["data"]


def _init_split(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    project = vault / "Study"
    project.mkdir()
    _ok("project", "init", "--project-dir", project, "--layout", "split")


def _gate(*args, code=None):
    response = run(*args, "--apply")
    assert response["_exit"] in (1, 7), response
    if code:
        assert code in str(response), response


def _spec(path, content):
    path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def _propose(title, plan):
    return _ok(
        "decision",
        "new",
        "--title",
        title,
        "--layer",
        "research",
        "--decided",
        title,
        "--reason",
        "Verify the workflow",
        "--plans",
        plan,
    )["id"]


def _contract(criteria="Use the plotted return", review="agent"):
    return {
        "deliverables": [
            {
                "id": "comparison",
                "question": "Does the run reproduce the result?",
                "files": ["curve"],
                "criteria": criteria,
                "review": review,
            },
        ]
    }


def _approve(tmp_path, plan, contract):
    decision = _propose("Approve workflow", plan)
    _ok(
        "plan",
        "submit",
        plan,
        "--decision",
        decision,
        "--workflow",
        _spec(tmp_path / "plan.json", contract),
    )
    response = confirm(decision)
    assert response["_exit"] == 0, response
    return decision


def _execution(tmp_path, plan, decision, stage="S1"):
    created = _ok("new", "exec", "--plan", plan, "--stage", stage, "--title", stage)
    exec_id = created["id"]
    path = Path(created["created"][0]["absPath"])
    with open_view() as view:
        heading = f"## {view.profile.heading_rules}"
    path.write_text(
        path.read_text().replace(heading, heading + "\n\nUse the approved protocol.")
    )
    _ok("exec", "freeze", exec_id, "--rules-decision", decision)
    _ok(
        "plan",
        "checklist",
        plan,
        "--from-file",
        _spec(tmp_path / "stages.json", {exec_id: {"deliverables": ["comparison"]}}),
    )
    return exec_id


def _evidence(tmp_path, plan, exec_id):
    created = _ok("new", "ev", "--plan", plan, "--exec", exec_id, "--title", "Curve")
    path = Path(created["created"][0]["absPath"])
    with open_view() as view:
        note = next(n for n in view.snapshot.notes if n.path == path)
        ident = note.get("record_id")
    artifact = path.parent / "curve.json"
    artifact.write_text('{"return": 10}')
    receipt = _spec(
        tmp_path / "outputs.json", {"comparison": {"curve": "evidence/curve.json"}}
    )
    _ok("ev", "register", ident, "--exec", exec_id, "--from-file", receipt)
    return ident, path, artifact


def test_cannot_skip_stage_evidence_result_or_close(tmp_path, monkeypatch):
    statuses = {"old-run": "finished", "current-run": "running"}

    def runs(_workspaces, ids):
        rows = [
            RunRow(key, "study", "replication", statuses[key], "", "") for key in ids
        ]
        return RunsJoin(rows, [], {}, [])

    monkeypatch.setattr(lifecycle, "join_runs", runs)
    _init_split(tmp_path)
    plan = "PLAN-001"
    _ok("new", "plan", "--id", plan, "--title", "Comparison")
    _gate("new", "exec", "--plan", plan, "--stage", "S1", "--title", "Early")
    decision = _approve(tmp_path, plan, _contract())
    _gate("plan", "advance", plan, "--to", "running", code="FLOW.CHECKLIST")
    exec_id = _execution(tmp_path, plan, decision)
    _gate("exec", "advance", exec_id, "--to", "running")
    _ok("plan", "advance", plan, "--to", "running")
    _gate(
        "exec",
        "advance",
        exec_id,
        "--to",
        "running",
        "--run",
        "old-run",
        code="FLOW.RUN_ALREADY_ENDED",
    )
    start = ("exec", "advance", exec_id, "--to", "running", "--run", "current-run")
    binding = _ok(*start)["runBinding"]
    assert binding["runs"] == {"current-run": "study"}
    assert binding["planRevision"] != binding["execRevision"]
    created = _ok("new", "res", "--plan", plan, "--title", "Result")
    result = created["id"]
    result_path = Path(created["created"][0]["absPath"])
    submit = ("res", "submit", result, "--deviation-impact", "none")
    _gate(*submit, code="FLOW.STAGE_INCOMPLETE")
    _gate("exec", "advance", exec_id, "--to", "done", code="FLOW.STAGE_NO_EVIDENCE")
    _, _, artifact = _evidence(tmp_path, plan, exec_id)
    _gate("exec", "advance", exec_id, "--to", "done", code="FLOW.RUN_INCOMPLETE")
    statuses["current-run"] = "finished"
    assert _ok(*start)["runBinding"] == binding
    _ok("exec", "advance", exec_id, "--to", "done")
    _ok(*submit)
    artifact.write_text('{"return": 11}')
    review = (
        "res",
        "review",
        result,
        "--verdict",
        "pass",
        "--authorization",
        "I confirm",
    )
    _gate(*review, code="FLOW.EVIDENCE_CHANGED")
    artifact.write_text('{"return": 10}')
    _ok(*review)
    result_path.write_text(
        result_path.read_text() + "\nCorrect the final comparison.\n"
    )
    _gate(
        "plan",
        "close",
        plan,
        "--reason",
        "Early",
        "--authorization",
        "Close",
        code="FLOW.RESULT_STALE",
    )
    _ok(*submit)
    _ok(*review)
    _gate("plan", "advance", plan, "--to", "closed", "--decision", decision)
    _gate(
        "plan",
        "close",
        plan,
        "--reason",
        "early",
        "--force",
        "--authorization",
        "I agree",
    )
    _ok("plan", "close", plan, "--reason", "Done", "--authorization", "Close this plan")
    with open_view() as view:
        state = view.cache["workflow"][plan]
        assert state["fields"]["status"] == "closed"
        assert state["conclusion"]["revision"] == state["revision"]


def test_revision_keeps_history_and_requires_new_result(tmp_path):
    _init_split(tmp_path)
    plan = "PLAN-001"
    created = _ok("new", "plan", "--id", plan, "--title", "Comparison")
    path = Path(created["created"][0]["absPath"])
    decision = _approve(tmp_path, plan, _contract())
    stage = _execution(tmp_path, plan, decision)
    _ok("plan", "advance", plan, "--to", "running")
    _ok("exec", "advance", stage, "--to", "running")
    ev, _, _ = _evidence(tmp_path, plan, stage)
    _ok("exec", "advance", stage, "--to", "done")
    res = _ok("new", "res", "--plan", plan, "--title", "Result")["id"]
    _ok("res", "submit", res, "--deviation-impact", "none")
    _ok("res", "review", res, "--verdict", "pass", "--authorization", "Approved")
    with open_view() as view:
        old = view.cache["workflow"][res]["revision"]
    path.write_text(path.read_text() + "\nApply a different threshold.\n")
    spec = _contract("Apply a different threshold", "user")
    spec["impact"] = {
        "exec": {stage: "reuse"},
        "evidence": {ev: "review"},
        "result": "reuse-body",
    }
    _approve(tmp_path, plan, spec)
    with open_view() as view:
        states = view.cache["workflow"]
        assert states[plan]["fields"]["status"] == "approved"
        assert states[stage]["planRevision"] != states[plan]["revision"]
        assert old in states[res]["history"]
    _gate("plan", "close", plan, "--reason", "old RES", "--authorization", "Close")
    checklist = _spec(
        tmp_path / "reuse.json", {stage: {"deliverables": ["comparison"]}}
    )
    _ok("plan", "checklist", plan, "--from-file", checklist)
    _ok("plan", "advance", plan, "--to", "running")
    _gate(
        "res", "submit", res, "--deviation-impact", "none", code="FLOW.EVIDENCE_REVIEW"
    )
    _ok(
        "ev",
        "label",
        "set",
        ev,
        "--row",
        "...",
        "--value",
        "Value=reviewed",
        "--authorization",
        "Use this label",
    )
    _ok("ev", "label", "commit", ev, "--authorization", "Confirm the labels")
    _ok("res", "submit", res, "--deviation-impact", "none")
    _ok(
        "res",
        "review",
        res,
        "--verdict",
        "pass",
        "--authorization",
        "Accept the revised RES",
    )
    _ok("plan", "close", plan, "--reason", "Complete", "--authorization", "Close")


def test_competing_approvals_compare_decisions_even_with_same_plan_bytes(tmp_path):
    _init_split(tmp_path)
    plan = "PLAN-001"
    _ok("new", "plan", "--id", plan, "--title", "Comparison")
    _approve(tmp_path, plan, _contract())
    spec = {**_contract("Revised rule"), "impact": {}}
    source = _spec(tmp_path / "revision.json", spec)
    prior = _propose("Competing submission", plan)
    later = _propose("Another submission", plan)
    for decision in (prior, later):
        _ok("plan", "submit", plan, "--decision", decision, "--workflow", source)
    assert confirm(later)["_exit"] == 0
    refused = confirm(prior)
    assert refused["_exit"] == 5, refused
    _ok("plan", "submit", plan, "--decision", prior, "--workflow", source, "--replace")
    assert confirm(prior)["_exit"] == 0


def test_withdraw_records_user_decision_without_final_result(tmp_path):
    _init_split(tmp_path)
    plan = "PLAN-001"
    _ok("new", "plan", "--id", plan, "--title", "Comparison")
    _approve(tmp_path, plan, _contract())
    _gate("plan", "withdraw", plan, "--reason", "Stop", code="USER_ONLY")
    event = _ok(
        "plan",
        "withdraw",
        plan,
        "--reason",
        "Stop",
        "--authorization",
        "I want to stop",
    )
    with open_view() as view:
        state = view.cache["workflow"][plan]
        assert state["fields"]["status"] == "withdrawn"
        assert state["conclusion"]["decision"] == event["decision"]
