"""`plan adopt` / `plan next-id`: a hand-written draft becomes a standard plan."""

from __future__ import annotations

import hashlib
import re

import pytest
from minivault import PLAN_PATH, snapshot_tree, write
from writekit import audit_records, edit_profile, registered_vault, run, set_mtime

from expnote.research.edits import decode_note
from expnote.research.model import parse_note

ADOPTED = "plans/PLAN-901--预热想法.md"
DRAFT = "plans/预热想法.md"
DRAFT_TEXT = (
    "# 预热想法\n\n问题：预热是否稳定？[[别的笔记]]\n\n## 范围\n\n只做基础模型。\n"
)


@pytest.fixture
def world(tmp_path):
    return registered_vault(tmp_path)


def put(proj, rel, text):
    write(proj / rel, text)
    set_mtime(proj / rel, 3600)
    return proj / rel


def adopt(note, plan_id="PLAN-901", *extra, apply=True):
    args = ["plan", "adopt", note, "--id", plan_id, *extra]
    return run(*args, *(["--apply"] if apply else []))


def front(path):
    return parse_note(path, path.name, decode_note(path.read_bytes())[0])


def adopt_events():
    return [
        r["payload"]
        for r in audit_records()
        if r["payload"]["command"] == "plan adopt"
    ]


def unadopted():
    body = run("todo", "list", "--kind", "plan-draft-unadopted")
    return body["data"]["todos"]


def failures():
    return run("check")["data"]["summary"]["fail"]


# ---- the frontmatter ---------------------------------------------------------------


