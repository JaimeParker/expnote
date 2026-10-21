"""Differential oracle: vault.py findings mapped onto expnote research codes.

vault.py is loaded from a frozen copy of the vault (`EXPNOTE_PARITY_VAULT`) and
run in-process. `run_checks` has no side effects; the generators in `cmd_check`
are never called.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_REL = "10 Projects/New Off2On RL"
STAT = "<stat>"

# (oracle area, regex on the message, expnote code or STAT). First match wins.
RULES: list[tuple[str, str, str]] = [
    ("frontmatter 解析", r"^严格解析", STAT),
    ("frontmatter 解析", r"重复键", "FM.DUPLICATE_KEY"),
    ("frontmatter 解析", r".", "FM.PARSE"),
    ("全库文件名唯一性", r"^笔记文件名（忽略大小写）重复", "NAME.DUPLICATE_STEM"),
    ("全库文件名唯一性", r"^附件文件名（忽略大小写）重复", "NAME.DUPLICATE_ATTACHMENT"),
    ("全库文件名唯一性", r"^文件名含 Obsidian", "NAME.RESERVED_CHAR"),
    ("全库文件名唯一性", r"^全库（", STAT),
    ("链接与图片", r"^图片嵌入找不到", "LINK.IMAGE_UNRESOLVED"),
    ("链接与图片", r"^链接找不到", "LINK.UNRESOLVED"),
    ("链接与图片", r"^o2o_ 图片不在", "LINK.IMAGE_OUTSIDE_DIR"),
    ("链接与图片", r"^检查 \d+ 份", STAT),
    ("链接与图片", r"^图片目录共", "LINK.ORPHAN_IMAGES"),
    ("链接与图片", r"^图片目录不存在", "LINK.IMAGES_DIR_MISSING"),
    ("决策记录", r"编号重复$", "DEC.NUMBER_DUPLICATE"),
    ("决策记录", r"列数 \d+", "DEC.TABLE_COLUMNS"),
    ("决策记录", r"^状态 ", "DEC.STATUS_VOCAB"),
    ("决策记录", r"^旧状态", "DEC.LEGACY_STATUS"),
    ("决策记录", r"^superseded 必须", "DEC.SUPERSEDED_NO_SUCCESSOR"),
    ("决策记录", r"没有回指", "DEC.NO_BACKREF"),
    ("决策记录", r"^共 \d+ 行", STAT),
    ("偏离登记", r"^状态列", "DEV.STATUS_VOCAB"),
    ("偏离登记", r"^发现时点列", "DEV.FOUND_VOCAB"),
    ("偏离登记", r"^共 \d+ 条", STAT),
    ("偏离登记", r"^处置列含“关闭”", "DEV.CLOSED_BY_DISPOSITION"),
    ("偏离登记", r"^处置列未写", "DEV.OPEN_FOR_PLAN"),
    ("偏离登记", r"^本计划的偏离仍为 open", "DEV.OPEN_FOR_PLAN"),
    ("schema", r"^缺少必填字段", "SCHEMA.MISSING_FIELD"),
    ("schema", r"含未加引号的 wikilink|的值必须加引号", "SCHEMA.UNQUOTED"),
    ("schema", r"不符合类型", "SCHEMA.BAD_VALUE"),
    ("schema", r"^schema=", "SCHEMA.BAD_VALUE"),
    ("schema", r"^schema 外字段", "SCHEMA.UNKNOWN_KEYS"),
    ("schema", r"id 重复", "SCHEMA.ID_DUPLICATE"),
    ("schema", r"^找不到 type: plan", "SCHEMA.PLAN_NOT_FOUND"),
    ("迁移（旧格式）", r".", "SCHEMA.LEGACY_FORMAT"),
    ("生命周期", r"^最终 RES 状态为", "LIFE.PLAN_RESULT_NOT_CONFIRMED"),
    ("生命周期", r"：决策 #\d+ 状态为 .*须为 confirmed$", "DEC.NOT_CONFIRMED"),
    ("生命周期", r"^status≥approved 须有批准决策", "LIFE.PLAN_NO_APPROVAL"),
    ("生命周期", r"^closed 须有结项决策", "LIFE.PLAN_NO_CLOSING"),
    ("生命周期", r"^冻结须有用户确认规则摘要的决策", "LIFE.EXEC_NO_RULES_DECISION"),
    ("生命周期", r"^label_authorization 指向的决策", "EV.AUTHORIZATION_UNRESOLVED"),
    ("生命周期", r"^RES confirmed 须有确认决策", "RES.CONFIRM_NO_DECISION"),
    ("生命周期", r"^一个计划只能有一张未被取代的 RES", "LIFE.RES_MULTIPLE_ACTIVE"),
    ("生命周期", r"^status≥running 须至少有一份已冻结", "LIFE.PLAN_NO_FROZEN_EXEC"),
    ("生命周期", r"^status≥read-out 须恰有一张最终 RES", "LIFE.PLAN_RES_COUNT"),
    ("生命周期", r"^status≥read-out 须所有看图页已标注", "LIFE.PLAN_EV_UNLABELLED"),
    ("生命周期", r"^status≥confirmed 须在 `result`", "LIFE.PLAN_RESULT_MISSING"),
    ("生命周期", r"^`result` 指向的不是 RES 笔记", "LIFE.PLAN_RESULT_INVALID"),
    (
        "生命周期",
        r"^`result` 不是该计划唯一的未被取代 RES",
        "LIFE.PLAN_RESULT_NOT_ACTIVE",
    ),
    ("生命周期", r"^status=closed 只用于 legacy", "LIFE.EXEC_CLOSED_NOT_LEGACY"),
    ("生命周期", r"^缺少「.*」一节", "EXEC.NO_RULES_SECTION"),
    ("生命周期", r"但没有 rules_sha256", "LIFE.EXEC_NO_RULES_SHA"),
    ("生命周期", r"^规则摘要在冻结后被改动", "EXEC.RULES_CHANGED"),
    ("生命周期", r"^rules_amended_by 引用的", "EXEC.AMEND_UNREGISTERED"),
    ("生命周期", r"^看图页缺少附表孪生", "EV.NO_APPENDIX"),
    ("生命周期", r"^`appendix` 指向的笔记 type=", "EV.TWIN_MISMATCH"),
    ("生命周期", r"^附表的 `parent` 没有指回本看图页", "EV.TWIN_MISMATCH"),
    ("生命周期", r"^`exec_plan` 没有指向", "EV.EXEC_PLAN_INVALID"),
    ("生命周期", r"^status=labelled 但用户标签表为", "EV.LABEL_NOT_FILLED"),
    ("生命周期", r"^status=labelled 须写 labelled_by", "EV.LABELLED_BY_MISSING"),
    ("生命周期", r"^标签由助手代填", "EV.AUTHORIZATION_MISSING"),
    ("生命周期", r"^status=label-na 但标签表已填", "EV.LABEL_NA_BUT_FILLED"),
    ("生命周期", r"^未标注（rendered）前不得写", "EV.OBSERVATION_BEFORE_LABEL"),
    (
        "生命周期",
        r"^标签表已填但 status 仍为 rendered",
        "EV.LABEL_FILLED_STATUS_RENDERED",
    ),
    ("生命周期", r"^看图页除标签表外最多一张终点表", "EV.EXTRA_TABLES"),
    ("生命周期", r"^看图页终点表与附表", "EV.ENDPOINT_NOT_IN_APPENDIX"),
    ("生命周期", r"^figures 中 .* 在全库找不到", "EV.FIGURE_UNRESOLVED"),
    ("生命周期", r"^figures 中 .* 未在正文嵌入", "EV.FIGURE_NOT_EMBEDDED"),
    ("生命周期", r"^附表的 `parent` 未解析到看图页", "EV.APPENDIX_NO_PARENT"),
    ("生命周期", r"^看图页的 `appendix` 没有指回本附表", "EV.TWIN_MISMATCH"),
    ("生命周期", r"没有链接本 RES$", "RES.DECISION_NOT_LINKED"),
    ("生命周期", r"^RES confirmed 须填 deviation_impact", "RES.NO_IMPACT"),
    ("生命周期", r"^status=superseded 须有 superseded_by", "RES.SUPERSEDED_INVALID"),
    ("生命周期", r"^superseded_by 指向的 RES 不属于同一计划", "RES.SUPERSEDED_INVALID"),
    ("生命周期", r"^evidence 中 \[\[.*\]\] 找不到", "RES.EVIDENCE_UNRESOLVED"),
    # `_decision_ok` failures that are not "must be confirmed" are mapped by the
    # gate text above; any (area, message) without a rule is a hard error.
]
RULES_COMPILED = [(a, re.compile(m), c) for a, m, c in RULES]

# Decision-gate messages ending in "缺少决策行号" / "不存在" reuse the gate rules above:
# their prefix (the `what` text) selects the code, so they match by prefix, not suffix.
MAPPED_CODES = frozenset(c for _, _, c in RULES if c != STAT)


class UnmappedFinding(Exception):
    pass


def load_oracle(vault_dir: Path):
    """Import vault.py from the frozen copy under a private module name."""
    templates = vault_dir / PROJECT_REL / "templates"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(templates))
    try:
        name = f"vault_oracle_{abs(hash(str(vault_dir)))}"
        spec = importlib.util.spec_from_file_location(name, templates / "vault.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve annotations via sys.modules
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(templates))
    return module


def map_oracle_findings(report) -> tuple[Counter, dict]:
    """Counter of (level, code, where) over mapped findings, plus raw stat lines."""
    mapped: Counter = Counter()
    stats: dict[str, str] = {}
    for finding in set(report.items):  # vault.py deduplicates through a set
        for area, pattern, mapped_code in RULES_COMPILED:
            if area == finding.area and pattern.search(finding.msg):
                code = mapped_code
                break
        else:
            raise UnmappedFinding(
                f"{finding.level} [{finding.area}] {finding.where}: {finding.msg}"
            )
        if code == STAT or code == "LINK.ORPHAN_IMAGES":
            stats[finding.area + ":" + finding.msg.split()[0]] = finding.msg
        if code != STAT:
            mapped[(finding.level, code, finding.where)] += 1
    return mapped, stats


def map_new_findings(findings) -> Counter:
    mapped: Counter = Counter()
    for finding in {f.key: f for f in findings}.values():
        if finding.code in MAPPED_CODES:
            mapped[(finding.level, finding.code, finding.where)] += 1
    return mapped


def diff_counters(oracle: Counter, mine: Counter) -> tuple[list, list]:
    return sorted((oracle - mine).elements()), sorted((mine - oracle).elements())


def stat_differences(oracle_stats: dict, stats: dict) -> list[str]:
    """Compare the counts vault.py prints in its INFO summaries with `stats`."""
    problems = []

    def numbers(text):
        return [int(n) for n in re.findall(r"\d+", text)]

    for key, message in oracle_stats.items():
        if key.startswith("frontmatter 解析"):
            want = (numbers(message)[0], numbers(message)[1])
            got = (stats["parse"]["notes"], stats["parse"]["failed"])
        elif key.startswith("全库文件名唯一性"):
            found = re.search(r"笔记 (\d+) 份、附件 (\d+) 个；重复 (\d+) 组", message)
            want = tuple(int(n) for n in found.groups())
            got = (
                stats["names"]["notes"],
                stats["names"]["attachments"],
                stats["names"]["duplicateGroups"],
            )
        elif key.startswith("链接与图片:图片目录共"):
            want = tuple(numbers(message))
            got = (stats["images"]["total"], stats["images"]["orphans"])
        elif key.startswith("链接与图片"):
            want = tuple(numbers(message))
            got = (stats["links"]["checked"], stats["links"]["unresolved"])
        elif key.startswith("决策记录"):
            count = numbers(message)[0]
            tally = {}
            for label, n in re.findall(
                r"([^，：\s]+) (\d+)", message.split("：", 1)[1]
            ):
                label = label.replace("（旧）", "/legacy").replace(
                    "（需人工）", "/review"
                )
                tally[label.replace("未映射", "unmapped")] = int(n)
            want = (count, sorted(tally.items()))
            got = (
                stats["decisions"]["count"],
                sorted(stats["decisions"]["statusTally"].items()),
            )
        elif key.startswith("偏离登记"):
            want = (numbers(message)[0], re.findall(r"D-\d+", message))
            got = (stats["deviations"]["count"], stats["deviations"]["open"])
        else:
            continue
        if want != got:
            problems.append(f"{key}: oracle {want} != expnote {got}")
    return problems


def default_vault() -> Path | None:
    value = os.environ.get("EXPNOTE_PARITY_VAULT")
    return Path(value).expanduser() if value else None


def compare(oracle_module, vault_root: Path, project_dir: Path, context, plan_id=None):
    """Run both engines on one tree; return (missing, extra, stat problems)."""
    from expnote.research.checks import run_checks
    from expnote.research.snapshot import scan_vault

    vault = oracle_module.Vault(project_dir, vault_root)
    oracle_mapped, oracle_stats = map_oracle_findings(
        oracle_module.run_checks(vault, plan_id)
    )
    mine = run_checks(scan_vault(context), plan_id)
    from expnote.research.checks import split_new_codes

    unmapped = {f.code for f in split_new_codes(mine.findings)[0]} - MAPPED_CODES
    assert not unmapped, f"regular codes vault.py has no counterpart for: {unmapped}"
    missing, extra = diff_counters(oracle_mapped, map_new_findings(mine.findings))
    return missing, extra, stat_differences(oracle_stats, mine.stats)
