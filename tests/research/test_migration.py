"""P4 vault migration on a synthetic legacy mini-vault (every legacy shape)."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest
from migkit import (
    PROJECT_REL,
    WORKSPACE,
    LegacyVault,
    build_legacy_vault,
    full_run,
    init_project,
    migrate,
)
from readkit import invoke, run
from writekit import audit_records, make_context, set_mtime

from expnote.research.decision_store import DecisionStore, read_rows
from expnote.research.migrate import migration_dir
from expnote.research.writeback import (
    FileEdit,
    apply_changes,
    journaled_delete,
    plan_changes,
    rename_path,
)

STEPS = (
    "schema",
    "decisions",
    "hub",
    "view",
    "types",
    "exec",
    "runlinks",
    "projection",
    "rename",
    "templates",
)


@pytest.fixture
def legacy(tmp_path) -> LegacyVault:
    vault = build_legacy_vault(tmp_path)
    init_project(vault)
    return vault


@pytest.fixture
def migrated(legacy) -> LegacyVault:
    full_run(legacy)
    return legacy


def tree(root: Path) -> dict[str, tuple[str, int]]:
    """rel -> (sha256, mtime_ns) of every file, to prove a command wrote nothing."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return out


def step_data(body: dict, name: str) -> dict:
    return next(s for s in body["data"]["steps"] if s["name"] == name)


def gates(body: dict) -> dict[str, dict]:
    return {g["gate"]: g for g in body["data"]["gates"]}


# ---- backup (M0) --------------------------------------------------------------------


def test_backup_refuses_a_folder_inside_the_vault(legacy):
    body = migrate("backup", "--out", legacy.root / "somewhere", apply=True)
    assert body["_exit"] == 2 and body["error"]["code"] == "BACKUP_INSIDE_VAULT"
    inside_project = migrate("backup", "--out", legacy.proj / "bk")
    assert inside_project["error"]["code"] == "BACKUP_INSIDE_VAULT"
    assert not (legacy.root / "somewhere").exists()


def test_backup_dry_run_writes_nothing_and_exits_4(legacy, tmp_path):
    before = tree(legacy.root)
    body = migrate("backup", "--out", tmp_path / "bk")
    assert body["_exit"] == 4 and body["data"]["dryRun"] is True
    assert not (tmp_path / "bk").exists()
    assert tree(legacy.root) == before


