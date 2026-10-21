"""Index behaviour: incremental refresh, link re-resolution, conflicts, run links."""

from __future__ import annotations

import fcntl
import json
import sqlite3

import pytest
from minivault import (
    EV_PATH,
    PLAN_PATH,
    build_vault,
    make_context,
    make_workspace,
    write,
)
from readkit import data, index_rows, register, run, vault_id

from expnote.research import index as index_module
from expnote.research.extract import parse_todo_lines
from expnote.research.index import index_path


@pytest.fixture
def project(tmp_path):
    root, proj = build_vault(tmp_path)
    register(root, proj)
    return root, proj


def resolutions(note_suffix: str = "引用.md") -> dict[str, str]:
    """target -> resolution of the links written in the note ending with the suffix."""
    rows = index_rows(
        vault_id(),
        "SELECT l.target, l.resolution FROM links l "
        "JOIN files f ON f.id = l.src_file_id WHERE f.rel LIKE ?",
        "%" + note_suffix,
    )
    return {r["target"]: r["resolution"] for r in rows}


def reindex(*extra):
    return data("reindex", *extra)


def test_full_build_then_noop_refresh(project):
    first = reindex("--full")
    assert first["rebuilt"] is True and first["notes"] >= 9
    assert first["filesChanged"] == first["files"]
    again = reindex()
    assert again["filesChanged"] == 0 and again["rebuilt"] is False
    assert again["files"] == first["files"] and again["notes"] == first["notes"]


def test_edit_add_delete_are_picked_up(project):
    root, proj = project
    reindex()
    note = proj / "别的笔记.md"
    note.write_text("全新内容：熵项坍缩检验", encoding="utf-8")
    write(proj / "新笔记.md", "# 新\n\n另一段话 [[别的笔记]]")
    (proj / "工程决策.md").unlink()
    stats = reindex()
    assert stats["filesChanged"] == 3
    hit = data("search", "熵项坍缩")["hits"]
    assert [h["stem"] for h in hit] == ["别的笔记"]
    stems = {r["stem"] for r in index_rows(vault_id(), "SELECT stem FROM notes")}
    assert "新笔记" in stems and "工程决策" not in stems
    # the FTS row of a deleted note is gone too
    assert not data("search", "工程")["hits"] or all(
        h["stem"] != "工程决策" for h in data("search", "工程")["hits"]
    )


def test_touch_without_edit_does_not_reparse(project):
    root, proj = project
    reindex()
    path = proj / "别的笔记.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8")  # same bytes, new mtime
    stats = reindex()
    assert stats["filesChanged"] == 0


def test_rename_reresolves_links_both_ways(project):
    root, proj = project
    write(proj / "引用.md", "指向 [[别的笔记]] 与 [[Other Note]]")
    reindex()
    assert resolutions() == {"别的笔记": "file", "Other Note": "none"}
    (proj / "别的笔记.md").rename(proj / "Other Note.md")
    reindex()
    assert resolutions() == {"别的笔记": "none", "Other Note": "file"}


def test_alias_change_reresolves_links(project):
    root, proj = project
    write(proj / "引用.md", "看 [[简称]]")
    write(proj / "全称.md", "---\naliases: [简称]\n---\n正文")
    reindex()
    assert resolutions() == {"简称": "alias"}
    write(proj / "全称.md", "---\naliases: [别称]\n---\n正文")
    reindex()
    assert resolutions() == {"简称": "none"}
    write(proj / "全称.md", "---\naliases: [简称]\n---\n正文")
    reindex()
    assert resolutions() == {"简称": "alias"}


def test_conflict_files_are_excluded_everywhere(project):
    root, proj = project
    write(proj / "别的笔记.sync-conflict-20261021.md", "冲突副本 独有词汇冲突")
    write(proj / "别的笔记 (conflict 2026).md", "第二个冲突")
    write(proj / "ops/note conflicted copy.md", "第三个")
    stats = reindex()
    assert stats["conflictFiles"] == 3
    assert not data("search", "独有词汇冲突")["hits"]
    notes = {r["stem"] for r in index_rows(vault_id(), "SELECT stem FROM notes")}
    assert not any("conflict" in s for s in notes)
    # excluded from uniqueness: no NAME.DUPLICATE_STEM from the copies
    codes = {f["code"] for f in data("check")["findings"]}
    assert "NAME.DUPLICATE_STEM" not in codes
    todo_kinds = {
        t["kind"] for t in data("todo", "list", "--kind", "sync-conflict-file")["todos"]
    }
    assert todo_kinds == {"sync-conflict-file"}
    checks = {c["id"]: c for c in data("doctor")["checks"]}
    assert (
        checks["sync-conflicts"]["status"] == "warn"
        and checks["sync-conflicts"]["count"] == 3
    )


