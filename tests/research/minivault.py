"""Mini-vault builder shared by the unit tests and the oracle parity tests.

The layout mirrors the Off2On vault (images under the fixed images directory),
so vault.py can read the same tree as the oracle.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from expnote.research.checks import rules_sha256
from expnote.research.profile import read_profile_note
from expnote.research.registry import (
    ProjectContext,
    ProjectEntry,
    ResolvedProject,
)

FIXTURES = Path(__file__).parent / "fixtures" / "off2on"
PROFILE_FIXTURE = FIXTURES / "expnote 项目配置.md"
IMAGES_REL = "40 Resources/42 Assets/Images/New Off2On RL"
PROJECT_REL = "10 Projects/Demo"

DECISION_HEADER = """---
type: decision-log
---
# 决策记录

| # | 日期 | 决定 | 理由 | 落到 | 状态 | 层 |
|---|---|---|---|---|---|---|
"""


def decision_rows(*rows: tuple) -> str:
    return "".join("| " + " | ".join(str(c) for c in row) + " |\n" for row in rows)


BASE_DECISION_ROWS = (
    (1, "2026-10-01", "无关决定", "x", "[[别的笔记]]", "有效", "研究"),
    (
        2,
        "2026-10-02",
        "PLAN-900A 批准（第一步）：开始执行",
        "用户原话",
        "[[PLAN-900A--demo]]",
        "已确认（用户 2026-10-02）",
        "研究",
    ),
    (
        3,
        "2026-10-03",
        "规则摘要确认",
        "用户原话",
        "[[执行方案--PLAN-900A S1]]",
        "已确认（用户 2026-10-03）",
        "研究",
    ),
    (
        4,
        "2026-10-04",
        "最终判读确认",
        "用户原话",
        "[[RES--PLAN-900A 判读]]",
        "confirmed（用户 2026-10-04）",
        "研究",
    ),
    (
        5,
        "2026-10-05",
        "PLAN-900A 结项",
        "用户原话",
        "[[PLAN-900A--demo]]",
        "confirmed（用户）",
        "研究",
    ),
    (6, "2026-10-05", "工程事项", "x", "[[工程决策]]", "有效", "工程"),
    (7, "2026-10-06", "旧做法", "x", "[[PLAN-900B--other]]", "已推翻（见 #8）", "研究"),
    (8, "2026-10-07", "新做法，取代 #7", "x", "[[PLAN-900B--other]]", "有效", "研究"),
)
DECISIONS = DECISION_HEADER + decision_rows(*BASE_DECISION_ROWS)

DEVIATIONS = """---
type: deviation-log
---
| 编号 | 日期 | 实验 | 偏离内容 | 对结论有效性的影响 | 处置 | 相关实验卡 |
|---|---|---|---|---|---|---|
| D-01 | 2026-10-03 | PLAN-900A S1 | x | 无 | 已关闭 | [[RES--PLAN-900A 判读]] |
| D-02 | 2026-10-04 | PLAN-900A S1 | y | 弱 | 维持 | — |
"""

PLAN = """---
schema: 2
type: plan
id: PLAN-900A
title: "PLAN-900A：演示"
status: closed
approved_decision: 2
closed_decision: 5
result: "[[RES--PLAN-900A 判读]]"
---
# PLAN-900A：演示

## 1. 问题

人写正文。
"""

EXEC_BODY = """# 执行方案

## 规则摘要（须确认）

- 判定规则：A > B 记为成立。
- n：3 个训练 seed。

## 风险

- 无
"""

EXEC_FM = """---
schema: 2
type: exec-plan
id: PLAN-900A-S1
plan: "[[PLAN-900A--demo]]"
stage: "S1"
status: draft
servers: ["6017"]
rules_decision: 3
---
"""

EV_PAGE = """---
schema: 2
type: evidence
plan: "[[PLAN-900A--demo]]"
exec_plan: "[[执行方案--PLAN-900A S1]]"
status: labelled
figures: ["o2o_demo.png"]
runs: ["0012345", "abc12345"]
appendix: "[[附表--EV--PLAN-900A 演示]]"
labelled_by: user
---
# EV--PLAN-900A 演示

## 图

**全窗口**

![[o2o_demo.png]]

## 终点表

