"""Check codes on the mini vault: every scenario must produce its expected codes."""

from __future__ import annotations

import pytest
from minivault import SCENARIOS, make_context, scenario_vault

from expnote.research.checks import NEW_CODES, run_checks, split_new_codes
from expnote.research.snapshot import scan_vault


def run(tmp_path, name, plan_id=None):
    root, proj = scenario_vault(tmp_path, name)
    return run_checks(scan_vault(make_context(root, proj)), plan_id)


def codes(result, level=None):
    return {f.code for f in result.findings if level is None or f.level == level}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_produces_expected_codes(tmp_path, name):
    expected = SCENARIOS[name][1]
    found = codes(run(tmp_path, name))
    assert expected <= found, (name, expected - found)


def test_base_vault_has_no_fail_and_only_info_new_codes(tmp_path):
    result = run(tmp_path, "base")
    regular, new = split_new_codes(result.findings)
    assert [f for f in result.findings if f.level == "FAIL"] == []
    assert {f.code for f in new} == {"SCHEMA.OLD"}
    assert {f.code for f in regular} <= {"LINK.ORPHAN_IMAGES", "SCHEMA.LEGACY_FORMAT"}


@pytest.mark.parametrize(
    "name",
    [
        "res_decision_not_linking",
        "ev_no_appendix",
        "ev_agent_without_authorization",
        "frozen_rules_edited",
        "plan_without_result",
        "plan_running_needs_frozen_exec",
    ],
)
def test_failing_scenarios_have_fail_findings(tmp_path, name):
    assert codes(run(tmp_path, name), "FAIL") >= SCENARIOS[name][1] - {
        "LINK.UNRESOLVED"
    }


@pytest.mark.parametrize(
    "name",
    [
        "ev_agent_authorization_row4",
        "ev_agent_authorization_quote",
        "legacy_ev_exempt",
        "legacy_exec_exempt",
        "superseded_result_allowed",
        "frozen_rules_amended",
        "archived_duplicate_plan_id_ignored",
        "ignored_preview_folder",
        "alias_resolves_link",
        "nfd_filename_link",
    ],
)
def test_clean_scenarios_have_no_fail(tmp_path, name):
    assert codes(run(tmp_path, name), "FAIL") == set()


def test_levels_of_selected_codes(tmp_path):
    level_of = {}
    for name in (
        "ev_figure_missing_and_not_embedded",
        "reserved_char_filename",
        "decision_no_backref_info",
        "duplicate_plan_id",
    ):
        for f in run(tmp_path / name, name).findings:
            level_of.setdefault(f.code, set()).add(f.level)
    assert level_of["EV.FIGURE_UNRESOLVED"] == {"FAIL"}
    assert level_of["EV.FIGURE_NOT_EMBEDDED"] == {"WARN"}
    assert level_of["NAME.RESERVED_CHAR"] == {"WARN"}
    assert level_of["DEC.NO_BACKREF"] == {"INFO"}
    assert level_of["SCHEMA.ID_DUPLICATE"] == {"WARN"}


def test_where_conventions(tmp_path):
    result = run(tmp_path, "frozen_rules_edited")
    assert any(f.where == "ops/执行方案--PLAN-900A S1.md" for f in result.findings)
    result = run(tmp_path / "d", "decision_superseded_without_successor")
    assert any(
        f.where == "#7" and f.code == "DEC.SUPERSEDED_NO_SUCCESSOR"
        for f in result.findings
    )


def test_code_in_fences_and_spans_is_not_linked(tmp_path):
    unresolved = [
        f.message
        for f in run(tmp_path, "unresolved_link_in_code_ignored").findings
        if f.code == "LINK.UNRESOLVED"
    ]
    assert unresolved == ["link not found: [[nope3]]"]


