"""CLI behaviour: envelope, exit codes, --fields/--level/--code/--compat, registry."""

from __future__ import annotations

import json
import shutil

import pytest
from minivault import (
    IMAGES_REL,
    PROFILE_FIXTURE,
    SCENARIOS,
    build_vault,
    scenario_vault,
)
from typer.testing import CliRunner

from expnote.cli import app

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, list(args))


def payload(result):
    return json.loads(result.stdout)


def register_args(root, proj, images=None, *extra):
    return [
        "project",
        "register",
        "--vault-root",
        str(root),
        "--project-dir",
        str(proj),
        "--images-dir",
        str(images or root / IMAGES_REL),
        "--profile-note",
        str(proj / PROFILE_FIXTURE.name),
        "--json",
        *extra,
    ]


def register(root, proj, *extra):
    return invoke(*register_args(root, proj, None, *extra))


@pytest.fixture
def registered(tmp_path):
    root, proj = build_vault(tmp_path)
    assert register(root, proj).exit_code == 0
    return root, proj


def test_register_returns_envelope_and_activates_first_project(tmp_path):
    root, proj = build_vault(tmp_path)
    result = register(root, proj, "--workspace", "w1")
    assert result.exit_code == 0
    body = payload(result)
    assert (
        body["ok"] is True
        and body["schemaVersion"] == 1
        and body["command"] == "project register"
    )
    data = body["data"]
    assert data["project"] == "off2on" and data["workspaces"] == ["w1"]
    assert data["absProjectDir"] == str(proj) and len(data["profileSha256"]) == 64
    assert data["profile"] == {"found": True, "parsed": True, "projectId": "off2on"}
    assert body["meta"]["project"] == "off2on" and body["meta"]["index"] is None
    listed = payload(invoke("project", "list", "--json"))["data"]["projects"]
    assert listed == [{"id": "off2on", "name": "Demo", "active": True, "vaultOk": True}]


