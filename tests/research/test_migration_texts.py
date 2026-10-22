"""Pure text transforms of the P4 migration: schema bump, status tail, links, names."""

from __future__ import annotations

import random

import pytest

from expnote.research import migrate_texts as T
from expnote.research.migrate_profile import OFF2ON_PROFILE_TEXT
from expnote.research.model import parse_note
from expnote.research.profile import parse_profile_text

START = "<!-- vault:status:start -->"
END = "<!-- vault:status:end -->"


def profile():
    return parse_profile_text(OFF2ON_PROFILE_TEXT).profile


# ---- schema and ids ----------------------------------------------------------------


def test_set_schema_first_inserts_first_and_replaces_in_place():
    legacy = "---\ntype: plan\nid: P\n---\nbody\n"
    assert T.set_schema_first(legacy).startswith("---\nschema: 3\ntype: plan\n")
    old = "---\ntype: plan\nschema: 2\nid: P\n---\nbody\n"
    assert T.set_schema_first(old) == "---\ntype: plan\nschema: 3\nid: P\n---\nbody\n"
    assert T.set_schema_first(T.set_schema_first(old)) == T.set_schema_first(old)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("115", "O2O-DEC-0115"),
        ("#7", "O2O-DEC-0007"),
        ("O2O-DEC-12", "O2O-DEC-0012"),
        ("O2O-DEC-0012", "O2O-DEC-0012"),
        ("用户原话", None),
        ("", None),
        (115, None),
    ],
)
def test_canonical_decision_id_forms(value, expected):
    assert T.canonical_decision_id(value, "O2O-DEC") == expected


def test_canonicalise_quotes_scalars_and_lists_and_keeps_free_text():
    text = (
        "---\nschema: 2\ntype: result\ndecisions: [2, 4]\nconfirmed_decision: 4\n"
        'authorization: "words"\n---\nbody\n'
    )
    note = parse_note(None, "n.md", text)
    out = T.canonicalise_decision_fields(
        text, note, "O2O-DEC", ("confirmed_decision",), ("decisions",)
    )
    assert 'decisions: ["O2O-DEC-0002", "O2O-DEC-0004"]' in out
    assert 'confirmed_decision: "O2O-DEC-0004"' in out
    assert 'authorization: "words"' in out
    again = T.canonicalise_decision_fields(
        out,
        parse_note(None, "n.md", out),
        "O2O-DEC",
        ("confirmed_decision",),
        ("decisions",),
    )
    assert again == out


# ---- status block: excision and the body-preservation property -----------------------


def test_excise_takes_marker_block_and_blank_lines_before_it():
    text = f"# T\n\ntext\n\n{START}\n\n## 状态\n\nrows\n\n{END}\n"
    cut = T.excise_marker_block(text, START, END)
    assert cut.text == "# T\n\ntext\n"
    assert cut.removed[0] == "" and cut.removed[-1] == END
    T.assert_pure_excision(text, cut.text, cut.removed)


def test_excise_keeps_text_after_the_block_and_ignores_quoted_markers():
    text = f"a\n\n{START}\nx\n{END}\n\nafter\n`{START}` quoted\n"
    cut = T.excise_marker_block(text, START, END)
    assert cut.text == "a\n\nafter\n`<!-- vault:status:start -->` quoted\n"


def test_excise_without_end_marker_changes_nothing():
    text = f"a\n{START}\nx\n"
    assert T.excise_marker_block(text, START, END).text == text


def test_assert_pure_excision_catches_extra_edits():
    text = f"a\n\n{START}\n{END}\nb\n"
    cut = T.excise_marker_block(text, START, END)
    with pytest.raises(AssertionError):
        T.assert_pure_excision(text, cut.text.replace("b", "B"), cut.removed)


def _random_body(rng: random.Random) -> list[str]:
    pieces = [
        "# Title",
        "",
        "plain text with [[link]] and `code`",
        "| a | b |",
        "|---|---|",
        "| 1 | 2 |",
        "```",
        "fenced",
        "```",
        "- item",
        "## Section",
        "",
        "",
    ]
    return [rng.choice(pieces) for _ in range(rng.randint(0, 30))]