def test_a_note_without_frontmatter_gets_the_template_frontmatter(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    fails = failures()
    body = adopt(DRAFT)
    assert body["_exit"] == 0, body
    assert not (proj / DRAFT).exists()
    text = (proj / ADOPTED).read_text(encoding="utf-8")
    head, rest = text.split("---\n# 预热想法", 1)
    assert re.fullmatch(
        r'---\nschema: 3\ntype: plan\nid: "PLAN-901"\ntitle: "预热想法"\n'
        r'status: draft\ncreated: "\d{4}-\d\d-\d\d"\nowner: ""\n',
        head,
    ), head
    assert "# 预热想法" + rest == DRAFT_TEXT
    data = body["data"]
    assert data["adopted"] is True and data["moved"] is True
    assert data["from"].endswith(DRAFT) and data["path"].endswith(ADOPTED)
    assert [c["key"] for c in data["frontmatterChanges"]] == [
        "schema", "type", "id", "title", "status", "created", "owner",
    ]  # fmt: skip
    assert failures() == fails


def test_partial_frontmatter_keeps_every_key_and_value(world):
    root, proj = world
    original = (
        "---\ntags: [想法, idea]\nowner: \"我\"\nsupervisor: 'Li Wei'\n"
        "status: approved\ncreated: 2026-01-02\n---\n# 计划草稿\n\n正文\n"
    )
    put(proj, "plans/草稿.md", original)
    body = adopt("草稿")
    assert body["_exit"] == 0, body
    path = proj / "plans/PLAN-901--计划草稿.md"
    text = path.read_text(encoding="utf-8")
    for line in original.split("---\n")[1].splitlines():
        assert line in text.splitlines(), line  # kept byte for byte
    note = front(path)
    assert note.fm["schema"] == "3" and note.fm["type"] == "plan"
    assert note.fm["id"] == "PLAN-901" and note.fm["title"] == "计划草稿"
    assert note.fm["status"] == "approved" and note.fm["created"] == "2026-01-02"
    assert "legacy_status" not in note.fm
    assert note.body == "# 计划草稿\n\n正文\n"
    kept = [w["code"] for w in body["warnings"]]
    assert "ADOPT_STATUS_KEPT" in kept
    keys = {c["key"] for c in body["data"]["frontmatterChanges"]}
    assert keys == {"schema", "type", "id", "title"}


def test_an_unknown_status_becomes_draft_and_is_kept_as_legacy_status(world):
    root, proj = world
    put(proj, "plans/草稿.md", "---\nstatus: 进行中\n---\n# 草稿\n")
    fails = failures()
    assert adopt("草稿")["_exit"] == 0
    note = front(proj / "plans/PLAN-901--草稿.md")
    assert note.fm["status"] == "draft" and note.fm["legacy_status"] == "进行中"
    assert failures() == fails


def test_title_comes_from_the_option_then_the_title_key_then_the_heading(world):
    root, proj = world
    put(proj, "plans/一.md", "---\ntitle: 键里的标题\n---\n# 标题行\n")
    put(proj, "plans/二.md", "# PLAN-014: 标题行: 带冒号\n")
    put(proj, "plans/三.md", "没有标题行\n")
    put(proj, "plans/四.md", "---\ntitle: 键里的标题\n---\n")
    assert adopt("一", "PLAN-901")["data"]["title"] == "键里的标题"
    assert adopt("二", "PLAN-902")["data"]["title"] == "标题行: 带冒号"
    assert (proj / "plans/PLAN-902--标题行 带冒号.md").exists()  # reserved chars
    assert adopt("三", "PLAN-903")["data"]["title"] == "三"
    assert adopt("四", "PLAN-904", "--title", "选项优先")["data"]["title"] == "选项优先"
    assert front(proj / "plans/PLAN-904--选项优先.md").fm["title"] == "选项优先"


def test_a_hyphenated_first_word_is_not_taken_for_a_plan_id(world):
    root, proj = world
    scaffold = "'^[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$'"
    edit_profile(proj, "'^PLAN-\\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*$'", scaffold)
    put(proj, "plans/一.md", "# Learning-rate warmup study\n")
    put(proj, "plans/二.md", "# PLAN-014: warmup study\n")
    put(proj, "plans/三.md", "# PLAN-015 - second study\n")
    put(proj, "plans/四.md", "# Learning-rate: the question\n")
    titles = [
        adopt(stem, f"PLAN-90{n}")["data"]["title"]
        for n, stem in enumerate("一二三四", start=1)
    ]
    assert titles == [
        "Learning-rate warmup study", "warmup study", "second study",
        "Learning-rate: the question",
    ]  # fmt: skip


def test_the_body_stays_byte_identical_with_crlf_bom_and_odd_whitespace(world):
    root, proj = world
    body = "# 草稿  \r\n\r\n\t缩进 \r\n```\r\n[[别的笔记]]  \r\n```\r\n末行无换行"
    plain = proj / "plans/无头.md"
    plain.write_bytes(body.encode())
    headed = proj / "plans/有头.md"
    headed.write_bytes(b"\xef\xbb\xbf---\r\nowner: x\r\n---\r\n" + body.encode())
    for path in (plain, headed):
        set_mtime(path, 3600)
    assert adopt("无头", "PLAN-901", "--title", "无头")["_exit"] == 0
    assert adopt("有头", "PLAN-902", "--title", "有头")["_exit"] == 0
    for stem in ("PLAN-901--无头", "PLAN-902--有头"):
        raw = (proj / f"plans/{stem}.md").read_bytes()
        assert raw.endswith(body.encode()), stem
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")
    headed_raw = (proj / "plans/PLAN-902--有头.md").read_bytes()
    assert headed_raw.startswith(b"\xef\xbb\xbf---")


# ---- moving and links ----------------------------------------------------------------


def test_the_note_moves_and_every_link_form_follows_across_the_vault(world):
    root, proj = world
    put(proj, "plans/drafts/预热想法.md", DRAFT_TEXT)
    linker = put(
        proj,
        "别的笔记.md",
        "见 [[预热想法]]、[[预热想法|别名]]、[[预热想法#范围]]、![[预热想法]]、"
        "[[plans/drafts/预热想法]]。\n\n```\n[[预热想法]]\n```\n行内 `[[预热想法]]`\n",
    )
    outside = put(
        root, "Inbox/外部.md", '---\nrelated: "[[预热想法]]"\n---\n[[预热想法]]\n'
    )
    unrelated = put(proj, "工程决策.md", "[[别的草稿]]\n")
    before = unrelated.read_bytes()
    fails = failures()
    dry = adopt("plans/drafts/预热想法.md", apply=False)
    assert dry["_exit"] == 4
    assert dry["data"]["linkEdits"] == 7 and dry["data"]["linkFiles"] == 2
    moves = [c for c in dry["data"]["changes"] if c["op"] == "rename"]
    assert len(moves) == 1 and moves[0]["address"].endswith("plans/drafts/预热想法.md")
    done = adopt("plans/drafts/预热想法.md")
    assert done["_exit"] == 0, done
    assert (proj / ADOPTED).exists()
    assert not (proj / "plans/drafts/预热想法.md").exists()
    new = "PLAN-901--预热想法"
    assert linker.read_text(encoding="utf-8") == (
        f"见 [[{new}]]、[[{new}|别名]]、[[{new}#范围]]、![[{new}]]、[[{new}]]。\n\n"
        "```\n[[预热想法]]\n```\n行内 `[[预热想法]]`\n"
    )
    assert outside.read_text(encoding="utf-8") == (
        f'---\nrelated: "[[{new}]]"\n---\n[[{new}]]\n'
    )
    assert unrelated.read_bytes() == before
    commands = {(p["op"], p["path"]) for p in adopt_events()}
    assert ("rename", f"10 Projects/Demo/{ADOPTED}") in commands
    assert {p["op"] for p in adopt_events()} == {"adopt", "retarget-links", "rename"}
    assert failures() == fails


def test_a_note_already_at_its_place_is_not_moved(world):
    root, proj = world
    put(proj, ADOPTED, DRAFT_TEXT)
    body = adopt("PLAN-901--预热想法")
    assert body["_exit"] == 0, body
    assert body["data"]["moved"] is False and body["data"]["linkEdits"] == 0
    assert {p["op"] for p in adopt_events()} == {"adopt"}
    assert front(proj / ADOPTED).fm["id"] == "PLAN-901"


def test_a_self_link_in_the_body_is_reported_not_edited(world):
    root, proj = world
    put(proj, DRAFT, "# 草稿\n\n见自己 [[预热想法]]\n")
    body = adopt(DRAFT)
    assert "ADOPT_SELF_LINKS" in [w["code"] for w in body["warnings"]]
    moved = proj / "plans/PLAN-901--草稿.md"
    assert moved.read_text(encoding="utf-8").endswith("见自己 [[预热想法]]\n")


# ---- no-op, refusals, dry run, CAS -------------------------------------------------


def test_adopting_a_standard_plan_with_its_own_id_is_a_no_op(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    assert adopt(DRAFT)["_exit"] == 0
    events = len(audit_records())
    before = snapshot_tree(root)
    again = adopt("PLAN-901--预热想法")
    assert again["_exit"] == 0 and again["data"]["alreadyStandard"] is True
    assert again["data"]["changes"] == []
    assert snapshot_tree(root) == before and len(audit_records()) == events
    other = adopt("PLAN-901--预热想法", "PLAN-902")
    assert other["_exit"] == 5 and other["error"]["code"] == "PLAN_ALREADY_STANDARD"


@pytest.mark.parametrize("bad", ["draft-1", "PLAN-", "PLAN-1 x", " PLAN-901"])
def test_an_id_that_does_not_fit_the_profile_regex_is_refused(world, bad):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    before = snapshot_tree(root)
    body = adopt(DRAFT, bad)
    assert body["_exit"] == 2 and body["error"]["code"] == "USAGE_BAD_ID"
    assert snapshot_tree(root) == before


def test_an_id_used_by_another_plan_is_refused(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    body = adopt(DRAFT, "PLAN-900A")
    assert body["_exit"] == 5 and body["error"]["code"] == "ID_EXISTS"
    assert (proj / DRAFT).exists()


def test_a_taken_target_name_is_refused(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    put(proj, ADOPTED, "# 别的内容\n")  # same file name, not a plan
    body = adopt(DRAFT)
    assert body["_exit"] == 5 and body["error"]["code"] == "TARGET_EXISTS"
    put(proj, "ops/PLAN-902--预热想法.md", "x\n")  # same name elsewhere in the project
    taken = adopt(DRAFT, "PLAN-902")
    assert taken["_exit"] == 5 and taken["error"]["code"] == "NAME_COLLISION"
    assert (proj / DRAFT).read_text(encoding="utf-8") == DRAFT_TEXT


def test_notes_that_cannot_be_adopted_are_refused(world):
    root, proj = world
    put(proj, "plans/结果.md", "---\ntype: result\n---\n# x\n")
    put(proj, "plans/坏头.md", "---\nid: [\n---\n# x\n")
    put(proj, "templates/模板.md", "# 模板\n")
    put(proj, "plans/重名.md", "# a\n")
    put(proj, "ops/重名.md", "# b\n")
    codes = {
        "plans/结果.md": (2, "USAGE_NOT_ADOPTABLE"),
        "plans/坏头.md": (2, "FRONTMATTER_INVALID"),
        "templates/模板.md": (2, "USAGE_NOT_ADOPTABLE"),
        "重名": (3, "AMBIGUOUS"),
        "plans/不存在.md": (3, "NOT_FOUND"),
    }
    for note, (exit_code, code) in codes.items():
        body = adopt(note)
        assert (body["_exit"], body["error"]["code"]) == (exit_code, code), note
    assert adopt("plans/重名.md")["_exit"] == 0  # a path settles the ambiguity


def test_note_may_be_named_by_vault_path_absolute_path_or_stem(world):
    root, proj = world
    refs = {
        "甲": "10 Projects/Demo/plans/甲.md",
        "乙": str(proj / "plans/乙.md"),
        "丙": "丙",
    }
    for number, (stem, ref) in enumerate(refs.items(), start=901):
        put(proj, f"plans/{stem}.md", f"# {stem}\n")
        assert adopt(ref, f"PLAN-{number}")["_exit"] == 0, ref


def test_dry_run_writes_nothing_and_shows_the_whole_change(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    put(proj, "别的笔记.md", "[[预热想法]]\n")
    before = snapshot_tree(root)
    events = len(audit_records())
    body = adopt(DRAFT, apply=False)
    assert body["_exit"] == 4 and body["data"]["dryRun"] is True
    assert body["data"]["applied"] is False and body["data"]["confirmCommand"]
    ops = [c["op"] for c in body["data"]["changes"]]
    assert ops == ["adopt", "retarget-links", "rename"]
    assert "+id: \"PLAN-901\"" in body["data"]["changes"][0]["unifiedDiff"]
    assert body["data"]["missingSections"] == [
        "1. Question", "2. Scope and non-goals", "3. What to test",
        "4. How to read the results", "5. Alternatives and prior evidence",
    ]  # fmt: skip
    assert snapshot_tree(root) == before and len(audit_records()) == events


def test_missing_sections_ignore_numbering_and_case(world):
    root, proj = world
    text = "# x\n\n## question\n\n### Scope and non-goals\n\n## 3. what to test\n"
    put(proj, DRAFT, text)
    missing = adopt(DRAFT, apply=False)["data"]["missingSections"]
    assert missing == [
        "4. How to read the results", "5. Alternatives and prior evidence",
    ]  # fmt: skip


def test_a_changed_note_is_a_cas_conflict(world):
    root, proj = world
    path = put(proj, DRAFT, DRAFT_TEXT)
    before = snapshot_tree(root)
    body = adopt(DRAFT, "PLAN-901", "--expected-sha256", "0" * 64)
    assert body["_exit"] == 5 and body["error"]["code"] == "CAS_MISMATCH"
    assert snapshot_tree(root) == before
    good = hashlib.sha256(path.read_bytes()).hexdigest()
    assert adopt(DRAFT, "PLAN-901", "--expected-sha256", good)["_exit"] == 0


def test_a_recently_saved_draft_waits_for_the_quiet_window(tmp_path):
    root, proj = registered_vault(tmp_path, quiet=30)
    write(proj / DRAFT, DRAFT_TEXT)  # just written, as an open editor would
    body = adopt(DRAFT)
    assert body["_exit"] == 5 and body["error"]["code"] == "RECENTLY_MODIFIED"
    assert (proj / DRAFT).exists()
    assert adopt(DRAFT, "PLAN-901", "--no-quiet")["_exit"] == 0


# ---- finishing an interrupted adopt ----------------------------------------------


def half_done(proj):
    """The state a failed rename leaves: standard frontmatter, old path, old links."""
    put(
        proj,
        DRAFT,
        '---\nschema: 3\ntype: plan\nid: "PLAN-901"\ntitle: "预热想法"\n'
        'status: draft\ncreated: "2026-10-22"\nowner: ""\n---\n' + DRAFT_TEXT,
    )
    return put(proj, "链接.md", "见 [[预热想法]] 和 [[预热想法|别名]]\n")


def test_a_retry_finishes_the_move_and_the_links_of_an_interrupted_adopt(world):
    root, proj = world
    linker = half_done(proj)
    original = (proj / DRAFT).read_bytes()
    before = snapshot_tree(root)
    dry = adopt(DRAFT, apply=False)
    assert dry["_exit"] == 4, dry
    data = dry["data"]
    assert [c["op"] for c in data["changes"]] == ["retarget-links", "rename"]
    assert data["adopted"] is False and data["moved"] is True
    assert data["frontmatterChanges"] == [] and data["linkEdits"] == 2
    assert snapshot_tree(root) == before
    done = adopt(DRAFT)
    assert done["_exit"] == 0, done
    assert (proj / ADOPTED).read_bytes() == original  # the note itself is untouched
    assert not (proj / DRAFT).exists()
    assert linker.read_text(encoding="utf-8") == (
        "见 [[PLAN-901--预热想法]] 和 [[PLAN-901--预热想法|别名]]\n"
    )
    events = len(audit_records())
    again = adopt("PLAN-901--预热想法")
    assert again["_exit"] == 0 and again["data"]["alreadyStandard"] is True
    assert again["data"]["changes"] == [] and len(audit_records()) == events
    assert run("check")["data"]["summary"]["fail"] == 0


def test_finishing_a_move_keeps_the_same_refusals(world):
    root, proj = world
    half_done(proj)
    put(proj, ADOPTED, "# 别的内容\n")
    body = adopt(DRAFT)
    assert body["_exit"] == 5 and body["error"]["code"] == "TARGET_EXISTS"
    (proj / ADOPTED).unlink()
    put(proj, "ops/PLAN-901--预热想法.md", "x\n")
    body = adopt(DRAFT)
    assert body["_exit"] == 5 and body["error"]["code"] == "NAME_COLLISION"
    assert (proj / DRAFT).exists()


def test_a_standard_plan_with_another_file_name_gets_only_the_move(world):
    root, proj = world
    body = adopt(PLAN_PATH, "PLAN-900A", apply=False)
    assert body["_exit"] == 4
    ops = [c["op"] for c in body["data"]["changes"]]
    assert ops[-1] == "rename" and set(ops[:-1]) == {"retarget-links"}
    assert body["data"]["path"].endswith("PLAN-900A--PLAN-900A：演示.md")
    assert body["data"]["frontmatterChanges"] == []


def test_the_todo_does_not_flag_standard_plans_with_other_file_names(world):
    root, proj = world
    half_done(proj)  # a standard plan outside its target name: not a draft
    assert unadopted() == []


# ---- next-id -----------------------------------------------------------------------


def test_next_id_follows_the_highest_number_and_its_padding(world):
    root, proj = world
    assert run("plan", "next-id")["data"]["id"] == "PLAN-901"
    put(proj, "plans/PLAN-1000--大.md", "---\ntype: plan\nid: PLAN-1000\n---\n")
    assert run("plan", "next-id")["data"]["id"] == "PLAN-1001"
    put(proj, "plans/PLAN-0007--小.md", "---\ntype: plan\nid: PLAN-0007\n---\n")
    assert run("plan", "next-id")["data"]["id"] == "PLAN-1001"
    body = run("plan", "next-id")
    assert "highest number plus one" in body["data"]["rule"]


def test_next_id_is_null_when_the_regex_rejects_it(world):
    root, proj = world
    edit_profile(proj, "'^PLAN-\\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*$'", "'^PLAN-\\d+[A-Z]$'")
    assert run("plan", "next-id")["data"]["id"] is None


def test_next_id_after_an_adopt_moves_on(world):
    root, proj = world
    put(proj, DRAFT, DRAFT_TEXT)
    assert run("plan", "next-id")["data"]["id"] == "PLAN-901"
    assert adopt(DRAFT)["_exit"] == 0
    assert run("plan", "next-id")["data"]["id"] == "PLAN-902"


# ---- the derived TODO --------------------------------------------------------------


def test_the_todo_appears_for_drafts_and_goes_after_the_adopt(world):
    root, proj = world
    assert unadopted() == []
    put(proj, DRAFT, DRAFT_TEXT)
    put(proj, "plans/空类型.md", "---\ntags: [x]\n---\n# 空类型\n")
    put(proj, "plans/无编号.md", "---\ntype: plan\n---\n# 无编号\n")
    items = unadopted()
    assert [i["ref"] for i in items] == [
        "plans/无编号.md", "plans/空类型.md", "plans/预热想法.md",
    ]  # fmt: skip
    item = items[2]
    assert item["owner"] == "user" and item["severity"] == "info"
    assert item["stem"] == "预热想法" and item["suggestedId"] == "PLAN-901"
    assert item["suggestedTitle"] == "预热想法" and len(item["sha256"]) == 64
    assert item["path"] == f"10 Projects/Demo/{DRAFT}"
    assert item["command"].startswith("expnote plan adopt 'plans/")
    assert "--id PLAN-901 --apply" in item["command"]
    assert adopt(DRAFT)["_exit"] == 0
    assert [i["ref"] for i in unadopted()] == ["plans/无编号.md", "plans/空类型.md"]


def test_the_todo_skips_what_is_not_a_draft_in_the_plans_folder(world):
    root, proj = world
    put(proj, "plans/归档/旧草稿.md", "# 旧草稿\n")  # archive folder
    put(proj, "plans/templates/模板.md", "# 模板\n")  # template folder
    put(proj, "plans/_refactor_preview/x.md", "# x\n")  # profile ignore glob
    put(proj, "plans/备注.md", "---\ntype: note\n---\n# 备注\n")  # another type
    put(proj, "ops/草稿.md", "# 不在 plans 里\n")
    put(proj, "plans/坏头.md", "---\nid: [\n---\n")  # check reports it
    assert unadopted() == []
    put(proj, "plans/子目录/想法.md", "# 想法\n")
    assert [i["ref"] for i in unadopted()] == ["plans/子目录/想法.md"]


def test_doctor_points_untyped_plans_at_adopt(world):
    root, proj = world
    put(proj, "ops/无编号.md", "---\ntype: plan\n---\n# 无编号\n")
    checks = {c["id"]: c for c in run("doctor")["data"]["checks"]}
    assert "expnote plan adopt" in checks["untyped-plans"]["remediation"]


def test_the_guide_lists_the_kind_and_the_adopt_workflow(world):
    guide = run("guide", "research")["data"]
    assert "plan-draft-unadopted" in guide["derivedTodoKinds"]
    steps = [s["run"] for s in guide["workflows"]["start a plan"]]
    assert any(s.startswith("expnote plan adopt") for s in steps)
    assert "adopt a draft as a plan" in " ".join(guide["principles"])