def test_profile_conflict_globs_extend_the_defaults(project):
    root, proj = project
    profile = proj / "expnote 项目配置.md"
    text = profile.read_text(encoding="utf-8").replace(
        '"* (conflict *)*"]', '"* (conflict *)*", "*.orig.md"]'
    )
    profile.write_text(text, encoding="utf-8")
    write(proj / "别的笔记.orig.md", "x")
    assert reindex()["conflictFiles"] == 1


def test_trigram_search_and_like_fallback(project):
    root, proj = project
    write(proj / "笔记甲.md", "# 甲\n\n在线初期的熵项会推动策略坍缩。\n")
    found = run("search", "熵项会推动")
    assert found["data"]["matchMode"] == "fts"
    assert [h["stem"] for h in found["data"]["hits"]] == ["笔记甲"]
    hit = found["data"]["hits"][0]
    assert hit["line"] == 3 and hit["snippet"].startswith(hit["path"] + ":3: ")
    short = run("search", "坍缩")
    assert short["data"]["matchMode"] == "like"
    assert [h["stem"] for h in short["data"]["hits"]] == ["笔记甲"]
    assert run("search", "%")["data"]["hits"] == []  # LIKE wildcards are escaped


def test_index_schema_mismatch_rebuilds_automatically(project):
    root, proj = project
    reindex()
    path = index_path(vault_id())
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='0' WHERE key='index_schema'")
    conn.commit()
    conn.close()
    stale = run("check", "--no-refresh")
    assert stale["_exit"] == 6 and stale["error"]["code"] == "INDEX_SCHEMA"
    stats = reindex()
    assert stats["rebuilt"] is True
    assert run("check", "--no-refresh")["_exit"] == 0


def test_no_refresh_without_an_index_is_an_environment_error(project):
    body = run("check", "--no-refresh")
    assert body["_exit"] == 6 and body["error"]["code"] == "INDEX_MISSING"
    assert body["error"]["remediation"] == "expnote reindex"