def test_plan_scope_limits_notes_but_not_vault_wide_checks(tmp_path):
    result = run(tmp_path, "two_active_results", "PLAN-900B")
    assert result.scope["notes"] == 1
    assert "LIFE.RES_MULTIPLE_ACTIVE" not in codes(result)
    missing = run(tmp_path / "m", "base", "PLAN-NOPE")
    assert any(
        f.code == "SCHEMA.PLAN_NOT_FOUND" and f.where == "PLAN-NOPE"
        for f in missing.findings
    )


def test_plan_scope_reports_legacy_decision_detail_and_open_deviations(tmp_path):
    result = run(tmp_path, "base", "PLAN-900A")
    detail = {f.where for f in result.findings if f.code == "DEC.LEGACY_STATUS"}
    assert detail == {"#2", "#3"}  # only pre-cutover wording needs a mapping note
    assert any(
        f.code == "DEV.OPEN_FOR_PLAN" and f.where == "D-02" for f in result.findings
    )
    assert any(
        f.code == "DEV.CLOSED_BY_DISPOSITION" and f.where == "D-01"
        for f in result.findings
    )


def test_findings_are_unique_and_sorted(tmp_path):
    result = run(tmp_path, "decisions_new_vocabulary_and_columns")
    keys = [f.key for f in result.findings]
    assert len(keys) == len(set(keys))
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    ranks = [order[f.level] for f in result.findings]
    assert ranks == sorted(ranks)


def test_stats_counts(tmp_path):
    stats = run(tmp_path, "base").stats
    assert stats["parse"] == {"notes": 11, "failed": 0}
    assert stats["decisions"]["count"] == 8
    assert stats["deviations"]["count"] == 2
    assert stats["images"] == {"total": 1, "orphans": 0}


def test_templates_are_parsed_but_not_link_checked(tmp_path):
    result = run(tmp_path, "template_note_not_checked_but_parsed")
    assert any(f.code == "FM.DUPLICATE_KEY" for f in result.findings)
    assert not any(f.code == "LINK.UNRESOLVED" for f in result.findings)


def test_new_codes_constant_matches_design():
    assert {
        "GEN.STATUS_TAIL_PRESENT",
        "PROSE.RAW_NUMBERS",
        "SCHEMA.OLD",
        "DEC.AUTH_WEAK",
        "DEC.BODY_CHANGED",
        "DEC.NO_OPTIONS",
    } <= NEW_CODES


def test_status_tail_marker_in_code_is_documentation(tmp_path):
    root, proj = scenario_vault(tmp_path, "base")
    plan = proj / "plans/PLAN-900A--demo.md"
    plan.write_text(
        plan.read_text(encoding="utf-8")
        + "\nUse `<!-- vault:status:start -->` blocks.\n",
        encoding="utf-8",
    )
    result = run_checks(scan_vault(make_context(root, proj)))
    assert "GEN.STATUS_TAIL_PRESENT" not in codes(result)


def test_raw_numbers_heuristic(tmp_path):
    root, proj = scenario_vault(tmp_path, "base")
    result_note = proj / "results/RES--PLAN-900A 判读.md"
    prose = (
        "\nRate improved to 0.85 and 12% after 5 steps; see PLAN-900A, D-02, #4, "
        "2026-10-21, `0.5`, v2.1, S1, and [[note 1.2]].\n"
    )
    result_note.write_text(
        result_note.read_text(encoding="utf-8") + prose, encoding="utf-8"
    )
    result = run_checks(scan_vault(make_context(root, proj)))
    hits = [f for f in result.findings if f.code == "PROSE.RAW_NUMBERS"]
    assert len(hits) == 1 and hits[0].level == "WARN"
    assert hits[0].data["samples"] == ["0.85", "12%", "5 steps"]


def test_raw_numbers_not_flagged_in_evidence_or_legacy_plan(tmp_path):
    root, proj = scenario_vault(tmp_path, "base")
    plan = proj / "plans/PLAN-900A--demo.md"
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "status: closed", "status: closed\nlegacy: true"
        )
        + "\n0.85\n",
        encoding="utf-8",
    )
    result = run_checks(scan_vault(make_context(root, proj)))
    assert "PROSE.RAW_NUMBERS" not in codes(result)