def test_body_preservation_property():
    """Random bodies with a generated block: only that block (and blank lines before
    it) leaves, everything else keeps its bytes."""
    rng = random.Random(20261022)
    for _ in range(300):
        head = _random_body(rng)
        tail = _random_body(rng)
        block = [START, "", "## 状态", *_random_body(rng), END]
        text = "\n".join([*head, *block, *tail]) + "\n"
        cut = T.excise_marker_block(text, START, END)
        T.assert_pure_excision(text, cut.text, cut.removed)
        stripped_head = list(head)
        while stripped_head and not stripped_head[-1].strip():
            stripped_head.pop()
        assert cut.text == "\n".join([*stripped_head, *tail]) + "\n"
        # idempotent: nothing left to cut
        assert T.excise_marker_block(cut.text, START, END).removed == ()


# ---- links -------------------------------------------------------------------


def test_retarget_links_keeps_alias_heading_and_skips_code():
    text = (
        "[[旧]] [[旧|别名]] [[旧#节]] ![[旧]] [[其他]]\n"
        "| a |\n|---|\n| [[旧\\|表内别名]] |\n"
        "`[[旧]]`\n```\n[[旧]]\n```\n<!-- [[旧]] -->\n"
    )
    out, count = T.retarget_links(text, {"旧": "新"})
    assert count == 5
    assert out.startswith("[[新]] [[新|别名]] [[新#节]] ![[新]] [[其他]]")
    assert "[[新\\|表内别名]]" in out
    assert "`[[旧]]`" in out and "```\n[[旧]]\n```" in out and "<!-- [[旧]] -->" in out


def test_runs_to_plain_rewrites_only_projected_stems_outside_code():
    text = (
        "[[abc12345]] 与 [[abc12345|名]] 与 [[别的]] 与 ![[abc12345]]\n"
        "`[[abc12345]]`\n"
    )
    out, count = T.runs_to_plain(text, {"abc12345"})
    assert count == 2
    assert out.startswith("`abc12345` 与 名 (`abc12345`) 与 [[别的]] 与 ![[abc12345]]")
    assert out.endswith("`[[abc12345]]`\n")
    assert T.runs_to_plain(out, {"abc12345"})[1] == 0


# ---- names -------------------------------------------------------------------


def test_first_clause_respects_brackets_links_and_math():
    clause = T.first_clause
    assert clause("结论：理由", "：。；") == "结论"
    assert clause("结论（含：括号）。后文", "：。；") == "结论（含：括号）"
    assert clause("见 [[a：b]]；后文", "：。；") == "见 [[a：b]]"
    assert clause("值 $a；b$ 之后。尾", "：。；") == "值 $a；b$ 之后"
    assert clause("x" * 200, "：。；", limit=10).endswith("…")


def test_straighten_quotes_alternates_cjk_quotes():
    assert T.straighten_quotes('H-04 "排序"和"别的"') == "H-04 “排序”和“别的”"
    assert T.straighten_quotes("none") == "none"
    slug = T.result_id_slug("RES--PLAN-002A AM-L 完整四格")
    assert slug == "RES-PLAN-002A-AM-L-完整四格"


# ---- decision files and the round trip ---------------------------------------

TABLE = "\n".join(
    [
        "| # | 日期 | 决定 | 理由 | 落到 | 状态 | 层 |",
        "|---|---|---|---|---|---|---|",
        "| 1 | 2026-09-01 | 关 [[P--x\\|P]] 线：证伪 | 范数 $\\|e\\|$ | 立项 "
        "| confirmed | 研究 |",
        "| 2 | 2026-09-02 | 新；取代 #1、#9 | r | [[A]]；[[B\\|b]]；[[A]] "
        "| superseded by #3 | 工 |",
        "| 123 | 2026-10-21 | PLAN-003A 补记 | 用 | [[P--x]] "
        "| confirmed（用户 2026-10-21） | 研 |",
        "| 124 | 2026-10-21 | 无用户字样 | r | l | confirmed | 研究 |",
    ]
)