def test_writer_lock_times_out(project, monkeypatch):
    root, proj = project
    reindex()
    directory = index_path(vault_id()).parent
    monkeypatch.setattr(index_module, "LOCK_TIMEOUT_SECONDS", 0.3)
    with open(directory / "index.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        body = run("reindex")
    assert body["_exit"] == 6 and body["error"]["code"] == "ENV_INDEX_LOCKED"
    assert body["error"]["retryable"] is True


def test_dot_directories_are_not_walked(project):
    root, proj = project
    write(proj / ".agents/skill.md", "# hidden 隐藏词汇")
    reindex()
    assert not data("search", "隐藏词汇")["hits"]


def test_hub_form_refs_follow_the_profile_hub_stem(project):
    root, proj = project
    write(proj / "引用.md", "依据 [[决策记录]] #3、#4")
    reindex()
    refs = index_rows(
        vault_id(), "SELECT number, form FROM decision_refs ORDER BY number"
    )
    assert [(r["number"], r["form"]) for r in refs if r["form"] == "hub"] == [
        (3, "hub"),
        (4, "hub"),
    ]


def test_decision_refs_cover_fields_id_links_and_hub_form(project):
    root, proj = project
    write(
        proj / "引用.md",
        '---\napproved_decision: "O2O-DEC-0125"\nlabel_authorization: "用户原话"\n---\n'
        "正文 [[O2O-DEC-0126--结项]] 与 `[[O2O-DEC-0999]]` 以及 "
        "[[决策记录]] #81、#83、#84。\n"
        "裸 #77 不算。",
    )
    reindex()
    refs = index_rows(
        vault_id(),
        "SELECT r.number, r.form, r.line FROM decision_refs r "
        "JOIN files f ON f.id = r.src_file_id "
        "WHERE f.rel LIKE '%引用.md' ORDER BY r.number",
    )
    got = {(r["number"], r["form"]) for r in refs}
    assert got == {
        (125, "field"),
        (126, "id-link"),
        (81, "hub"),
        (83, "hub"),
        (84, "hub"),
    }
    assert {r["line"] for r in refs if r["form"] == "hub"} == {5}


def test_run_and_doc_links_resolve_against_the_workspace(tmp_path):
    root, proj = build_vault(tmp_path)
    make_workspace(
        "w1",
        runs=[{"id": "abcd1234", "topic": "T"}],
        docs=[
            {"id": "doc00001", "title": "分析", "body": "正文", "runs": ["abcd1234"]}
        ],
    )
    register(root, proj, "--workspace", "w1")
    write(proj / "引用.md", "跑 [[abcd1234]] 见 [[doc00001]] 但 [[zzzz9999]] 不存在")
    stats = run("reindex")
    assert stats["warnings"] == []
    sql = (
        "SELECT target, resolution FROM links l JOIN files f ON f.id=l.src_file_id "
        "WHERE f.rel LIKE '%引用.md'"
    )
    resolved = {r["target"]: r["resolution"] for r in index_rows(vault_id(), sql)}
    assert resolved == {"abcd1234": "run", "doc00001": "doc", "zzzz9999": "none"}


def test_run_links_do_not_change_check_results(tmp_path):
    root, proj = build_vault(tmp_path)
    write(proj / "引用.md", "跑 [[abcd1234]]")
    register(root, proj)
    without = data("check")
    make_workspace("w1", runs=[{"id": "abcd1234", "topic": "T"}])
    register(root, proj, "--workspace", "w1")
    with_ws = data("check")
    assert with_ws["findings"] == without["findings"]
    assert any(
        f["code"] == "LINK.UNRESOLVED" and "abcd1234" in f["message"]
        for f in with_ws["findings"]
    )


def test_unavailable_workspace_warns_and_keeps_going(tmp_path):
    root, proj = build_vault(tmp_path)
    write(proj / "引用.md", "跑 [[abcd1234]]")
    register(root, proj, "--workspace", "missing-ws")
    body = run("reindex")
    assert body["_exit"] == 0
    assert [w["code"] for w in body["warnings"]] == ["RUNS_UNAVAILABLE"]


def test_manual_todo_grammar():
    text = (
        "---\ntype: todo-list\n---\n"
        "- [ ] 有编号 [[链接]] [id:: T-0007] [plan:: P1] [due:: 2026-10-25] "
        "[by:: agent]\n"
        "  续行文字\n"
        "- [x] 已完成 [id:: T-0003] [done:: 2026-10-21]\n"
        "- [ ] 没有编号的一行\n"
        "普通文字\n"
    )
    rows = parse_todo_lines(text)
    assert [(r.todo_id, r.state, r.synthetic) for r in rows[:2]] == [
        ("T-0007", "open", False),
        ("T-0003", "done", False),
    ]
    assert rows[0].text == "有编号 [[链接]] 续行文字"
    assert rows[0].fields == {
        "id": "T-0007",
        "plan": "P1",
        "due": "2026-10-25",
        "by": "agent",
    }
    assert rows[0].line == 4
    assert (
        rows[2].synthetic
        and rows[2].todo_id.startswith("T~")
        and len(rows[2].todo_id) == 10
    )
    assert rows[2].line == 7


def test_index_matches_the_note_text_it_was_built_from(project):
    root, proj = project
    reindex()
    rows = index_rows(
        vault_id(),
        "SELECT fm_raw || body AS text FROM notes n "
        "JOIN files f ON f.id = n.file_id WHERE f.rel LIKE ?",
        "%" + PLAN_PATH,
    )
    assert rows[0]["text"] == (proj / PLAN_PATH).read_text(encoding="utf-8")
    assert (
        json.loads(
            index_rows(
                vault_id(),
                "SELECT fm_json FROM notes n "
                "JOIN files f ON f.id = n.file_id WHERE f.rel LIKE ?",
                "%" + EV_PATH,
            )[0]["fm_json"]
        )["status"]
        == "labelled"
    )


def test_context_vault_id_keys_the_index_location(tmp_path):
    root, proj = build_vault(tmp_path)
    context = make_context(root, proj)
    assert index_path(context.entry.vault_id).parent.name == "test"


def test_profile_change_rederives_decision_refs_without_touching_notes(project):
    root, proj = project
    write(proj / "引用.md", "依据 [[决策记录]] #3，另见 [[别的笔记]] #5")
    reindex()
    sql = "SELECT number FROM decision_refs WHERE form='hub' ORDER BY number"
    assert [r["number"] for r in index_rows(vault_id(), sql)] == [3]
    profile = proj / "expnote 项目配置.md"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            'decision_table: "决策记录.md"', 'decision_table: "别的笔记.md"'
        ),
        encoding="utf-8",
    )
    stats = reindex()
    assert stats["filesChanged"] == 1  # the profile note itself
    assert [r["number"] for r in index_rows(vault_id(), sql)] == [5]


def test_readers_cannot_write_the_index(project):
    reindex()
    reader = index_module.open_reader(vault_id())
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("DELETE FROM files")
    finally:
        reader.close()


def test_conflict_matching_uses_names_not_directories(project):
    root, proj = project
    write(proj / "conflict-notes/普通.md", "# 普通目录名里带 conflict 字样的笔记")
    stats = reindex()
    assert stats["conflictFiles"] == 0
