---
schema: 3
type: result
id: "RES-{{plan_id}}"
plan: "[[{{plan}}]]"
status: draft
evidence: []
decisions: []
deviation_impact: ""
superseded_by: ""
reviewed_by: ""
project: New Off2On RL
date: "{{date}}"
---
<!-- vault:template-doc:start -->
> **模板说明（`expnote new res` 生成新笔记时会删掉本块）**
>
> 每个计划只有一张最终 RES，在计划 running 收尾时起草，提交后计划进入 read-out。阶段性判读写成这张卡里的小节，不另建 RES。若旧卡被并入，旧卡标 `superseded` 并写 `superseded_by`。
>
> **生命周期**：`draft`（协调方起草）→ `first-read`（协调方提交，等用户审阅）→ `confirmed`（用户审阅通过）；用户退回则回到 `draft`，也可转为 `superseded` 或 `withdrawn`。
> - 写完后用 `expnote res submit <ID> --deviation-impact <none|weak|moderate|affects>` 提交：状态变 `first-read`，`deviation_impact` 是协调方的建议；计划在 `running` 且 read-out 门槛满足时同时进入 `read-out`。
> - 用户在 GUI 里读卡并通过或退回；`deviation_impact` 由用户审阅时可改。退回时意见写入文末「Review remarks」，协调方按意见修改后重新提交。
> - 审阅不经决策：通过后卡上写 `reviewed_by` / `reviewed_on`，不要再写 `confirmed_decision`。
<!-- vault:template-doc:end -->
# RES--{{plan_id}} {{title}}

> 本卡不写数值；数值见各看图页与附表。每条陈述标注等级：**证据** / **推测** / **未决**。

## 一、问题与结论摘要

- 问题：引人读方案 §1。
- 结论：按 Q 编号逐条写，每条一到两句，并标注等级与确认状态。

## 二、预注册判定（按执行方案「规则摘要」）

| Q | 规则（引执行方案） | 观察（引看图页） | 判定 |
|---|---|---|---|
| Q1 | … | … | 成立 / 不成立 / 证据不足 / 不可判 |

## 三、探索性分析（不进预注册判定）

- …

## 四、范围与局限

- **结论覆盖**：任务、格位、父模型、seed。
- **不覆盖**：…
- **局限**：不超过 3 条。

## 五、偏离

| D-xx | 影响 | 对本卡结论的作用 |
|---|---|---|
| … | 无 / 弱 / 中 / 影响结论 | … |

## 六、用户决定与后续方向

- 用户审阅：通过后见卡上 `reviewed_by` / `reviewed_on`；退回意见见文末「Review remarks」。
- 后续方向（均未批准）：…
