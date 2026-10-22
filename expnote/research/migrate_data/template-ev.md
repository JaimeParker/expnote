---
schema: 3
type: evidence
project: New Off2On RL
created: "{{date}}"
plan: "[[{{plan}}]]"
exec_plan: ""
question: ""
status: rendered
figures: []
runs: []
appendix: "[[{{appendix}}]]"
labelled_by: ""
labelled_on: ""
label_authorization: ""
---
<!-- vault:template-doc:start -->
> **模板说明（`expnote new ev` 生成新笔记时会删掉本块；同时生成附表孪生）**
>
> 看图页控制在一屏内，给用户看图打标签：图（图注只写面板内容，不写判读）、至多一张终点表、用户标签表与备注。其余数值、运行身份、来源与核对都放在附表。
>
> **生命周期**：`rendered → labelled`（或 `label-na`）。标签由用户在 `expnote web` 里填写并确认；助手代填须 `expnote ev label --authorization`（决策 id 或用户原话）。`rendered` 状态下「观察」节必须为空：先打标签，后写解读。
>
> 图片直接拷入图片目录（见 `expnote project show` 的 absImagesDir），文件名前缀 `o2o_`，全库唯一，再在正文用 `![[文件名]]` 嵌入并列入 `figures`。
<!-- vault:template-doc:end -->
# {{ev}}

> 数值、运行身份、来源与核对见附表 [[{{appendix}}]]。本页只放图、终点表与用户标签；标签确认前不写观察。

## 图

**面板说明**：每个面板画什么、颜色与线型各代表什么（只描述，不解读）。

## 终点表（附表 T-1 的逐行复制）

| 臂 | 指标 | 终点 |
|---|---|---|
| … | … | … |

## {{label_heading}}（用户填；执行员不填）

| 臂 | 跌落 | 恢复/提升 |
|---|---|---|
| … |  |  |

备注：

## {{observation_heading}}（协调方；用户标签确认后填写）

<!-- 标签确认前留空；`expnote check` 会拦截 -->
