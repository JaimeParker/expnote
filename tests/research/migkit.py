# ruff: noqa: E501  (Chinese table rows)
"""A synthetic legacy Off2On mini-vault with every shape the P4 migration handles."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from minivault import IMAGES_REL, frozen_exec
from readkit import invoke, run
from writekit import age_tree

PROJECT_REL = "10 Projects/Off"
WORKSPACE = "ws1"

DECISIONS = """---
type: decision-log
project: Off
---
# 决策记录

一行一条。

| # | 日期 | 决定 | 理由 | 落到 | 状态 | 层 |
|---|---|---|---|---|---|---|
| 1 | 2026-09-01 | 关闭 [[PLAN-900A--demo\\|PLAN-900A]] 旧线：判为证伪 | 范数 $\\|e_k\\|$ 太大；见 [[决策记录--研究视图]] | 本项目立项 | confirmed | 研究 |
| 2 | 2026-09-02 | PLAN-900A 批准：开始第一步 | 用户原话 | [[PLAN-900A--demo]]；[[执行方案--PLAN-900A S1]] | confirmed（用户 2026-09-02） | 研究 |
| 3 | 2026-09-03 | 规则摘要确认；取代 #1 的旧阈值 | 用户原话 | [[执行方案--PLAN-900A S1]] | confirmed（用户 2026-09-03） | 研究 |
| 4 | 2026-09-04 | PLAN-900A 最终判读确认 | 用户原话 | [[RES--PLAN-900A 判读]] | confirmed（用户 2026-09-04） | 研究 |
| 5 | 2026-09-05 | PLAN-900A 结项 | 用户原话 | [[PLAN-900A--demo]] | confirmed（用户 2026-09-05） | 研究 |
| 6 | 2026-09-06 | 旧做法 | 过时 | [[PLAN-900B--legacy]] | superseded by #7 | 工程 |
| 7 | 2026-09-07 | 新做法 | 更好 | [[PLAN-900B--legacy]] | confirmed | 工程 |
| 8 | 2026-09-08 | 范围修订：部分内容 | x | [[PLAN-900B--legacy]] | confirmed（部分由 #7 修订） | 研究 |
| 9 | 2026-09-09 | 待执行的事项 | x | 本项目 | confirmed | 工程 |
| 11 | 2026-09-11 | 多任务并行 | x | [[PLAN-900B--legacy]] | confirmed（部分由 #7 修订） | 研究 |
| 47 | 2026-09-12 | 弃用旧容器 | x | 本项目 | confirmed | 工程 |
| 123 | 2026-10-21 | 补记授权 | 用户 2026-10-21 确认 | [[PLAN-900A--demo]] | confirmed（用户 2026-10-21） | 研究 |
| 124 | 2026-10-21 | 体系重构 | 用户 2026-10-21 | [[PLAN-900B--legacy]] | confirmed（用户 2026-10-21） | 研究 |
"""

RESEARCH_VIEW = """---
type: decision-log-view
---
# 决策记录--研究视图

生成物，勿手改。见 [[决策记录]]。
"""

STATUS_TAIL = """
<!-- vault:status:start -->

## 状态（自动生成，勿手改）

**计划状态**：closed

| # | 决定 |
|---|---|
| 2 | PLAN-900A 批准 |

<!-- vault:status:end -->
"""

PLAN_A = (
    """---
schema: 2
id: PLAN-900A
type: plan
title: "PLAN-900A：演示"
status: closed
legacy: true
approved_decision: 2
closed_decision: 5
result: "[[RES--PLAN-900A 判读]]"
---
# PLAN-900A：演示

## 1. 问题

人写正文。跟踪运行 [[abc12345]]，决策见 [[决策记录--研究视图]]。行内代码 `[[abc12345]]` 保持原样。

```
[[abc12345]] 在代码块里也保持原样
```
"""
    + STATUS_TAIL
)

PLAN_B = """---
id: PLAN-900B
type: plan
status: awaiting-approval
created: 2026-09-10
approval: "[[决策记录]] #2、#7"
evidence:
  - "[[EV--PLAN-900B 旧卡]]"
---
# PLAN-900B：旧计划

人写正文。
"""

PLAN_B_DRAFT = """---
id: PLAN-900B
type: plan
status: draft
---
# 归档的旧草稿
"""

EXEC = frozen_exec().replace("rules_decision: 3", "rules_decision: 3")

EV_SCHEMA2 = """---
schema: 2
type: evidence
plan: "[[PLAN-900A--demo]]"
exec_plan: "[[执行方案--PLAN-900A S1]]"
status: labelled
figures: ["o2o_demo.png"]
runs: ["abc12345"]
appendix: "[[附表--EV--PLAN-900A 演示]]"
labelled_by: agent-for-user
label_authorization: "3"
---
# EV--PLAN-900A 演示