def test_register_path_outside_vault_is_usage_error(tmp_path):
    root, proj = build_vault(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(*register_args(root, proj, outside))
    assert result.exit_code == 2
    error = payload(result)["error"]
    assert (
        payload(result)["ok"] is False and error["code"] == "USAGE_PATH_OUTSIDE_VAULT"
    )
    assert invoke("project", "list", "--json").stdout.count("off2on") == 0


def test_register_without_profile_note_or_with_invalid_one(tmp_path):
    root, proj = build_vault(tmp_path)
    (proj / PROFILE_FIXTURE.name).unlink()
    result = register(root, proj)
    assert (
        result.exit_code == 3
        and payload(result)["error"]["code"] == "PROFILE_NOT_FOUND"
    )
    (proj / PROFILE_FIXTURE.name).write_text(
        "---\ntype: project-profile\n---\n", encoding="utf-8"
    )
    result = register(root, proj)
    assert (
        result.exit_code == 6 and payload(result)["error"]["code"] == "PROFILE_INVALID"
    )


def test_register_id_must_match_profile(tmp_path):
    root, proj = build_vault(tmp_path)
    result = register(root, proj, "--id", "other")
    assert (
        result.exit_code == 6
        and "does not match" in payload(result)["error"]["message"]
    )


def test_second_project_in_same_vault_shares_vault_id(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    other = root / "10 Projects" / "Second"
    other.mkdir()
    text = PROFILE_FIXTURE.read_text(encoding="utf-8").replace(
        'project_id: "off2on"', 'project_id: "second"'
    )
    (other / PROFILE_FIXTURE.name).write_text(text, encoding="utf-8")
    result = invoke(*register_args(root, other))
    assert result.exit_code == 0
    first = payload(invoke("project", "show", "off2on", "--json"))["data"]["project"][
        "vault_id"
    ]
    second = payload(invoke("project", "show", "second", "--json"))["data"]["project"][
        "vault_id"
    ]
    assert first == second
    assert payload(invoke("project", "use", "second", "--json"))["data"] == {
        "activeProject": "second"
    }
    listed = payload(invoke("project", "list", "--json"))["data"]["projects"]
    assert {p["id"]: p["active"] for p in listed} == {"off2on": False, "second": True}


def test_show_unknown_project_exit_3(registered):
    result = invoke("project", "show", "nope", "--json")
    assert (
        result.exit_code == 3
        and payload(result)["error"]["code"] == "PROJECT_NOT_FOUND"
    )
    assert payload(result)["error"]["details"]["registered"] == ["off2on"]


def test_show_reports_profile_and_paths(registered):
    root, proj = registered
    data = payload(invoke("project", "show", "off2on", "--json"))["data"]
    assert data["absImagesDir"] == str(root / IMAGES_REL)
    assert data["profile"]["parsed"] and data["profile"]["schema"] == "3"
    assert data["index"]["built"] is False  # P2: stats instead of null


def test_check_without_registered_project(tmp_path):
    result = invoke("check", "--json")
    assert result.exit_code == 3
    assert payload(result)["error"]["code"] == "PROJECT_NOT_REGISTERED"


def test_check_envelope_and_exit_zero_on_clean_vault(registered):
    result = invoke("check", "--project", "off2on", "--json")
    assert result.exit_code == 0
    body = payload(result)
    assert body["command"] == "check" and body["ok"] is True
    data = body["data"]
    assert data["summary"]["fail"] == 0
    assert set(data) >= {"summary", "findings", "newCodes", "stats", "scope"}
    assert {f["code"] for f in data["newCodes"]} == {"SCHEMA.OLD"}
    first = data["findings"][0]
    assert set(first) == {"level", "code", "area", "where", "message", "data"}
    assert result.stdout.count("\n") == 1  # one JSON document on stdout


def test_check_output_is_deterministic(registered):
    # meta.index reports what the refresh changed, so only the data must repeat
    a = payload(invoke("check", "--json"))
    b = payload(invoke("check", "--json"))
    assert a["data"] == b["data"] and a["warnings"] == b["warnings"]
    assert a["meta"]["index"]["filesChanged"] > 0 == b["meta"]["index"]["filesChanged"]


def test_check_fails_with_exit_1(tmp_path):
    root, proj = scenario_vault(tmp_path, "frozen_rules_edited")
    register(root, proj)
    result = invoke("check", "--json")
    assert result.exit_code == 1
    body = payload(result)
    assert body["ok"] is True  # the command ran; the gate result is the exit code
    assert body["data"]["summary"]["fail"] >= 1


def test_compat_vault_py_hides_new_codes(tmp_path):
    root, proj = scenario_vault(tmp_path, "status_tail_present")
    register(root, proj)
    plain = payload(invoke("check", "--json"))["data"]
    compat = payload(invoke("check", "--compat", "vault-py", "--json"))["data"]
    assert {"GEN.STATUS_TAIL_PRESENT", "SCHEMA.OLD"} <= {
        f["code"] for f in plain["newCodes"]
    }
    assert compat["newCodes"] == [] and compat["summary"] == plain["summary"]
    assert compat["scope"]["compat"] == "vault-py"
    bad = invoke("check", "--compat", "other", "--json")
    assert bad.exit_code == 2 and payload(bad)["error"]["code"] == "USAGE_BAD_COMPAT"


def test_level_code_and_fields_filters(tmp_path):
    root, proj = scenario_vault(tmp_path, "ev_figure_missing_and_not_embedded")
    register(root, proj)
    everything = payload(invoke("check", "--json"))["data"]
    fails = payload(invoke("check", "--level", "fail", "--json"))["data"]
    assert {f["level"] for f in fails["findings"]} == {"FAIL"}
    assert fails["summary"] == everything["summary"]  # filters never hide the gate
    one = payload(invoke("check", "--code", "EV.FIGURE_NOT_EMBEDDED", "--json"))["data"]
    assert [f["code"] for f in one["findings"]] == ["EV.FIGURE_NOT_EMBEDDED"]
    thin = payload(
        invoke(
            "check",
            "--code",
            "EV.FIGURE_UNRESOLVED",
            "--fields",
            "code,where",
            "--json",
        )
    )["data"]["findings"]
    assert thin == [
        {"code": "EV.FIGURE_UNRESOLVED", "where": "evidence/EV--PLAN-900A 演示.md"}
    ]
    bad = invoke("check", "--fields", "nope", "--json")
    assert bad.exit_code == 2 and payload(bad)["error"]["code"] == "USAGE_BAD_FIELDS"
    assert invoke("check", "--level", "loud", "--json").exit_code == 2


def test_check_plan_scope(registered):
    data = payload(invoke("check", "--plan", "PLAN-900A", "--json"))["data"]
    assert data["scope"]["plan"] == "PLAN-900A" and data["scope"]["notes"] == 5
    missing = invoke("check", "--plan", "PLAN-404", "--json")
    assert missing.exit_code == 1
    assert any(
        f["code"] == "SCHEMA.PLAN_NOT_FOUND"
        for f in payload(missing)["data"]["findings"]
    )


def test_human_output_without_json(registered):
    result = invoke("check")
    assert result.exit_code == 0
    assert "FAIL 0" in result.stdout and "LINK.ORPHAN_IMAGES" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_vault_unavailable_exit_6_with_remediation(registered, tmp_path):
    root, _ = registered
    shutil.move(str(root), str(tmp_path / "moved"))
    result = invoke("check", "--json")
    assert result.exit_code == 6
    error = payload(result)["error"]
    assert (
        error["code"] == "VAULT_UNAVAILABLE"
        and "project rebind off2on" in error["remediation"]
    )
    listed = payload(invoke("project", "list", "--json"))["data"]["projects"]
    assert listed[0]["vaultOk"] is False


def test_rebind_moves_all_paths(registered, tmp_path):
    root, proj = registered
    new_root = tmp_path / "moved"
    shutil.move(str(root), str(new_root))
    result = invoke(
        "project", "rebind", "off2on", "--vault-root", str(new_root), "--json"
    )
    assert result.exit_code == 0
    data = payload(result)["data"]
    assert data["previousVaultRoot"] == str(root)
    assert data["absProjectDir"] == str(new_root / "10 Projects" / "Demo")
    assert data["absImagesDir"] == str(new_root / IMAGES_REL)
    assert invoke("check", "--json").exit_code == 0


def test_rebind_keeps_alternates(tmp_path):
    root, proj = build_vault(tmp_path)
    assert register(root, proj, "--alternate", "D:/somewhere").exit_code == 0
    new_root = tmp_path / "moved"
    shutil.move(str(root), str(new_root))
    assert (
        invoke("project", "rebind", "off2on", "--vault-root", str(new_root)).exit_code
        == 0
    )
    shown = payload(invoke("project", "show", "off2on", "--json"))["data"]["project"]
    assert shown["vault_root_alternates"] == ["D:/somewhere"]


def test_rebind_to_missing_root_changes_nothing(registered, tmp_path):
    result = invoke(
        "project",
        "rebind",
        "off2on",
        "--vault-root",
        str(tmp_path / "nowhere"),
        "--json",
    )
    assert result.exit_code == 6
    shown = payload(invoke("project", "show", "off2on", "--json"))["data"]
    assert shown["absVaultRoot"] != str(tmp_path / "nowhere")


def test_alternate_root_reports_path_note(tmp_path):
    root, proj = build_vault(tmp_path)
    assert register(root, proj, "--alternate", str(tmp_path / "unused")).exit_code == 0
    stale = tmp_path / "relocated"
    shutil.move(str(root), str(stale))
    # point the primary root at the old location and the alternate at the new one
    from expnote.research.registry import load_registry, save_registry

    registry = load_registry()
    registry.projects["off2on"].vault_root_alternates = [str(stale)]
    save_registry(registry)
    result = invoke("check", "--json")
    assert result.exit_code == 0
    assert str(stale) in payload(result)["meta"]["pathNote"]


def test_every_scenario_is_listed_for_cli_smoke():
    assert "base" in SCENARIOS  # guards the CLI tests against an emptied table