def _rows():
    return T.parse_legacy_rows(TABLE, profile().legacy_decision_columns)


def test_parse_legacy_rows_keeps_raw_escaped_cells():
    rows = _rows()
    assert [r.number for r in rows] == [1, 2, 123, 124]
    assert rows[0].cells["decision"] == "关 [[P--x\\|P]] 线：证伪"
    assert rows[0].cells["reason"] == "范数 $\\|e\\|$"


def _records(table_rows=None, plan_ids=("PLAN-003A",)):
    rows = table_rows or _rows()
    return {
        r.number: r for r in T.build_decision_records(rows, profile(), list(plan_ids))
    }


def test_decision_record_fields_and_unescaped_text():
    made = _records(plan_ids=("PLAN-003A", "PLAN-1"))[1]
    record = made.record
    assert (record["id"], record["number"], record["status"]) == (
        "O2O-DEC-0001",
        1,
        "confirmed",
    )
    assert record["legacy"] is True and record["status_raw"] == "confirmed"
    assert record["confirmed_by"] == "legacy" and record["source"] == "migrated"
    assert record["title"] == "关 [[P--x|P]] 线"
    assert record["decided"] == "关 [[P--x|P]] 线：证伪"  # pipe unescaped
    assert record["reason"] == "范数 $|e|$" and made.problems == []
    assert record["created_at"] == "2026-09-01T00:00:00Z"


def test_supersedes_targets_and_successor_are_parsed():
    made = _records()[2]
    assert made.record["superseded_by"] == 3
    assert made.record["supersedes"] == [1, 9]
    assert made.record["targets"] == ["[[A]]", "[[B]]"]
    assert made.problems == []


def test_non_legacy_rows_get_confirmation_fields_or_a_problem():
    records = _records()
    made = records[123]
    assert made.record["legacy"] is False
    assert made.record["confirmed_by"] == "user"
    assert made.record["confirmed_on"] == "2026-10-21"
    assert made.record["plans"] == ["PLAN-003A"] and made.problems == []
    assert any("who confirmed" in p for p in records[124].problems)
    assert records[124].record["confirmed_by"] is None


def test_round_trip_regenerates_every_cell_and_detects_edits():
    rows = _rows()
    records = {n: m.record for n, m in _records().items()}
    assert T.roundtrip_mismatches(rows, records) == []
    broken = {n: dict(r) for n, r in records.items()}
    broken[1]["reason"] = broken[1]["reason"].replace("范数", "范 数")
    assert T.roundtrip_mismatches(rows, broken) == ["#1: cell 'reason' differs"]
    del broken[2]
    assert "#2: not in the decision store" in T.roundtrip_mismatches(rows, broken)


def test_import_differences_name_the_immutable_columns_that_moved():
    made = _records()[1].record
    assert T.import_differences(made, dict(made)) == []
    moved = {**made, "reason": "改", "plans": ["P"], "status": "superseded"}
    assert T.import_differences(made, moved) == ["reason", "plans"]  # status moves


def test_back_reference_is_kept_when_the_successor_names_the_old_number():
    table = "\n".join(
        [
            "| # | 日期 | 决定 | 理由 | 落到 | 状态 | 层 |",
            "|---|---|---|---|---|---|---|",
            "| 6 | 2026-09-06 | 旧 | r | l | superseded by #7 | 工 |",
            "| 7 | 2026-09-07 | 新 | 见 #6 | l | confirmed | 工 |",
            "| 8 | 2026-09-08 | 旧二 | r | l | superseded by #9 | 工 |",
            "| 9 | 2026-09-09 | 新二 | r | l | confirmed | 工 |",
        ]
    )
    prof = profile()
    rows = T.parse_legacy_rows(table, prof.legacy_decision_columns)
    made = {m.number: m.record for m in T.build_decision_records(rows, prof, [])}
    assert made[7]["supersedes"] == [6]
    assert made[9]["supersedes"] == []  # never mentioned: a back-ref gap
    assert made[6]["superseded_by"] == 7