| 臂 | 终点 |
|---|---|
| orig | 0.1 |

## 用户看图标签（用户填；执行员不填）

| 臂 | 跌落 |
|---|---|
| orig | 跌落 |

## 观察（协调方；用户标签确认后填写）

已标注后可写。
"""

EV_APPENDIX = """---
schema: 2
type: evidence-appendix
parent: "[[EV--PLAN-900A 演示]]"
plan: "[[PLAN-900A--demo]]"
runs: ["0012345"]
---
# 附表

| 臂 | 终点 |
|---|---|
| orig | 0.1 |

| 另一张 | 表 |
|---|---|
| a | b |
"""

RES = """---
schema: 2
type: result
id: RES-PLAN-900A
plan: "[[PLAN-900A--demo]]"
status: confirmed
evidence: ["[[EV--PLAN-900A 演示]]"]
decisions: ["2", "4"]
confirmed_decision: 4
deviation_impact: weak
---
# RES--PLAN-900A 判读
"""

LEGACY_EV = """---
type: evidence
date: 2026-10-20
plan: "[[PLAN-900A--demo]]"
decision: "[[决策记录]] #2"
---
# EV--PLAN-900A 旧卡

## 表 M-1：成功率

| 臂 | 终点 |
|---|---|
| orig | 0.0 |

![[o2o_demo.png]]

## 用户看图标签（用户填；执行员不填）