## 图

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

APPENDIX_SCHEMA2 = """---
schema: 2
type: evidence-appendix
parent: "[[EV--PLAN-900A 演示]]"
plan: "[[PLAN-900A--demo]]"
runs: ["abc12345"]
---
# 附表

| 臂 | 终点 |
|---|---|
| orig | 0.1 |
"""

RES_SCHEMA2 = """---
schema: 2
type: result
id: RES-PLAN-900A
plan: "[[PLAN-900A--demo]]"
status: confirmed
evidence: ["[[EV--PLAN-900A 演示]]"]
decisions: [2, 4]
confirmed_decision: 4
deviation_impact: weak
---
# RES--PLAN-900A 判读

引用运行 [[abc12345]]。
"""

EV_LEGACY_BACKREF = """---
type: evidence
date: 2026-09-11
---
# EV--PLAN-900B 旧卡

数据层参考：[[RES--PLAN-900B 阶段一]]。

![[o2o_demo.png]]

跟踪 [[abc12345]]。
"""

EV_LEGACY_FILENAME = """---
type: evidence
status: current
---
# EV--PLAN-900B 仅文件名

只有文件名里有计划编号。
"""

EV_LEGACY_ORPHAN = """---
type: evidence
status: draft
---
# EV--代码审计

没有计划编号，也没有被任何实验卡引用。
"""

RESULT_CARD = """---
type: result-card
experiment: B-1
plan: "[[PLAN-900B--legacy]]"
evidence: "[[EV--PLAN-900B 旧卡]]"
status: published
date: 2026-09-12
---
# RES--PLAN-900B 阶段一

实验卡正文，保持原样。
"""

RESULT_CARD_ARCHIVED = """---
type: result-card
experiment: B-2
plan: "[[PLAN-900B--legacy]]"
evidence: ["[[EV--PLAN-900B 旧卡]]"]
status: draft
---
# RES--PLAN-900B 阶段二
"""

HYPOTHESIS = """---
type: hypothesis
id: H-01
status: open
---
# H-01

相关假说 [[H-04 "引号"测试]]。
"""

HYPOTHESIS_QUOTES = """---
type: hypothesis
id: H-04
status: open
---
# H-04 引号

文件名里带 ASCII 双引号。
"""

EXEC_LOG = """---
type: execution-log
plan: "[[PLAN-900A--demo]]"
---
# 执行日志--PLAN-900A

| 运行 | 备注 |
|---|---|
| [[abc12345\\|第一次]] | 完成 |
| [[ana00001]] | 分析 |
"""

OLD_TEMPLATE = """---
schema: 2
type: plan
id: "{{id}}"
status: draft
---
<!-- vault:template-doc:start -->
> 说明 `vault.py new plan`
<!-- vault:template-doc:end -->
# {{id}}

<!-- vault:status:start -->
<!-- vault:status:end -->
"""

DEVIATIONS = """---
type: deviation-log
---
| 编号 | 日期 | 实验 | 偏离内容 | 对结论有效性的影响 | 处置 | 相关实验卡 |
|---|---|---|---|---|---|---|
| D-01 | 2026-09-03 | PLAN-900A S1 | x | 无 | 已关闭 | [[RES--PLAN-900A 判读]] |
"""

MOC = """---
type: project-moc
---
# Off MOC

决策：[[决策记录--研究视图]]；检查 `templates/check_vault.py`。
"""

LAYERING = """# 文档分层约定

## 研究层
- `plans/PLAN-xxx 进展看板`：一行一个实验。
- `决策记录--研究视图.md` 是只含研究条目的投影。

## schema 2 与 vault.py

收尾必跑 `python3 templates/vault.py check .`。
"""

PREVIEW = """---
type: exec-plan
schema: 2
---
# 预览
"""


