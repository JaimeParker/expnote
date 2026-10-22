---
schema: 3
type: result
id: "RES-{{plan_id}}"
plan: "[[{{plan}}]]"
status: first-read
evidence: []
decisions: []
confirmed_decision: ""
deviation_impact: ""
superseded_by: ""
reviewed_by: 协调方
project: New Off2On RL
date: "{{date}}"
---
<!-- vault:template-doc:start -->
> **模板说明（`expnote new res` 生成新笔记时会删掉本块）**
>
> 每个计划只有一张最终 RES，写在计划进入 read-out 时。阶段性判读写成这张卡里的小节，不另建 RES。若旧卡被并入，旧卡标 `superseded` 并写 `superseded_by`。
>
> **生命周期**：`first-read`（协调方初读）→ `confirmed`（用户确认），也可转为 `superseded` 或 `withdrawn`。
> - 确认用 `expnote res confirm <ID>`：需要 `confirmed_decision` 指向一条 confirmed 决策，且该决策链接本卡；还要填写 `deviation_impact`（none / weak / moderate / affects）。
> - 只有用户能确认；协调方引用用户原话写决策。
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

- 用户确认：决策 [[O2O-DEC-nnnn]]。
- 后续方向（均未批准）：…