| 臂 | 跌落 |
|---|---|
| orig | 跌落 |
"""

# Filenames used by the base vault.
PLAN_PATH = "plans/PLAN-900A--demo.md"
EXEC_PATH = "ops/执行方案--PLAN-900A S1.md"
EV_PATH = "evidence/EV--PLAN-900A 演示.md"
APPENDIX_PATH = "ops/附表--EV--PLAN-900A 演示.md"
RES_PATH = "results/RES--PLAN-900A 判读.md"
DECISIONS_PATH = "决策记录.md"
DEVIATIONS_PATH = "ops/偏离登记.md"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _profile():
    return read_profile_note(PROFILE_FIXTURE).profile


def frozen_exec(body: str = EXEC_BODY, extra_fm: str = "") -> str:
    """The exec-plan text with status frozen and the rules hash stored."""
    digest = rules_sha256(body, _profile())
    head = EXEC_FM.replace("status: draft", "status: frozen")
    head = head.replace(
        "rules_decision: 3\n",
        f"rules_decision: 3\nrules_sha256: {digest}\nfrozen_on: 2026-10-03\n{extra_fm}",
    )
    return head + body


def build_vault(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "vault"
    proj = root / PROJECT_REL
    (root / ".obsidian").mkdir(parents=True)
    write(root / IMAGES_REL / "o2o_demo.png", "png")
    write(proj / DECISIONS_PATH, DECISIONS)
    write(proj / DEVIATIONS_PATH, DEVIATIONS)
    write(proj / PLAN_PATH, PLAN)
    write(proj / EXEC_PATH, frozen_exec())
    write(proj / EV_PATH, EV_PAGE)
    write(proj / APPENDIX_PATH, EV_APPENDIX)
    write(proj / RES_PATH, RES)
    write(proj / "别的笔记.md", "x")
    write(proj / "工程决策.md", "x")
    write(
        proj / "plans" / "PLAN-900B--other.md", "---\ntype: plan\nid: PLAN-900B\n---\n"
    )
    shutil.copy(PROFILE_FIXTURE, proj / PROFILE_FIXTURE.name)
    return root, proj


def make_context(root: Path, proj: Path) -> ProjectContext:
    note = read_profile_note(PROFILE_FIXTURE)
    images = root / IMAGES_REL
    entry = ProjectEntry(
        id="off2on",
        name="Demo",
        vault_id="test",
        vault_root=str(root),
        project_dir=str(proj),
        images_dir=str(images),
        profile_note=str(PROFILE_FIXTURE),
    )
    resolved = ResolvedProject(entry, root, proj, images, PROFILE_FIXTURE)
    return ProjectContext(resolved, note.profile, note.sha256)


# ---- scenarios: (mutation, codes that must appear) -------------------------------


def _edit(rel: str, fn: Callable[[str], str]):
    def mutate(root: Path, proj: Path) -> None:
        path = proj / rel
        path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")

    return mutate


def _replace(rel: str, old: str, new: str):
    def swap(text: str) -> str:
        assert old in text, (rel, old)
        return text.replace(old, new)

    return _edit(rel, swap)


def _put(rel: str, text: str, *, under_root: bool = False):
    def mutate(root: Path, proj: Path) -> None:
        write((root if under_root else proj) / rel, text)

    return mutate


def _delete(rel: str, *, under_root: bool = False):
    def mutate(root: Path, proj: Path) -> None:
        target = (root if under_root else proj) / rel
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    return mutate


def _all(*mutations):
    def mutate(root: Path, proj: Path) -> None:
        for step in mutations:
            step(root, proj)

    return mutate


def _frozen_edited() -> Callable[[Path, Path], None]:
    return _replace(EXEC_PATH, "A > B", "A ≥ B")


def _amended(deviation: str) -> Callable[[Path, Path], None]:
    edited = EXEC_BODY.replace("A > B", "A ≥ B")
    digest = rules_sha256(edited, _profile())
    text = (
        EXEC_FM.replace("status: draft", "status: frozen").replace(
            "rules_decision: 3\n",
            f"rules_decision: 3\nrules_sha256: {digest}\nfrozen_on: 2026-10-04\n"
            f'rules_amended_by: ["{deviation}"]\n',
        )
        + edited
    )
    return _put(EXEC_PATH, text)


LEGACY_EXEC = (
    '---\nschema: 2\ntype: exec-plan\nid: PLAN-900A-S0\nplan: "[[PLAN-900A--demo]]"\n'
    'stage: "旧执行细则"\nstatus: closed\nlegacy: true\n---\n'
    "# 旧执行细则\n\n无规则摘要。\n"
)

ESCAPED_PIPE_DECISIONS = DECISIONS + decision_rows(
    (
        123,
        "2026-10-20",
        "新规则 [[PLAN-900B--other\\|PLAN-900B]]",
        "理由",
        "[[PLAN-900B--other]]",
        "superseded by #124（用户）",
        "研究",
    ),
    (
        124,
        "2026-10-21",
        "接替",
        "x",
        "[[PLAN-900B--other]]",
        "confirmed（用户）",
        "研究",
    ),
    (125, "2026-10-22", "状态词旧写法", "x", "[[工程决策]]", "已确认（用户）", "工程"),
    (126, "2026-10-22", "列数不对", "x", "只有五列", "confirmed"),
    (2, "2026-10-22", "编号重复", "x", "y", "confirmed", "研究"),
)

DEVIATIONS_9COL = (
    DEVIATIONS.replace("| 相关实验卡 |", "| 相关实验卡 | 状态 | 发现时点 |")
    .replace("|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|---|---|")
    .replace(
        "[[RES--PLAN-900A 判读]] |", "[[RES--PLAN-900A 判读]] | closed | 见数据后 |"
    )
    .replace("| 维持 | — |", "| 维持 | — | open | later |")
)

EV_AGENT = EV_PAGE.replace("labelled_by: user", "labelled_by: agent-for-user")

SCENARIOS: dict[str, tuple[Callable[[Path, Path], None], set[str]]] = {
    "base": (lambda root, proj: None, set()),
    "res_decision_not_linking": (
        _replace(RES_PATH, "confirmed_decision: 4", "confirmed_decision: 2"),
        {"RES.DECISION_NOT_LINKED"},
    ),
    "res_no_decision": (
        _replace(RES_PATH, "confirmed_decision: 4\n", ""),
        {"RES.CONFIRM_NO_DECISION"},
    ),
    "res_no_impact": (
        _replace(RES_PATH, "deviation_impact: weak\n", ""),
        {"RES.NO_IMPACT"},
    ),
    "ev_no_appendix": (_delete(APPENDIX_PATH), {"EV.NO_APPENDIX", "LINK.UNRESOLVED"}),
    "appendix_twin_mismatch": (
        _replace(APPENDIX_PATH, "[[EV--PLAN-900A 演示]]", "[[别的笔记]]"),
        {"EV.TWIN_MISMATCH"},
    ),
    "appendix_no_parent": (
        _replace(
            APPENDIX_PATH, 'parent: "[[EV--PLAN-900A 演示]]"', 'parent: "[[不存在]]"'
        ),
        {"EV.APPENDIX_NO_PARENT", "LINK.UNRESOLVED"},
    ),
    "ev_agent_without_authorization": (
        _put(EV_PATH, EV_AGENT),
        {"EV.AUTHORIZATION_MISSING"},
    ),
    "ev_agent_authorization_row4": (
        _put(
            EV_PATH,
            EV_AGENT.replace(
                "agent-for-user", 'agent-for-user\nlabel_authorization: "#4"'
            ),
        ),
        set(),
    ),
    "ev_agent_authorization_row99": (
        _put(
            EV_PATH,
            EV_AGENT.replace(
                "agent-for-user", "agent-for-user\nlabel_authorization: 99"
            ),
        ),
        {"EV.AUTHORIZATION_UNRESOLVED"},
    ),
    "ev_agent_authorization_quote": (
        _put(
            EV_PATH,
            EV_AGENT.replace(
                "agent-for-user", 'agent-for-user\nlabel_authorization: "用户原话授权"'
            ),
        ),
        set(),
    ),
    "ev_observation_before_label_and_extra_table": (
        _edit(
            EV_PATH,
            lambda t: (
                t.replace("status: labelled", "status: rendered")
                .replace("| orig | 跌落 |", "| orig |  |")
                .replace(
                    "## 用户看图标签",
                    "| x | y |\n|---|---|\n| 1 | 2 |\n\n## 用户看图标签",
                )
            ),
        ),
        {"EV.OBSERVATION_BEFORE_LABEL", "EV.EXTRA_TABLES"},
    ),
    "ev_labelled_but_empty_table": (
        _replace(EV_PATH, "| orig | 跌落 |", "| orig |  |"),
        {"EV.LABEL_NOT_FILLED"},
    ),
    "ev_label_na_but_filled": (
        _replace(EV_PATH, "status: labelled", "status: label-na"),
        {"EV.LABEL_NA_BUT_FILLED"},
    ),
    "ev_rendered_but_filled": (
        _replace(EV_PATH, "status: labelled", "status: rendered"),
        {"EV.LABEL_FILLED_STATUS_RENDERED", "EV.OBSERVATION_BEFORE_LABEL"},
    ),
    "ev_figure_missing_and_not_embedded": (
        _replace(
            EV_PATH,
            'figures: ["o2o_demo.png"]',
            'figures: ["o2o_demo.png", "o2o_gone.png"]',
        ),
        {"EV.FIGURE_UNRESOLVED", "EV.FIGURE_NOT_EMBEDDED"},
    ),
    "ev_endpoint_not_in_appendix": (
        _replace(EV_PATH, "| orig | 0.1 |", "| orig | 0.2 |"),
        {"EV.ENDPOINT_NOT_IN_APPENDIX"},
    ),
    "ev_exec_plan_not_exec": (
        _replace(EV_PATH, '"[[执行方案--PLAN-900A S1]]"', '"[[别的笔记]]"'),
        {"EV.EXEC_PLAN_INVALID"},
    ),
    "ev_unquoted_link_and_runs": (
        _edit(
            EV_PATH,
            lambda t: t.replace(
                'plan: "[[PLAN-900A--demo]]"', "plan: [[PLAN-900A--demo]]"
            ).replace('runs: ["0012345", "abc12345"]', "runs: [0012345, abc12345]"),
        ),
        {"SCHEMA.UNQUOTED"},
    ),
    "legacy_ev_exempt": (
        _put(
            "evidence/EV--PLAN-900A 旧卡.md",
            LEGACY_EV.replace(
                "type: evidence\n",
                "schema: 2\ntype: evidence\nlegacy: true\n"
                "status: labelled\nlabelled_by: user\n",
            ),
        ),
        set(),
    ),
    "legacy_format_note": (
        _put("evidence/EV--PLAN-900A 旧卡.md", LEGACY_EV),
        {"SCHEMA.LEGACY_FORMAT"},
    ),
    "two_active_results": (
        _put(
            "results/RES--PLAN-900A 旧判读.md",
            RES.replace("RES-PLAN-900A", "RES-PLAN-900A-old"),
        ),
        {"LIFE.RES_MULTIPLE_ACTIVE", "LIFE.PLAN_RES_COUNT"},
    ),
    "plan_without_result": (
        _replace(PLAN_PATH, 'result: "[[RES--PLAN-900A 判读]]"\n', ""),
        {"LIFE.PLAN_RESULT_MISSING"},
    ),
    "plan_result_not_result_note": (
        _replace(PLAN_PATH, "[[RES--PLAN-900A 判读]]", "[[别的笔记]]"),
        {"LIFE.PLAN_RESULT_INVALID"},
    ),
    "plan_result_not_confirmed": (
        _replace(RES_PATH, "status: confirmed", "status: first-read"),
        {"LIFE.PLAN_RESULT_NOT_CONFIRMED"},
    ),
    "superseded_result_allowed": (
        _put(
            "results/RES--PLAN-900A 旧判读.md",
            RES.replace("RES-PLAN-900A", "RES-PLAN-900A-old")
            .replace("status: confirmed", "status: superseded")
            .replace(
                "deviation_impact: weak",
                'deviation_impact: weak\nsuperseded_by: "[[RES--PLAN-900A 判读]]"',
            ),
        ),
        set(),
    ),
    "superseded_result_invalid": (
        _put(
            "results/RES--PLAN-900A 旧判读.md",
            RES.replace("RES-PLAN-900A", "RES-PLAN-900A-old").replace(
                "status: confirmed", "status: superseded"
            ),
        ),
        {"RES.SUPERSEDED_INVALID"},
    ),
    "res_evidence_unresolved": (
        _replace(RES_PATH, '"[[EV--PLAN-900A 演示]]"', '"[[EV--不存在]]"'),
        {"RES.EVIDENCE_UNRESOLVED"},
    ),
    "plan_running_needs_frozen_exec": (
        _all(
            _replace(PLAN_PATH, "status: closed", "status: running"),
            _put(EXEC_PATH, EXEC_FM + EXEC_BODY),
        ),
        {"LIFE.PLAN_NO_FROZEN_EXEC"},
    ),
    "plan_readout_ev_unlabelled": (
        _all(
            _replace(PLAN_PATH, "status: closed", "status: read-out"),
            _replace(EV_PATH, "status: labelled", "status: rendered"),
        ),
        {"LIFE.PLAN_EV_UNLABELLED"},
    ),
    "plan_approved_without_decision": (
        _replace(PLAN_PATH, "approved_decision: 2\n", ""),
        {"LIFE.PLAN_NO_APPROVAL"},
    ),
    "plan_approved_by_proposed_decision": (
        _all(
            _replace(DECISIONS_PATH, "| 已确认（用户 2026-10-02） |", "| 待用户决定 |"),
        ),
        {"DEC.NOT_CONFIRMED"},
    ),
    "plan_closed_without_decision": (
        _replace(PLAN_PATH, "closed_decision: 5\n", "closed_decision: 77\n"),
        {"LIFE.PLAN_NO_CLOSING"},
    ),
    "frozen_rules_edited": (_frozen_edited(), {"EXEC.RULES_CHANGED"}),
    "frozen_rules_amended": (_amended("D-02"), set()),
    "frozen_rules_amended_unregistered": (
        _amended("D-99"),
        {"EXEC.AMEND_UNREGISTERED"},
    ),
    "exec_frozen_without_hash": (
        _put(EXEC_PATH, EXEC_FM.replace("status: draft", "status: frozen") + EXEC_BODY),
        {"LIFE.EXEC_NO_RULES_SHA"},
    ),
    "exec_frozen_without_rules_decision": (
        _replace(EXEC_PATH, "rules_decision: 3\n", ""),
        {"LIFE.EXEC_NO_RULES_DECISION"},
    ),
    "exec_without_rules_section": (
        _put(EXEC_PATH, EXEC_FM + "# 执行方案\n\n无规则。\n"),
        {"EXEC.NO_RULES_SECTION"},
    ),
    "legacy_exec_exempt": (_put("ops/执行细则--PLAN-900A 旧.md", LEGACY_EXEC), set()),
    "legacy_exec_without_flag": (
        _put(
            "ops/执行细则--PLAN-900A 旧.md", LEGACY_EXEC.replace("legacy: true\n", "")
        ),
        {"EXEC.NO_RULES_SECTION", "LIFE.EXEC_CLOSED_NOT_LEGACY"},
    ),
    "casefold_duplicate_stem_and_missing_image": (
        _all(
            _put("50 Archive/ev--plan-900a 演示.md", "dup", under_root=True),
            _put("theory/note.md", "![[o2o_missing.png]]"),
        ),
        {"NAME.DUPLICATE_STEM", "LINK.IMAGE_UNRESOLVED"},
    ),
    "duplicate_attachment": (
        _put("40 Resources/other/O2O_DEMO.png", "png", under_root=True),
        {"NAME.DUPLICATE_ATTACHMENT"},
    ),
    "reserved_char_filename": (
        _all(
            _put("evidence/EV--x #54 标定.md", "x"),
            _put("theory/n.md", "[[EV--x #54 标定]]"),
        ),
        {"NAME.RESERVED_CHAR", "LINK.UNRESOLVED"},
    ),
    "image_prefix_outside_dir": (
        _all(
            _put("40 Resources/elsewhere/o2o_stray.png", "png", under_root=True),
            _put("theory/n.md", "![[o2o_stray.png]]"),
        ),
        {"LINK.IMAGE_OUTSIDE_DIR"},
    ),
    "unresolved_link_in_code_ignored": (
        _put("theory/n.md", "`[[nope]]`\n```\n[[nope2]]\n```\n[[nope3]]"),
        {"LINK.UNRESOLVED"},
    ),
    "images_dir_missing": (
        _delete(IMAGES_REL, under_root=True),
        {"LINK.IMAGES_DIR_MISSING", "LINK.IMAGE_UNRESOLVED"},
    ),
    "frontmatter_duplicate_key": (
        _put("theory/d.md", "---\na: 1\nb: 2\na: 3\n---\nx"),
        {"FM.DUPLICATE_KEY"},
    ),
    "frontmatter_unclosed_and_fullwidth_colon": (
        _all(
            _put("theory/u.md", "---\na: 1\nbody"),
            _put("theory/c.md", "---\nstatus：draft\n---\nx"),
        ),
        {"FM.PARSE"},
    ),
    "template_note_not_checked_but_parsed": (
        _put("templates/模板--坏.md", "---\na: 1\na: 2\n---\n[[nope]]"),
        {"FM.DUPLICATE_KEY"},
    ),
    "alias_resolves_link": (
        _put("theory/alias.md", "---\naliases: [短名]\n---\nx"),
        set(),
    ),
    "path_suffix_link": (
        _put(
            "theory/p.md",
            "[[plans/PLAN-900B--other]] [[plans/nope]] "
            "![[Images/New Off2On RL/o2o_demo.png]]",
        ),
        {"LINK.UNRESOLVED"},
    ),
    "decisions_new_vocabulary_and_columns": (
        _put(DECISIONS_PATH, ESCAPED_PIPE_DECISIONS),
        {
            "DEC.STATUS_VOCAB",
            "DEC.TABLE_COLUMNS",
            "DEC.NUMBER_DUPLICATE",
            "DEC.NO_BACKREF",
        },
    ),
    "decision_superseded_without_successor": (
        _replace(DECISIONS_PATH, "已推翻（见 #8）", "已推翻（见 #99）"),
        {"DEC.SUPERSEDED_NO_SUCCESSOR"},
    ),
    "decision_no_backref_info": (
        _replace(DECISIONS_PATH, "新做法，取代 #7", "新做法"),
        {"DEC.NO_BACKREF"},
    ),
    "deviation_columns_checked": (
        _put(DEVIATIONS_PATH, DEVIATIONS_9COL),
        {"DEV.FOUND_VOCAB"},
    ),
    "deviation_bad_status": (
        _put(
            DEVIATIONS_PATH,
            DEVIATIONS_9COL.replace("| open | later |", "| maybe | 见数据前 |"),
        ),
        {"DEV.STATUS_VOCAB"},
    ),
    "duplicate_plan_id": (
        _put(
            "plans/PLAN-900A 副本.md",
            PLAN.replace("schema: 2\n", "schema: 2\n")
            .replace("closed", "draft")
            .replace("approved_decision: 2\nclosed_decision: 5\n", ""),
        ),
        {"SCHEMA.ID_DUPLICATE"},
    ),
    "archived_duplicate_plan_id_ignored": (
        _put("plans/归档/PLAN-900A 旧稿.md", PLAN.replace("closed", "draft")),
        set(),
    ),
    "unknown_keys_info": (
        _replace(PLAN_PATH, "status: closed", "status: closed\nnovel_key: 1"),
        {"SCHEMA.UNKNOWN_KEYS"},
    ),
    "bad_enum_and_int": (
        _replace(PLAN_PATH, "status: closed", "status: finished"),
        {"SCHEMA.BAD_VALUE"},
    ),
    "missing_required_field": (
        _replace(PLAN_PATH, 'title: "PLAN-900A：演示"\n', ""),
        {"SCHEMA.MISSING_FIELD"},
    ),
    "status_tail_present": (
        _edit(
            PLAN_PATH,
            lambda t: (
                t + "\n<!-- vault:status:start -->\nx\n<!-- vault:status:end -->\n"
            ),
        ),
        {"GEN.STATUS_TAIL_PRESENT"},
    ),
    "ignored_preview_folder": (
        _put("ops/_refactor_preview/预览--执行方案.md", EXEC_FM + "# 无规则\n"),
        set(),
    ),
    "bom_and_crlf_note": (
        _put("theory/crlf.md", "﻿---\r\ntype: plan\r\n---\r\nbody\r\n"),
        {"SCHEMA.LEGACY_FORMAT"},
    ),
    "nfd_filename_link": (
        _all(
            _put("theory/nfd.md", "x"),
            _put("theory/linknfd.md", "[[nfd]] [[Café]]"),
            _put("theory/Café.md", "x"),
        ),
        set(),
    ),
}

PLAN_IDS = ("PLAN-900A", "PLAN-900B", "PLAN-MISSING")


def scenario_vault(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root, proj = build_vault(tmp_path)
    SCENARIOS[name][0](root, proj)
    return root, proj


_ = re  # keep `re` available for scenario edits


# ---- read-layer fixtures (index, bundle, todos, doctor) ---------------------------


def make_workspace(name: str, *, runs=(), docs=()) -> Path:
    """An expnote workspace DB under the test data home.

    runs: dicts with id, topic, and optional status/result/started_at.
    docs: dicts with id, title, body and optional runs (ids linked through doc_runs).
    """
    from expnote.db import connect
    from expnote.workspace import default_workspace_dir

    workspace_dir = default_workspace_dir(name)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    stamp = "2026-10-01T00:00:00+00:00"
    conn = connect(workspace_dir, state_dir=workspace_dir)
    try:
        topics: dict[str, str] = {}
        for run in runs:
            topic = run["topic"]
            if topic not in topics:
                topics[topic] = f"topic{len(topics)}"
                conn.execute(
                    "INSERT INTO topics(id, moc_id, title, created_at, updated_at) "
                    "VALUES(?, 'default', ?, ?, ?)",
                    (topics[topic], topic, stamp, stamp),
                )
            conn.execute(
                "INSERT INTO runs(id, topic_id, result, status, started_at, "
                "updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    run["id"],
                    topics[topic],
                    run.get("result", ""),
                    run.get("status", "finished"),
                    run.get("started_at", stamp),
                    stamp,
                ),
            )
        for doc in docs:
            conn.execute(
                "INSERT INTO docs(id, moc_id, title, body, created_at, updated_at) "
                "VALUES(?, 'default', ?, ?, ?, ?)",
                (doc["id"], doc["title"], doc["body"], stamp, stamp),
            )
            for position, run_id in enumerate(doc.get("runs", [])):
                conn.execute(
                    "INSERT INTO doc_runs(id, doc_id, run_id, position, created_at, "
                    "updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        f"{doc['id']}-{run_id}",
                        doc["id"],
                        run_id,
                        position,
                        stamp,
                        stamp,
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return workspace_dir


def snapshot_tree(root: Path) -> dict:
    """Everything observable about a tree: files (size, mtime_ns, sha256) and dirs."""
    import hashlib

    files, dirs = {}, set()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            dirs.add(rel)
        else:
            stat = path.stat()
            files[rel] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return {"files": files, "dirs": dirs}
