---
schema: 3
type: exec-plan
id: "{{id}}"
plan: "[[{{plan}}]]"
stage: "{{stage}}"
status: draft
project: New Off2On RL
created: "{{date}}"
servers: []
expnote_topic: ""
rules_decision: ""
rules_sha256: ""
frozen_on: ""
rules_amended_by: []
---
<!-- vault:template-doc:start -->
> **模板说明（`expnote new exec` 生成新笔记时会删掉本块）**
>
> 执行方案给协调方和执行员读：写服务器、路径、阶段与命令要点。预注册规则只写在「规则摘要（须确认）」一节。
>
> **生命周期**：`draft → frozen → running → done`，可随时转为 `aborted`。
> 1. 协调方写好规则摘要，请用户确认，确认写成一条 confirmed 决策。
> 2. 把该决策的 id 写进 `rules_decision`。
> 3. 运行 `expnote exec freeze <笔记名> --apply`。它对规则摘要一节（标题行除外；统一 LF、去行尾空白、NFC）计算 sha256，写入 `rules_sha256` 与 `frozen_on` 并将 `status` 置为 frozen。
>
> 冻结后再改规则摘要，`expnote check` 会报 FAIL。改规则须先在偏离登记登记 D-xx，再运行 `expnote exec freeze --amend D-xx`。`exec advance` 把 frozen 推进到 running / done / aborted。
<!-- vault:template-doc:end -->
# 执行方案--{{plan_id}} {{title}}

人读方案：[[{{plan}}]]。本页写执行与判定规则；判读写在最终 RES。

## 1. 阶段

| 阶段 | 内容 | 服务器 | 产物路径 |
|---|---|---|---|
| S1 | … | … | `runs/…` |

## 2. 材料与命令要点

- 父模型与数据：…
- 命令：…（写到可复现的程度；长命令放执行日志）

## {{rules_heading}}

冻结前由用户确认，冻结后只能经偏离登记修改。按 OSF/AsPredicted 的骨架写，每条一行：

- **假说**（对应人读方案 Q 编号）：…
- **设计与对照**：…
- **n 与独立重复单位**：训练 seed（不是探针状态数）…
- **变量与指标定义**：…
- **判定规则**：成立 / 不成立 / 证据不足 / 不可判，各自的阈值或公式…
- **阈值的标定方法与冻结时点**：…
- **数据排除规则**：…
- **探索性分析**（不进预注册判定）：…

## 3. 风险与资源

- …