@dataclasses.dataclass
class LegacyVault:
    tmp: Path
    root: Path
    proj: Path
    images: Path
    workspace_dir: Path

    def text(self, rel: str) -> str:
        return (self.proj / rel).read_text(encoding="utf-8")

    def write(self, rel: str, text: str) -> Path:
        path = self.proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_legacy_vault(tmp_path: Path, *, git: bool = False) -> LegacyVault:
    from expnote.workspace import data_home

    root = tmp_path / "vault"
    proj = root / PROJECT_REL
    images = root / IMAGES_REL
    (root / ".obsidian").mkdir(parents=True)
    _write(root / ".obsidian" / "app.json", "{}")
    _write(images / "o2o_demo.png", "png")
    vault = LegacyVault(tmp_path, root, proj, images, data_home() / "workspaces" / WORKSPACE)
    files = {
        "决策记录.md": DECISIONS,
        "决策记录--研究视图.md": RESEARCH_VIEW,
        "Off MOC.md": MOC,
        "plans/PLAN-900A--demo.md": PLAN_A,
        "plans/PLAN-900B--legacy.md": PLAN_B,
        "plans/归档/PLAN-900B 草稿.md": PLAN_B_DRAFT,
        "ops/执行方案--PLAN-900A S1.md": EXEC,
        "ops/附表--EV--PLAN-900A 演示.md": APPENDIX_SCHEMA2,
        "ops/偏离登记.md": DEVIATIONS,
        "ops/执行日志--PLAN-900A.md": EXEC_LOG,
        "ops/_refactor_preview/预览.md": PREVIEW,
        "evidence/EV--PLAN-900A 演示.md": EV_SCHEMA2,
        "evidence/EV--PLAN-900B 旧卡.md": EV_LEGACY_BACKREF,
        "evidence/EV--PLAN-900B 仅文件名.md": EV_LEGACY_FILENAME,
        "evidence/EV--代码审计.md": EV_LEGACY_ORPHAN,
        "results/RES--PLAN-900A 判读.md": RES_SCHEMA2,
        "results/RES--PLAN-900B 阶段一.md": RESULT_CARD,
        "results/归档/RES--PLAN-900B 阶段二.md": RESULT_CARD_ARCHIVED,
        "theory/归档/H-01 假说.md": HYPOTHESIS,
        'theory/归档/H-04 "引号"测试.md': HYPOTHESIS_QUOTES,
        "templates/模板--人读方案.md": OLD_TEMPLATE,
        "templates/文档分层约定.md": LAYERING,
        "templates/vault.py": "print('engine')\n",
        "templates/tests/test_vault.py": "def test_x():\n    pass\n",
    }
    for rel, text in files.items():
        _write(proj / rel, text)
    for hollow in ("plans/.codex", "results/.aws"):
        (proj / hollow).mkdir(parents=True)
    _write(proj / ".agents" / "notes.txt", "keep me")
    projection = root / "50 Archive" / "Expnote" / WORKSPACE
    _write(projection / "runs" / "abc12345.md", "# run abc12345\n")
    _write(projection / "analyses" / "ana00001.md", "# analysis\n")
    _write(projection / "Baseline MOC.md", "# moc\n")
    _write(root / "00 Inbox" / "DISS.md", "见 [[abc12345]] 与 [[别的笔记]]。\n")
    _write(root / "00 Inbox" / "别的笔记.md", "x\n")
    _write(root / "50 Archive" / "Projects" / "Old" / "n.md", "[[abc12345]] 归档\n")
    config = vault.workspace_dir / "config.toml"
    _write(
        config,
        f'project = "ws"\nstate_dir = "{vault.workspace_dir}"\n'
        f'obsidian_root = "{root}"\n'
        'notes_dir = "50 Archive/Expnote/ws1/runs"\n'
        'docs_dir = "50 Archive/Expnote/ws1/analyses"\nindex_path = "index.md"\n',
    )
    if git:
        for argv in (
            ["init", "-q"],
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"],
        ):
            subprocess.run(["git", "-C", str(root), *argv], check=True)
    age_tree(root)
    return vault


def init_project(vault: LegacyVault) -> None:
    result = invoke(
        "project", "init", "--id", "off2on", "--project-dir", vault.proj,
        "--images-dir", vault.images, "--workspace", WORKSPACE, "--apply", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output


def migrate(*args: str, apply: bool = False, no_quiet: bool = True) -> dict:
    """Run `expnote migrate ...` with --json and return the envelope (+ `_exit`)."""
    extra = []
    if apply:
        extra.append("--apply")
    if apply and no_quiet and args[0] in ("apply", "profile"):
        extra.append("--no-quiet")
    return run("migrate", *args, "--project", "off2on", *extra)


def full_run(vault: LegacyVault, out: Path | None = None) -> None:
    """backup -> profile -> every step, applied."""
    out = out or vault.tmp / "backup"
    assert migrate("backup", "--out", out, apply=True)["_exit"] == 0
    assert migrate("profile", apply=True)["_exit"] == 0
    assert migrate("plan")["_exit"] == 0
    body = migrate("apply", "--step", "all", apply=True)
    assert body["_exit"] == 0, body
