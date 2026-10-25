"""The write layer: CAS, atomic replace, quiet window, audit, journal, undo."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest
from writekit import audit_records, make_context, set_mtime

from expnote.research import edits, writeback
from expnote.research.errors import ResearchError
from expnote.research.writeback import (
    FileEdit,
    apply_changes,
    plan_changes,
    sha256_bytes,
    undo_write,
)

NOTE = "---\ntype: plan\nstatus: draft\nowner: me\n---\n# Title\n\nBody line.\n"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    target = root / "notes" / "a.md"
    target.write_bytes(NOTE.encode())
    set_mtime(target, 3600)
    return root, target


def set_status(path: Path, value: str, **kw) -> FileEdit:
    return FileEdit(
        path,
        "set-key",
        "frontmatter:status",
        transform=lambda text: edits.edit_frontmatter(text, [("status", value)]),
        **kw,
    )


def reasons(call):
    with pytest.raises(ResearchError) as caught:
        call()
    return caught.value


# ---- compare-and-swap --------------------------------------------------------------


def test_second_writer_gets_cas_mismatch_with_both_hashes(vault):
    root, target = vault
    ctx = make_context(root)
    base = sha256_bytes(target.read_bytes())
    first = plan_changes(ctx, [set_status(target, "approved")])
    second = plan_changes(ctx, [set_status(target, "running")])
    apply_changes(ctx, first, command="t")
    error = reasons(lambda: apply_changes(ctx, second, command="t"))
    assert error.code == "CAS_MISMATCH" and error.exit_code == 5
    assert error.details["expectedSha256"] == base
    assert error.details["currentSha256"] == sha256_bytes(target.read_bytes())
    assert b"status: approved" in target.read_bytes()


def test_an_explicit_expected_sha256_is_checked_when_planning(vault):
    root, target = vault
    ctx = make_context(root)
    edit = set_status(target, "approved", expected_sha256="0" * 64)
    error = reasons(lambda: plan_changes(ctx, [edit]))
    assert error.code == "CAS_MISMATCH"
    assert error.details["currentSha256"] == sha256_bytes(target.read_bytes())


# ---- crash safety, busy files ------------------------------------------------


def test_a_crash_between_temp_write_and_replace_leaves_the_original(vault, monkeypatch):
    root, target = vault
    ctx = make_context(root)
    original = target.read_bytes()
    changes = plan_changes(ctx, [set_status(target, "approved")])

    def crash(src, dst):
        raise RuntimeError("process died")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash)
        with pytest.raises(RuntimeError):
            apply_changes(ctx, changes, command="t")
    assert target.read_bytes() == original
    temps = [p for p in target.parent.iterdir() if writeback.TEMP_NAME.match(p.name)]
    assert len(temps) == 1 and temps[0].name.startswith(".a.md.expnote.")
    assert temps[0].read_bytes() != original
    assert audit_records("wb") == []
    # Fresh temps are kept; a stale one is what `doctor` removes.
    assert writeback.stale_temp_files(root) == []
    set_mtime(temps[0], 2 * 3600)
    assert writeback.stale_temp_files(root) == temps
    removed = writeback.delete_paths(ctx, temps, command="doctor")
    assert removed == ["notes/" + temps[0].name] and not temps[0].exists()


def test_the_stale_temp_walk_prunes_what_the_index_prunes(vault):
    root, target = vault
    hidden = root / ".trash"
    skipped = root / "build"
    for folder in (hidden, skipped, root / "notes"):
        folder.mkdir(exist_ok=True)
        temp = folder / ".x.md.expnote.1.deadbeef.tmp"
        temp.write_text("half")
        set_mtime(temp, 2 * 3600)
    found = writeback.stale_temp_files(root, frozenset({"build"}))
    assert [p.relative_to(root).as_posix() for p in found] == [
        "notes/.x.md.expnote.1.deadbeef.tmp"
    ]
    ordinary = root / "notes" / "expnote.tmp"  # not a dot-prefixed expnote temp
    ordinary.write_text("x")
    set_mtime(ordinary, 2 * 3600)
    assert ordinary not in writeback.stale_temp_files(root)


def test_a_held_file_is_retried_then_reported_busy(vault, monkeypatch):
    root, target = vault
    ctx = make_context(root)
    original = target.read_bytes()
    changes = plan_changes(ctx, [set_status(target, "approved")])
    calls = []

    def locked(src, dst):
        calls.append(src)
        raise PermissionError(errno.EACCES, "held by another process")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", locked)
        patch.setattr(writeback, "REPLACE_BACKOFF_SECONDS", 0)
        error = reasons(lambda: apply_changes(ctx, changes, command="t"))
    assert error.code == "FILE_BUSY" and error.exit_code == 5 and error.retryable
    assert len(calls) == writeback.REPLACE_RETRIES + 1 == 4
    assert target.read_bytes() == original
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".")]


def test_a_transient_lock_succeeds_on_retry(vault, monkeypatch):
    root, target = vault
    ctx = make_context(root)
    changes = plan_changes(ctx, [set_status(target, "approved")])
    real = os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(errno.EACCES, "busy")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(writeback, "REPLACE_BACKOFF_SECONDS", 0)
    apply_changes(ctx, changes, command="t")
    assert len(attempts) == 3 and b"status: approved" in target.read_bytes()


# ---- quiet window ------------------------------------------------------------


def test_quiet_window_refuses_a_just_modified_file(vault):
    root, target = vault
    ctx = make_context(root, quiet=10)
    set_mtime(target, 2)
    changes = plan_changes(ctx, [set_status(target, "approved")])
    error = reasons(lambda: apply_changes(ctx, changes, command="t"))
    assert error.code == "RECENTLY_MODIFIED" and error.exit_code == 5
    assert error.retryable and 7000 <= error.details["retryAfterMs"] <= 8000
    assert b"status: draft" in target.read_bytes()
    set_mtime(target, 11)
    apply_changes(ctx, changes, command="t")
    assert b"status: approved" in target.read_bytes()


def test_no_quiet_overrides_and_is_audited(vault):
    root, target = vault
    ctx = make_context(root, quiet=10)
    set_mtime(target, 1)
    changes = plan_changes(ctx, [set_status(target, "approved")])
    apply_changes(ctx, changes, command="t", no_quiet=True)
    assert audit_records("wb")[-1]["payload"]["noQuiet"] is True


def test_the_window_does_not_block_our_own_follow_up_write(vault):
    root, target = vault
    ctx = make_context(root, quiet=10)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "approved")]), command="t")
    # fresh mtime, but the file is exactly what this tool last wrote
    follow_up = plan_changes(ctx, [set_status(target, "running")])
    apply_changes(ctx, follow_up, command="t")
    assert b"status: running" in target.read_bytes()
    target.write_bytes(target.read_bytes() + b"edited elsewhere\n")  # a foreign edit
    stale = plan_changes(ctx, [set_status(target, "done")])
    error = reasons(lambda: apply_changes(ctx, stale, command="t"))
    assert error.code == "RECENTLY_MODIFIED"


# ---- write only if different -------------------------------------------------


def test_identical_bytes_are_not_written(vault):
    root, target = vault
    ctx = make_context(root)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "approved")]), command="t")
    stamp = target.stat().st_mtime_ns
    events = len(audit_records("wb"))
    assert plan_changes(ctx, [set_status(target, "approved")]) == []
    # a stale plan whose result is already on disk is skipped, not re-written
    stale_plan = plan_changes(ctx, [set_status(target, "running")])
    apply_changes(ctx, stale_plan, command="t")
    stamp = target.stat().st_mtime_ns
    events = len(audit_records("wb"))
    result = apply_changes(ctx, stale_plan, command="t")
    assert result.applied == []
    assert target.stat().st_mtime_ns == stamp and len(audit_records("wb")) == events


# ---- audit, journal, undo ----------------------------------------------------


def test_audit_record_journal_and_undo_restore_exact_bytes(vault):
    root, target = vault
    ctx = make_context(root)
    before = target.read_bytes()
    changes = plan_changes(ctx, [set_status(target, "approved")])
    result = apply_changes(
        ctx, changes, command="decision set", authorization="user said so"
    )
    after = target.read_bytes()
    assert after != before
    (record,) = audit_records("wb")
    payload = record["payload"]
    assert record["type"] == "research.write" and record["id"] and record["ts"]
    assert payload["actor"] == "cli" and payload["command"] == "decision set"
    assert payload["path"] == "notes/a.md" and payload["op"] == "set-key"
    assert payload["address"] == "frontmatter:status"
    assert payload["beforeSha256"] == sha256_bytes(before)
    assert payload["afterSha256"] == sha256_bytes(after)
    assert payload["authorization"] == "user said so"
    journal = Path(payload["journal"])
    assert journal.read_bytes() == before and journal == Path(
        result.applied[0]["journal"]
    )
    assert not str(journal).startswith(str(root))  # never inside the vault
    undone = undo_write(ctx, journal)
    assert target.read_bytes() == before and len(undone.applied) == 1
    assert audit_records("wb")[-1]["payload"]["command"] == "undo"


def test_undo_refuses_when_the_file_moved_on(vault):
    root, target = vault
    ctx = make_context(root)
    changes = plan_changes(ctx, [set_status(target, "approved")])
    apply_changes(ctx, changes, command="t")
    journal = audit_records("wb")[0]["payload"]["journal"]
    target.write_bytes(target.read_bytes() + b"later edit\n")
    error = reasons(lambda: undo_write(ctx, journal))
    assert error.code == "CAS_MISMATCH"
    assert b"later edit" in target.read_bytes()


def test_old_journal_entries_are_pruned(vault):
    root, target = vault
    ctx = make_context(root)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "approved")]), command="t")
    old = Path(audit_records("wb")[0]["payload"]["journal"])
    set_mtime(old, (writeback.JOURNAL_KEEP_DAYS + 1) * 86400)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "running")]), command="t")
    assert not old.exists()


# ---- line endings and BOM ----------------------------------------------------


def test_bom_and_crlf_survive_an_edit_and_only_the_edit_changes(tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    target = root / "n.md"
    raw = NOTE.replace("\n", "\r\n").encode()
    target.write_bytes(edits.BOM + raw)
    set_mtime(target, 3600)
    ctx = make_context(root)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "approved")]), command="t")
    out = target.read_bytes()
    assert out.startswith(edits.BOM)
    assert out == edits.BOM + raw.replace(b"status: draft", b"status: approved")
    assert out.count(b"\r\n") == out.count(b"\n")


def test_lf_files_stay_lf_and_mixed_endings_are_refused(tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    lf, mixed = root / "lf.md", root / "mixed.md"
    lf.write_bytes(NOTE.encode())
    mixed.write_bytes(b"---\r\nstatus: a\n---\r\nbody\n")
    ctx = make_context(root)
    apply_changes(ctx, plan_changes(ctx, [set_status(lf, "x")]), command="t")
    assert b"\r" not in lf.read_bytes()
    error = reasons(lambda: plan_changes(ctx, [set_status(mixed, "x")]))
    assert error.code == "MIXED_LINE_ENDINGS"


def test_no_final_newline_is_kept(tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    target = root / "n.md"
    target.write_bytes(NOTE.rstrip("\n").encode())
    ctx = make_context(root)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "x")]), command="t")
    assert target.read_bytes().endswith(b"Body line.")


# ---- creating files ----------------------------------------------------------


def test_create_never_clobbers_and_makes_folders(vault):
    root, target = vault
    ctx = make_context(root)
    fresh = root / "decisions" / "new.md"
    create = FileEdit(fresh, "create", "new-file", new_text="---\na: 1\n---\n")
    apply_changes(ctx, plan_changes(ctx, [create]), command="t")
    assert fresh.read_text(encoding="utf-8") == "---\na: 1\n---\n"
    error = reasons(lambda: plan_changes(ctx, [create]))
    assert error.code == "TARGET_EXISTS" and error.exit_code == 5
    # the target appears between planning and applying: still refused
    racing = root / "decisions" / "race.md"
    planned = plan_changes(
        ctx, [FileEdit(racing, "create", "new-file", new_text="mine\n")]
    )
    racing.write_text("theirs\n", encoding="utf-8")
    error = reasons(lambda: apply_changes(ctx, planned, command="t"))
    assert error.code == "TARGET_EXISTS" and racing.read_text() == "theirs\n"
    assert not [p for p in racing.parent.iterdir() if p.name.startswith(".")]


def test_create_falls_back_when_hard_links_are_unsupported(vault, monkeypatch):
    root, _ = vault
    ctx = make_context(root)
    fresh = root / "x.md"

    def no_links(src, dst):
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr(os, "link", no_links)
    planned = plan_changes(ctx, [FileEdit(fresh, "create", "n", new_text="hi\n")])
    apply_changes(ctx, planned, command="t")
    assert fresh.read_text() == "hi\n"
    assert not [p for p in root.iterdir() if p.name.startswith(".")]


def test_paths_must_stay_inside_the_vault(vault, tmp_path):
    root, _ = vault
    ctx = make_context(root)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    error = reasons(lambda: plan_changes(ctx, [set_status(outside, "x")]))
    assert error.code == "USAGE_PATH_OUTSIDE_VAULT"
    link = root / "escape"
    link.symlink_to(tmp_path)
    error = reasons(
        lambda: plan_changes(
            ctx, [FileEdit(link / "n.md", "create", "n", new_text="x")]
        )
    )
    assert error.code == "USAGE_PATH_OUTSIDE_VAULT"


# ---- bursts and re-indexing --------------------------------------------------


def test_partial_failure_names_the_files_that_changed(vault):
    root, target = vault
    other = root / "notes" / "b.md"
    other.write_bytes(NOTE.encode())
    set_mtime(other, 3600)
    ctx = make_context(root)
    changes = plan_changes(ctx, [set_status(target, "x"), set_status(other, "x")])
    other.write_bytes(other.read_bytes() + b"moved\n")
    error = reasons(lambda: apply_changes(ctx, changes, command="t"))
    assert error.code == "CAS_MISMATCH"
    assert error.details["applied"] == ["notes/a.md"]
    assert error.details["failedPath"] == "notes/b.md"


def test_the_index_is_refreshed_once_after_the_burst(vault):
    root, target = vault
    ctx = make_context(root)
    calls = []
    ctx.reindex = lambda: calls.append(1)
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "x")]), command="t")
    assert calls == [1]
    apply_changes(ctx, plan_changes(ctx, [set_status(target, "x")]), command="t")
    assert calls == [1]  # a no-op burst touches nothing, so no refresh

    def broken():
        raise ResearchError("ENV_INDEX_LOCKED", "locked", exit_code=6)

    ctx.reindex = broken
    result = apply_changes(
        ctx, plan_changes(ctx, [set_status(target, "y")]), command="t"
    )
    assert result.warnings[0]["code"] == "REINDEX_FAILED"


def test_diff_is_unified_and_truncated(vault):
    root, target = vault
    ctx = make_context(root)
    (change,) = plan_changes(ctx, [set_status(target, "approved")])
    assert "-status: draft" in change.diff and "+status: approved" in change.diff
    assert change.diff.startswith("--- a/notes/a.md")
    big = FileEdit(root / "big.md", "create", "n", new_text="line\n" * 4000)
    (change,) = plan_changes(ctx, [big])
    assert change.diff_truncated and len(change.diff) < 7000


# ---- text edits --------------------------------------------------------------


def test_frontmatter_edit_replaces_whole_blocks_and_appends_new_keys():
    text = (
        "---\nid: a\nservers:\n  - one\n  - two\nstatus: draft  # note\n\n"
        "tags: [x]\n---\nbody\n"
    )
    out = edits.edit_frontmatter(
        text, [("servers", '["6017"]'), ("status", "frozen"), ("added", '"v"')]
    )
    assert out == (
        '---\nid: a\nservers: ["6017"]\nstatus: frozen\n\ntags: [x]\n'
        'added: "v"\n---\nbody\n'
    )
    assert edits.edit_frontmatter(out, remove={"added", "tags"}).count("tags") == 0
    with pytest.raises(ResearchError):
        edits.edit_frontmatter("no frontmatter\n", [("a", "b")])


def test_frontmatter_edit_replaces_double_and_single_quoted_keys():
    text = "---\n\"status\": \"rendered\"\n'owner': me\nid: a\n---\nbody\n"
    out = edits.edit_frontmatter(text, [("status", "labelled"), ("owner", "you")])
    assert out == "---\nstatus: labelled\nowner: you\nid: a\n---\nbody\n"


def test_frontmatter_edit_replaces_a_quoted_key_with_a_list_value():
    text = '---\n"figures":\n- "a.png"\n  - "b.png"\n"id": x\n---\n'
    assert edits.frontmatter_block(text, "figures") == [
        '"figures":', '- "a.png"', '  - "b.png"'
    ]
    out = edits.edit_frontmatter(text, [("figures", '["c.png"]')])
    assert out == '---\nfigures: ["c.png"]\n"id": x\n---\n'


def test_frontmatter_edit_removes_a_quoted_key():
    text = '---\n"a": 1\n\'b\': 2\nc: 3\n---\n'
    assert edits.edit_frontmatter(text, remove={"a", "b"}) == "---\nc: 3\n---\n"


def test_frontmatter_edit_keeps_untouched_quoted_and_bare_lines():
    text = '---\n"a": "1"\nb: 2\n\'c\': 3  # k\n---\n'
    out = edits.edit_frontmatter(text, [("b", "9")])
    assert out == '---\n"a": "1"\nb: 9\n\'c\': 3  # k\n---\n'


def test_list_items_and_indented_lines_are_not_keys():
    for line in ('- "status": x', '  "status": x', "  status: x", '"status" x'):
        assert edits.key_of(line) is None
    assert edits.key_of('"status" : x') == "status"


def test_frontmatter_edit_on_the_real_quoted_evidence_frontmatter():
    text = (
        '---\n"schema": 3\n"type": "evidence"\n'
        '"plan": "[[PLAN-002C PickCube 在线 CQL α 对比]]"\n"status": "rendered"\n'
        '"figures":\n- "o2o_x.png"\n"labelled_by": ""\n"labelled_on": ""\n'
        '"label_authorization": ""\n---\nbody\n'
    )
    out = edits.edit_frontmatter(
        text,
        [
            ("status", "labelled"),
            ("labelled_by", "user"),
            ("labelled_on", "2026-10-23"),
        ],
    )
    keys = [edits.key_of(line) for line in out.split("\n")[1 : out.index("\n---\n", 4)]]
    keys = [k for k in keys if k]
    assert len(keys) == len(set(keys)) == 8
    assert "status: labelled" in out and "labelled_by: user" in out
    assert '"figures":\n- "o2o_x.png"\n' in out and out.endswith("---\nbody\n")


def test_table_cell_edit_keeps_padding_and_escapes_pipes():
    line = "| orig |  | keep |"
    assert edits.replace_cell(line, 1, "a|b") == "| orig | a\\|b | keep |"
    assert edits.replace_cell("| a | x |", 1, "") == "| a |  |"
    with pytest.raises(ResearchError):
        edits.replace_cell(line, 7, "x")
    spans = edits.cell_spans("| a \\| b | c |")
    assert len(spans) == 2


def test_tables_inside_comments_and_fences_are_not_tables():
    lines = [
        "<!--",
        "| a | b |",
        "|---|---|",
        "-->",
        "```",
        "| c | d |",
        "|---|---|",
        "```",
        "| e | f |",
        "|---|---|",
        "| 1 | 2 |",
    ]
    (table,) = edits.find_tables(lines)
    assert table.header == ["e", "f"] and list(table.row_lines()) == [10]
