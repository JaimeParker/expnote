---
schema: 3
type: plan
id: "{{id}}"
title: "{{title}}"
status: draft
project: New Off2On RL
created: "{{date}}"
owner: ""
parent: ""
approved_decision: ""
closed_decision: ""
result: ""
---
<!-- vault:template-doc:start -->
> **模板说明（`expnote new plan` 生成新笔记时会删掉本块）**
>
> 人读方案只写“问什么、不问什么、怎样读结果”。服务器、路径、命令、阶段与预注册阈值都写在执行方案里。正文完全由人写，工具不向本页写任何生成内容；决策时间线、证据与开放项在 `expnote web` 的计划页或 `expnote plan bundle <ID>` 里看。
>
> **生命周期**：`draft → approved → running → read-out → confirmed → closed`，另有终态 `superseded`、`withdrawn`。用 `expnote plan advance <ID>` 前进（先干跑，缺什么会列出），`expnote check --plan <ID>` 检查。
> - `approved`：`approved_decision` 指向一条 confirmed 决策（`O2O-DEC-nnnn`）
> - `running`：至少一份执行方案已冻结
> - `read-out`：恰有一张未被取代的 RES，且所有看图页已标注
> - `confirmed`：`result` 指向该 RES，且 RES 为 confirmed
> - `closed`：`closed_decision` 指向一条 confirmed 决策
>
> 已结项的旧计划加 `legacy: true`，免执行方案与看图页门槛。字段表见设计文档 4.3；公共字段 `project`、`created`、`related`、`tags`、`aliases` 照常可用。
<!-- vault:template-doc:end -->
# {{id}}：{{title}}

## 1. 问题

一句话写清要回答的问题。再用一两段写背景，以及为什么现在问。

## 2. 范围与非目标

**范围**：环境、格位、父模型、预算的量级。具体数值放执行方案。

**非目标**：明确写出“这次不回答什么”。

## 3. 要检验什么

每条带编号，供执行方案与证据引用：

- **Q1**：…
- **Q2**：…

## 4. 怎样读结果

人读的读法表。正式阈值与判定规则写在执行方案的「规则摘要（须确认）」里，冻结后不改。

| 观察 | 可以推进的结论 |
|---|---|
| … | … |

## 5. 备选方案与先前证据

- 考虑过、但没有采用的做法，及原因
- 先前证据：EV、RES 或文献链接