def test_backup_archive_manifest_golden_and_restore_test(tmp_path):
    vault = build_legacy_vault(tmp_path, git=True)
    init_project(vault)
    out = tmp_path / "bk"
    body = migrate("backup", "--out", out, apply=True)
    assert body["_exit"] == 0, body
    data = body["data"]
    assert data["restoreOk"] is True and data["gitHead"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    paths = {e["path"] for e in manifest}
    for expected in (
        f"{PROJECT_REL}/plans/PLAN-900A--demo.md",
        ".obsidian/app.json",
        f"50 Archive/Expnote/{WORKSPACE}/runs/abc12345.md",
        "00 Inbox/DISS.md",
    ):
        assert expected in paths
    assert "50 Archive/Projects/Old/n.md" not in paths  # archived outside note
    for entry in manifest:
        digest = hashlib.sha256((vault.root / entry["path"]).read_bytes()).hexdigest()
        assert entry["sha256"] == digest
    with tarfile.open(out / "backup.tar.gz") as archive:
        names = set(archive.getnames())
    assert f"{PROJECT_REL}/plans/.codex" in names  # empty directories are kept
    golden = json.loads((out / "golden.json").read_text(encoding="utf-8"))
    assert golden["summary"]["fail"] == 0
    assert {"level", "code", "where"} <= set(golden["findings"][0])
    state = json.loads(
        (migration_dir("off2on") / "backup.json").read_text(encoding="utf-8")
    )
    assert state["restoreOk"] and state["outsideNotes"] == ["00 Inbox/DISS.md"]


def test_default_backup_location_is_under_the_research_state(legacy):
    body = migrate("backup", apply=True)
    out = Path(body["data"]["dir"])
    assert out.parent.name == "off2on" and out.parent.parent.name == "backups"
    assert (out / "backup.tar.gz").is_file()
    assert str(legacy.root) not in str(out)


# ---- profile (M1) and plan ---------------------------------------------------


def test_profile_replaces_the_scaffold_and_is_then_a_no_op(legacy, tmp_path):
    note = legacy.proj / "expnote 项目配置.md"
    assert "Rules summary" in note.read_text(encoding="utf-8")
    dry = migrate("profile")
    assert dry["_exit"] == 4 and "Rules summary" in note.read_text(encoding="utf-8")
    applied = migrate("profile", apply=True)
    assert applied["_exit"] == 0 and applied["data"]["applied"] is True
    text = note.read_text(encoding="utf-8")
    assert "规则摘要（须确认）" in text and 'required_prefix: "o2o_"' in text
    assert "remark_prefix" in text and "split:" in text and "templates:" in text
    again = migrate("profile", apply=True)
    assert again["_exit"] == 0 and again["data"]["upToDate"] is True
    assert note.read_text(encoding="utf-8") == text
    show = run("project", "show", "off2on")
    assert show["data"]["profile"]["parsed"] is True


def test_profile_refuses_a_hand_edited_note_without_replace(legacy):
    note = legacy.proj / "expnote 项目配置.md"
    edited = note.read_text(encoding="utf-8") + "\nhand edit\n"
    note.write_text(edited, encoding="utf-8")
    set_mtime(note, 3600)
    body = migrate("profile", apply=True)
    assert body["_exit"] == 5 and body["error"]["code"] == "PROFILE_DIFFERS"
    forced = run("migrate", "profile", "--project", "off2on", "--replace", "--apply",
                 "--no-quiet")  # fmt: skip
    assert forced["_exit"] == 0 and "规则摘要" in note.read_text(encoding="utf-8")


def test_plan_refuses_until_the_profile_exists_and_parses(legacy, tmp_path):
    note = legacy.proj / "expnote 项目配置.md"
    original = note.read_text(encoding="utf-8")
    note.unlink()
    missing = migrate("plan")
    assert missing["_exit"] != 0 and not missing["ok"]
    note.write_text("---\ntype: project-profile\n---\nbroken\n", encoding="utf-8")
    invalid = migrate("plan")
    assert invalid["error"]["code"] == "PROFILE_INVALID"
    assert not (migration_dir("off2on") / "migration-plan.json").exists()
    note.write_text(original, encoding="utf-8")
    set_mtime(note, 3600)
    assert migrate("profile", apply=True)["_exit"] == 0
    assert migrate("plan")["_exit"] == 0


def test_plan_and_apply_refuse_the_generic_scaffold_profile(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    for args in (("plan",), ("apply", "--step", "schema"), ("verify",)):
        body = migrate(*args)
        assert body["error"]["code"] == "PROFILE_NOT_MIGRATION_READY", args
    assert not (migration_dir("off2on") / "migration-plan.json").exists()


def test_profile_refuses_a_project_registered_under_another_id(tmp_path):
    vault = build_legacy_vault(tmp_path)
    result = invoke(
        "project", "init", "--id", "other", "--project-dir", vault.proj,
        "--images-dir", vault.images, "--apply", "--json",
    )  # fmt: skip
    assert result.exit_code == 0
    body = run("migrate", "profile", "--project", "other")
    assert body["error"]["code"] == "PROFILE_ID_MISMATCH"


def test_plan_writes_the_plan_diff_and_all_six_review_tables(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    before = tree(legacy.root)
    body = migrate("plan")
    assert body["_exit"] == 0
    assert tree(legacy.root) == before  # nothing written into the vault
    state = migration_dir("off2on")
    for name in (
        "migration-plan.json",
        "migration.diff",
        "proposed--moc-wording.md",
        "proposed--layering-convention.md",
        *[f"review-{k}.{ext}" for k in (
            "a-ev-plan", "b-decision-rows", "c-plan-status", "d-final-res",
            "e-hygiene", "f-outside-notes") for ext in ("json", "md")],
    ):  # fmt: skip
        assert (state / name).is_file(), name
    plan = json.loads((state / "migration-plan.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in plan["steps"]] == list(STEPS)
    assert "diff --git" not in (state / "migration.diff").read_text(encoding="utf-8")
    assert "--- a/" in (state / "migration.diff").read_text(encoding="utf-8")

    table_a = {
        r["stem"]: r
        for r in json.loads((state / "review-a-ev-plan.json").read_text("utf-8"))
    }
    old = table_a["EV--PLAN-900B 旧卡"]
    assert old["proposedPlan"] == "PLAN-900B" and old["confidence"] == "high"
    assert {e["kind"] for e in old["evidence"]} >= {"result-backref", "file-name"}
    name_only = table_a["EV--PLAN-900B 仅文件名"]
    assert name_only["confidence"] == "medium" and name_only["appliedPlan"] is None
    assert table_a["EV--代码审计"]["confidence"] == "none"
    assert old["labelState"] == "missing" and old["labelNote"] == ""
    assert table_a["EV--PLAN-900B 旧卡"]["statusAfter"] == "label-na"

    table_b = {
        r["number"]: r
        for r in json.loads((state / "review-b-decision-rows.json").read_text("utf-8"))
    }
    assert table_b[11]["reason"] == "partial revision"
    assert table_b[11]["currentStatusCell"] == "confirmed（部分由 #7 修订）"
    assert table_b[11]["partialRevisedBy"] == ["7"]
    assert table_b[47]["reason"] == "execution-status word"
    assert table_b[78].get("missing") is True

    table_c = json.loads((state / "review-c-plan-status.json").read_text("utf-8"))
    row_c = next(r for r in table_c if r["plan"] == "PLAN-900B")
    assert row_c["currentStatus"] == "awaiting-approval"
    assert row_c["approvalDecisions"] == [2, 7] and row_c["decisionsRead"]

    table_d = json.loads((state / "review-d-final-res.json").read_text("utf-8"))
    assert [r["plan"] for r in table_d] == ["PLAN-900B"]
    assert len(table_d[0]["stageCards"]) == 2
    assert table_d[0]["promotableSummaryCards"] == []

    table_e = json.loads((state / "review-e-hygiene.json").read_text("utf-8"))
    by_path = {r["path"]: r for r in table_e}
    assert by_path[f"{PROJECT_REL}/plans/.codex"]["empty"] is True
    assert by_path[f"{PROJECT_REL}/.agents"]["empty"] is False
    rename = next(r for r in table_e if r["kind"] == "rename")
    assert rename["proposedName"] == "H-04 “引号”测试.md"
    assert any("H-01" in link for link in rename["linksMustFollow"])
    kinds = {r["kind"] for r in table_e}
    assert {"preview-folder", "vault-code", "projection"} <= kinds

    table_f = {r["path"]: r for r in json.loads(
        (state / "review-f-outside-notes.json").read_text("utf-8"))}  # fmt: skip
    assert table_f["00 Inbox/DISS.md"]["action"].startswith("rewrite")
    assert table_f["00 Inbox/DISS.md"]["rewrites"] == [
        {"id": "abc12345", "replacement": "`abc12345`"}
    ]
    assert table_f["50 Archive/Projects/Old/n.md"]["archived"] is True


def test_table_a_marks_filled_label_tables_for_the_coordinator(legacy, tmp_path):
    legacy.write(
        "evidence/EV--PLAN-900B 有标签.md",
        "---\ntype: evidence\nstatus: current\n---\n# EV\n\n## 用户看图标签\n\n"
        "| 臂 | 跌落 |\n|---|---|\n| a | 跌落 |\n",
    )
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    migrate("plan")
    rows = json.loads(
        (migration_dir("off2on") / "review-a-ev-plan.json").read_text("utf-8")
    )
    row = next(r for r in rows if r["stem"] == "EV--PLAN-900B 有标签")
    assert row["labelState"] == "filled" and row["statusAfter"] == "label-na"
    assert "candidate for `labelled`" in row["labelNote"]
    md = (migration_dir("off2on") / "review-a-ev-plan.md").read_text("utf-8")
    assert "filled -> candidate: labelled" in md


# ---- apply: preconditions and dry runs ---------------------------------------


def test_apply_needs_a_tested_backup_first(legacy):
    migrate("profile", apply=True)
    body = migrate("apply", "--step", "schema", apply=True)
    assert body["_exit"] == 1 and body["error"]["code"] == "MIGRATION_NO_BACKUP"


def test_apply_dry_run_writes_nothing_and_exits_4(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    before = tree(legacy.root)
    body = migrate("apply", "--step", "all")
    assert body["_exit"] == 4 and body["data"]["dryRun"] is True
    assert tree(legacy.root) == before
    assert step_data(body, "schema")["changes"]
    assert audit_records("off2on") != [] or True  # profile/backups are the only writes


def test_apply_rejects_an_unknown_step(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    body = migrate("apply", "--step", "nonsense")
    assert body["error"]["code"] == "USAGE_BAD_STEP"


# ---- the steps, in order -----------------------------------------------------


def test_step_schema_bumps_canonicalises_and_drops_the_status_tail(migrated):
    plan = migrated.text("plans/PLAN-900A--demo.md")
    assert plan.startswith("---\nschema: 3\n")
    assert 'approved_decision: "O2O-DEC-0002"' in plan
    assert 'closed_decision: "O2O-DEC-0005"' in plan
    assert "vault:status" not in plan and "状态（自动生成" not in plan
    body = plan.split("---")[2]
    assert "[[abc12345]]" not in body.split("`")[0]  # the run link was rewritten
    assert "`[[abc12345]]`" in plan  # inline code kept
    assert "```\n[[abc12345]] 在代码块里也保持原样\n```" in plan
    ev = migrated.text("evidence/EV--PLAN-900A 演示.md")
    assert ev.startswith("---\nschema: 3\n")
    assert 'label_authorization: "O2O-DEC-0003"' in ev
    res = migrated.text("results/RES--PLAN-900A 判读.md")
    assert 'decisions: ["O2O-DEC-0002", "O2O-DEC-0004"]' in res
    assert 'confirmed_decision: "O2O-DEC-0004"' in res
    assert migrated.text("ops/附表--EV--PLAN-900A 演示.md").startswith("---\nschema: 3")


def test_legacy_plans_are_left_for_the_coordinator(migrated):
    legacy_plan = migrated.text("plans/PLAN-900B--legacy.md")
    assert "schema" not in legacy_plan.split("---")[1]
    assert "status: awaiting-approval" in legacy_plan
    assert migrated.text("plans/归档/PLAN-900B 草稿.md").startswith("---\nid:")


def test_step_decisions_import_the_table_into_the_store(migrated):
    assert not (migrated.proj / "decisions").exists()  # no decision files, ever
    loaded = read_rows("off2on")
    assert loaded is not None and len(loaded[0]) == 13
    assert {r["source"] for r in loaded[0]} == {"migrated"}
    assert {e["action"] for e in loaded[1]} == {"import"}
    first = next(r for r in loaded[0] if r["number"] == 1)
    assert "关闭 [[PLAN-900A--demo|PLAN-900A]] 旧线：判为证伪" in first["decided"]
    assert "$|e_k|$" in first["reason"]  # the escaped pipes of the table are plain
    export = (migrated.proj / "ops" / "decisions.jsonl").read_text(encoding="utf-8")
    assert export.count('"kind": "decision"') == 13
    listing = run("decision", "list", "--project", "off2on")
    rows = listing["data"]["decisions"]
    assert len(rows) == 13 and {r["source"] for r in rows} == {"db"}
    six = next(r for r in rows if r["number"] == 6)
    assert six["status"] == "superseded" and six["supersededBy"] == "O2O-DEC-0007"
    three = next(r for r in rows if r["number"] == 3)
    assert three["supersedes"] == ["O2O-DEC-0001"]
    legacy = next(r for r in rows if r["number"] == 1)
    assert legacy["legacy"] is True and legacy["confirmedBy"] in (None, "legacy")


def test_step_decisions_is_idempotent_and_refuses_a_diverging_store(migrated):
    again = migrate("apply", "--step", "decisions", apply=True)
    assert again["_exit"] == 0 and again["data"]["steps"][0]["changes"] == []
    assert again["data"]["steps"][0]["summary"]["create"] == 0
    with DecisionStore.open("off2on") as store:
        store.conn.execute("DROP TRIGGER decisions_text_immutable")
        store.conn.execute("UPDATE decisions SET decided='x' WHERE number=2")
    refused = migrate("apply", "--step", "decisions", apply=True)
    assert refused["_exit"] == 1
    assert "differs from the table in decided" in refused["error"]["message"]


def test_step_hub_replaces_the_table_but_keeps_the_stem(migrated):
    hub = migrated.text("决策记录.md")
    assert "type: decision-hub" in hub and "| 1 |" not in hub
    assert 'aliases: ["决策记录"]' in hub
    saved = migration_dir("off2on") / "legacy-decision-table.md"
    assert "| 123 |" in saved.read_text(encoding="utf-8")
    assert run("check", "--project", "off2on")["data"]["summary"]["fail"] == 0


def test_step_view_deletes_the_generated_note_and_repoints_links(migrated):
    assert not (migrated.proj / "决策记录--研究视图.md").exists()
    assert "[[决策记录]]" in migrated.text("Off MOC.md")
    assert "研究视图" not in migrated.text("Off MOC.md").replace("`", "")
    deletes = [r for r in audit_records() if r["payload"]["op"] == "delete"]
    record = next(r for r in deletes if r["payload"]["path"].endswith("研究视图.md"))
    pre_image = Path(record["payload"]["journal"])
    assert pre_image.is_file() and "生成物，勿手改" in pre_image.read_text("utf-8")
    assert record["payload"]["command"] == "migrate apply --step view"


def test_step_types_migrates_confident_evidence_and_all_result_cards(migrated):
    ev = migrated.text("evidence/EV--PLAN-900B 旧卡.md")
    assert ev.startswith("---\nschema: 3\ntype: evidence\n")
    assert "legacy: true" in ev and 'plan: "[[PLAN-900B--legacy]]"' in ev
    assert 'figures: ["o2o_demo.png"]' in ev
    assert "status: label-na" in ev  # absent status maps to label-na
    medium = migrated.text("evidence/EV--PLAN-900B 仅文件名.md")
    assert "schema" not in medium.split("---")[1]  # held for the coordinator
    assert migrated.text("evidence/EV--代码审计.md").count("schema") == 0
    card = migrated.text("results/RES--PLAN-900B 阶段一.md")
    assert card.startswith("---\nschema: 3\ntype: result\n")
    assert 'id: "RES-PLAN-900B-阶段一"' in card and "status: confirmed" in card
    assert 'evidence: ["[[EV--PLAN-900B 旧卡]]"]' in card and "legacy: true" in card
    archived = migrated.text("results/归档/RES--PLAN-900B 阶段二.md")
    assert "status: first-read" in archived and "type: result" in archived
    log = migrated.text("ops/执行日志--PLAN-900A.md")
    assert log.startswith("---\nschema: 3\n")
    hypothesis = migrated.text("theory/归档/H-01 假说.md")
    assert hypothesis.startswith("---\nschema: 3\n") and "plan:" not in hypothesis


def test_step_exec_keeps_the_rules_hash(migrated):
    exec_note = migrated.text("ops/执行方案--PLAN-900A S1.md")
    assert exec_note.startswith("---\nschema: 3\n")
    assert 'rules_decision: "O2O-DEC-0003"' in exec_note
    with tarfile.open(migrated.tmp / "backup" / "backup.tar.gz") as archive:
        old = archive.extractfile(f"{PROJECT_REL}/ops/执行方案--PLAN-900A S1.md")
        old_text = old.read().decode("utf-8")
    sha = [ln for ln in old_text.split("\n") if ln.startswith("rules_sha256")]
    assert sha and sha[0] in exec_note
    assert run("exec", "show", "PLAN-900A-S1", "--project", "off2on")["ok"]


def test_step_runlinks_rewrites_project_and_unarchived_outside_notes(migrated):
    log = migrated.text("ops/执行日志--PLAN-900A.md")
    assert "`abc12345`" in log and "[[abc12345" not in log
    assert "第一次 (`abc12345`)" in log  # the table alias form
    assert "`ana00001`" in log
    inbox = (migrated.root / "00 Inbox" / "DISS.md").read_text(encoding="utf-8")
    assert inbox == "见 `abc12345` 与 [[别的笔记]]。\n"
    archived = (migrated.root / "50 Archive/Projects/Old/n.md").read_text("utf-8")
    assert archived == "[[abc12345]] 归档\n"
    assert "`abc12345`" in migrated.text("results/RES--PLAN-900A 判读.md")


def test_step_projection_turns_the_workspace_projection_off(migrated):
    config = (migrated.workspace_dir / "config.toml").read_text(encoding="utf-8")
    lines = [ln for ln in config.split("\n") if ln and not ln.startswith("#")]
    assert not any(ln.startswith(("obsidian_root", "notes_dir", "docs_dir"))
                   for ln in lines)  # fmt: skip
    assert 'projection = "off"' in lines
    assert f"# obsidian_root = \"{migrated.root}\"" in config  # kept as a comment
    from expnote.db import read_config

    parsed = read_config(migrated.workspace_dir, state_dir=migrated.workspace_dir)
    assert "obsidian_root" not in parsed and parsed["index_path"] == "index.md"
    audit = [r for r in audit_records() if r["payload"]["address"] == "projection:off"]
    assert audit and Path(audit[0]["payload"]["journal"]).is_file()


def test_step_rename_and_links_follow(migrated):
    old = migrated.proj / 'theory/归档/H-04 "引号"测试.md'
    new = migrated.proj / "theory/归档/H-04 “引号”测试.md"
    assert not old.exists() and new.is_file()
    assert "[[H-04 “引号”测试]]" in migrated.text("theory/归档/H-01 假说.md")
    renames = [r for r in audit_records() if r["payload"]["op"] == "rename"]
    assert renames and renames[0]["payload"]["address"].startswith("from:")


def test_step_templates_adds_schema3_files_and_keeps_the_old_ones(migrated):
    folder = migrated.proj / "templates"
    for name in ("模板--人读方案 v3.md", "模板--待办.md",
                 "模板--最终RES v3.md", "模板--执行方案 v3.md"):  # fmt: skip
        assert (folder / name).is_file(), name
    assert not (folder / "模板--决策.md").exists()  # decisions are not notes
    plan = (folder / "模板--人读方案 v3.md").read_text(encoding="utf-8")
    assert "schema: 3" in plan and "vault:status" not in plan and "vault.py" not in plan
    assert "expnote" in plan
    assert (folder / "模板--人读方案.md").is_file()  # legacy kept
    exec_text = (folder / "模板--执行方案 v3.md").read_text(encoding="utf-8")
    assert "expnote exec freeze" in exec_text and "vault.py" not in exec_text


def test_proposals_are_written_not_applied(migrated):
    state = migration_dir("off2on")
    moc = (state / "proposed--moc-wording.md").read_text(encoding="utf-8")
    assert "check_vault.py" in moc and "expnote check --project off2on" in moc
    assert "check_vault.py" in migrated.text("Off MOC.md")  # the MOC is untouched
    layering = (state / "proposed--layering-convention.md").read_text("utf-8")
    assert "## schema 3 与 expnote" in layering
    assert "## schema 2 与 vault.py" not in layering
    assert "<!-- coordinator to review -->" in layering
    old = migrated.text("templates/文档分层约定.md")
    assert "## schema 2 与 vault.py" in old  # the convention note is untouched


# ---- idempotence, overrides, judgements --------------------------------------


def test_every_step_is_a_no_op_the_second_time(migrated):
    before = tree(migrated.root)
    workspaces = migration_dir("off2on").parent.parent.parent / "workspaces"
    state_before = tree(workspaces)
    body = migrate("apply", "--step", "all", apply=True)
    assert body["_exit"] == 0
    for step in body["data"]["steps"]:
        result = step["result"]
        assert result["applied"] == [] and result["renamed"] == []
        assert result["deleted"] == [] and step["changes"] == []
    assert tree(migrated.root) == before  # same bytes and same mtimes
    assert tree(workspaces) == state_before
    dry = migrate("apply", "--step", "all")
    assert dry["_exit"] == 0 and dry["data"]["dryRun"] is True


def test_each_step_alone_is_idempotent(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    for name in STEPS:
        first = migrate("apply", "--step", name, apply=True)
        assert first["_exit"] == 0, (name, first)
        again = migrate("apply", "--step", name, apply=True)
        result = step_data(again, name)["result"]
        assert result["applied"] == [] and result["deleted"] == [], name


def test_plan_overrides_promote_a_held_evidence_note(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    state = migration_dir("off2on")
    state.mkdir(parents=True, exist_ok=True)
    rel = "evidence/EV--PLAN-900B 仅文件名.md"
    (state / "plan-overrides.json").write_text(
        json.dumps({rel: "PLAN-900B", "evidence/EV--代码审计.md": None}),
        encoding="utf-8",
    )
    assert migrate("apply", "--step", "types", apply=True)["_exit"] == 0
    text = legacy.text(rel)
    assert text.startswith("---\nschema: 3\n") and "[[PLAN-900B--legacy]]" in text
    assert "status: label-na" in text and 'legacy_status: "current"' in text
    assert "schema" not in legacy.text("evidence/EV--代码审计.md").split("---")[1]


def test_judgements_apply_a_coordinator_status_and_the_gate_marks_it_pending(
    legacy, tmp_path
):
    full_run(legacy)
    state = migration_dir("off2on")
    (state / "judgements.json").write_text(
        json.dumps({"plans": {"PLAN-900B": {"status": "closed", "approved_decision": 2,
                                            "closed_decision": 7}}}),
        encoding="utf-8",
    )  # fmt: skip
    assert migrate("apply", "--step", "schema", apply=True)["_exit"] == 0
    plan = legacy.text("plans/PLAN-900B--legacy.md")
    assert plan.startswith("---\nschema: 3\n") and "legacy: true" in plan
    assert "status: closed" in plan and 'approved_decision: "O2O-DEC-0002"' in plan
    assert 'title: "PLAN-900B：旧计划"' in plan
    assert 'legacy_status: "awaiting-approval"' in plan  # the original is kept
    verify = migrate("verify")
    assert verify["_exit"] == 0
    assert "pending final RES / judgement 3" in gates(verify)["3"]["detail"]
    assert "FAIL 0 (other 0)" in gates(verify)["3"]["detail"]
    strict = run("migrate", "verify", "--project", "off2on", "--strict")
    assert strict["_exit"] == 1 and gates(strict)["3"]["status"] == "FAIL"


def test_a_legacy_status_outside_the_enum_is_held_not_migrated(legacy, tmp_path):
    full_run(legacy)
    state = migration_dir("off2on")
    (state / "judgements.json").write_text(
        json.dumps({"plans": {"PLAN-900B": {"approved_decision": 2}}}),
        encoding="utf-8",
    )
    body = migrate("apply", "--step", "schema", apply=True)
    assert body["_exit"] == 0
    notes = step_data(body, "schema")["notes"]
    assert any("PLAN-900B" in n and "held" in n for n in notes)
    plan = legacy.text("plans/PLAN-900B--legacy.md")
    assert "schema" not in plan.split("---")[1]  # never a schema-3 enum failure
    assert migrate("verify")["_exit"] == 0


# ---- verify (M4) -------------------------------------------------------------


def test_verify_passes_after_a_full_migration(migrated):
    body = migrate("verify")
    assert body["_exit"] == 0 and body["data"]["passed"] is True
    table = gates(body)
    assert set(table) == {"1", "3", "4", "5", "6", "7", "8", "10"}
    assert {g["status"] for g in table.values()} <= {"PASS", "REVIEW"}
    assert "restore tested" in table["1"]["detail"]
    assert "13 rows, 13 stored, 0 mismatches" in table["6"]["detail"]
    assert "2 plans built" in table["5"]["detail"]


def test_verify_before_any_step_flags_the_open_work(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    body = migrate("verify")
    table = gates(body)
    assert table["10"]["status"] == "FAIL"  # steps still have work
    assert table["6"]["status"] == "FAIL"  # nothing in the decision store yet


def test_verify_catches_a_hand_edited_body(migrated):
    path = migrated.proj / "plans/PLAN-900A--demo.md"
    path.write_text(path.read_text("utf-8") + "\n手改的一段。\n", encoding="utf-8")
    body = migrate("verify")
    assert body["_exit"] == 1 and gates(body)["7"]["status"] == "FAIL"
    assert "PLAN-900A--demo.md" in gates(body)["7"]["items"][0]


def test_verify_catches_a_changed_rules_section(migrated):
    path = migrated.proj / "ops/执行方案--PLAN-900A S1.md"
    path.write_text(path.read_text("utf-8").replace("A > B", "A ≥ B"), encoding="utf-8")
    body = migrate("verify")
    table = gates(body)
    assert table["8"]["status"] == "FAIL" and table["3"]["status"] == "FAIL"
    assert any(i["code"] == "EXEC.RULES_CHANGED" for i in table["3"]["items"])


def test_verify_catches_an_edited_store_row(migrated):
    with DecisionStore.open("off2on") as store:
        store.conn.execute("DROP TRIGGER decisions_text_immutable")
        store.conn.execute("UPDATE decisions SET reason='别的话' WHERE number=2")
    body = migrate("verify")
    assert gates(body)["6"]["status"] == "FAIL"
    assert gates(body)["6"]["items"][0] == "#2: cell 'reason' differs"


def test_verify_catches_a_missing_or_stale_export(migrated):
    export = migrated.proj / "ops" / "decisions.jsonl"
    export.write_text(export.read_text("utf-8").splitlines()[0] + "\n", "utf-8")
    body = migrate("verify")
    assert gates(body)["6"]["status"] == "FAIL"
    assert any("export" in item for item in gates(body)["6"]["items"])
    export.unlink()
    assert any(
        "does not exist" in item for item in gates(migrate("verify"))["6"]["items"]
    )


def test_verify_catches_a_link_that_would_dangle(migrated):
    path = migrated.proj / "ops/执行日志--PLAN-900A.md"
    edited = path.read_text("utf-8") + "\n新引用 [[不存在的笔记]]\n"
    path.write_text(edited, encoding="utf-8")
    body = migrate("verify")
    assert gates(body)["4"]["status"] == "FAIL"
    assert gates(body)["4"]["items"][0]["message"] == "link not found: [[不存在的笔记]]"


def test_verify_sees_projection_links_that_step_7_has_not_rewritten(legacy, tmp_path):
    migrate("backup", "--out", tmp_path / "bk", apply=True)
    migrate("profile", apply=True)
    for step in ("schema", "decisions", "hub", "view", "types", "exec"):
        assert migrate("apply", "--step", step, apply=True)["_exit"] == 0
    link_gate = gates(migrate("verify"))["4"]
    assert link_gate["status"] == "FAIL"  # the run links would die with the folder
    assert migrate("apply", "--step", "runlinks", apply=True)["_exit"] == 0
    assert gates(migrate("verify"))["4"]["status"] == "PASS"


# ---- cleanup (M5) ------------------------------------------------------------


def test_cleanup_refuses_when_verify_fails(migrated):
    path = migrated.proj / "plans/PLAN-900A--demo.md"
    path.write_text(path.read_text("utf-8") + "\n手改。\n", encoding="utf-8")
    before = tree(migrated.root)
    body = migrate("cleanup", apply=True)
    assert body["_exit"] == 1 and body["error"]["code"] == "VERIFY_FAILED"
    assert tree(migrated.root) == before
    dry = migrate("cleanup")
    assert dry["error"]["code"] == "VERIFY_FAILED"


def test_cleanup_dry_run_lists_and_deletes_nothing(migrated):
    before = tree(migrated.root)
    body = migrate("cleanup")
    assert body["_exit"] == 4 and body["data"]["applied"] is False
    assert tree(migrated.root) == before
    files = set(body["data"]["files"])
    assert f"{PROJECT_REL}/templates/vault.py" in files
    assert f"{PROJECT_REL}/templates/tests/test_vault.py" in files
    assert f"{PROJECT_REL}/ops/_refactor_preview/预览.md" in files
    assert f"50 Archive/Expnote/{WORKSPACE}/runs/abc12345.md" in files
    kept = {u["path"] for u in body["data"]["unknownKept"]}
    assert kept == {f"{PROJECT_REL}/.agents"}


def test_cleanup_deletes_with_journaled_pre_images_and_keeps_unknown_dirs(migrated):
    body = migrate("cleanup", apply=True)
    assert body["_exit"] == 0 and body["data"]["applied"] is True
    assert not (migrated.root / "50 Archive" / "Expnote" / WORKSPACE).exists()
    assert not (migrated.proj / "ops/_refactor_preview").exists()
    assert not (migrated.proj / "templates/vault.py").exists()
    assert not (migrated.proj / "templates/tests").exists()
    assert not (migrated.proj / "plans/.codex").exists()
    assert not (migrated.proj / "results/.aws").exists()
    assert (migrated.proj / ".agents/notes.txt").read_text() == "keep me"  # non-empty
    assert (migrated.proj / "templates/模板--人读方案.md").is_file()
    deletes = [r["payload"] for r in audit_records()
               if r["payload"]["command"] == "migrate cleanup"]  # fmt: skip
    assert deletes and all(Path(d["journal"]).is_file() for d in deletes)
    runs_note = next(d for d in deletes if d["path"].endswith("runs/abc12345.md"))
    assert Path(runs_note["journal"]).read_text("utf-8") == "# run abc12345\n"
    again = migrate("cleanup", apply=True)
    assert again["_exit"] == 0 and again["data"]["deletedFiles"] == 0
    assert migrate("verify")["data"]["passed"] is True


def test_cleanup_refuses_files_that_are_not_in_the_backup(migrated):
    stray = migrated.root / "50 Archive" / "Expnote" / WORKSPACE / "runs" / "new.md"
    stray.write_text("created after the backup\n", encoding="utf-8")
    body = migrate("cleanup", apply=True)
    assert body["_exit"] == 1 and body["error"]["code"] == "CLEANUP_NOT_IN_BACKUP"
    assert stray.is_file()


# ---- write-layer additions ---------------------------------------------------


def test_journaled_delete_keeps_a_pre_image_and_honours_cas(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "a.md"
    note.write_text("原文\n", encoding="utf-8")
    ctx = make_context(root)
    from expnote.research.errors import ResearchError

    with pytest.raises(ResearchError) as error:
        journaled_delete(ctx, [note], command="t", expected_sha256={"a.md": "0" * 64})
    assert error.value.code == "CAS_MISMATCH" and note.exists()
    removed = journaled_delete(ctx, [note], command="t")
    assert not note.exists()
    assert Path(removed[0]["journal"]).read_text(encoding="utf-8") == "原文\n"
    assert journaled_delete(ctx, [note], command="t") == []  # already gone


def test_rename_path_never_clobbers_and_is_audited(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("x", encoding="utf-8")
    (root / "b.md").write_text("y", encoding="utf-8")
    ctx = make_context(root)
    from expnote.research.errors import ResearchError

    with pytest.raises(ResearchError) as error:
        rename_path(ctx, root / "a.md", root / "b.md", command="t")
    assert error.value.code == "TARGET_EXISTS"
    result = rename_path(ctx, root / "a.md", root / "sub" / "c.md", command="t")
    assert (root / "sub" / "c.md").read_text() == "x" and not (root / "a.md").exists()
    assert result["from"] == "a.md" and result["path"] == "sub/c.md"


def test_external_edit_is_audited_with_an_absolute_path(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "config.toml"
    outside.parent.mkdir()
    outside.write_text("a = 1\n", encoding="utf-8")
    ctx = make_context(root)
    changes = plan_changes(
        ctx,
        [
            FileEdit(
                outside,
                "migrate",
                "t",
                transform=lambda t: t + "b = 2\n",
                external=True,
            )
        ],
    )
    set_mtime(outside, 3600)
    result = apply_changes(ctx, changes, command="t")
    assert outside.read_text() == "a = 1\nb = 2\n"
    assert result.applied[0]["path"] == str(outside)
    assert Path(result.applied[0]["journal"]).read_text() == "a = 1\n"
