---
type: decision-hub
schema: 3
aliases: ["{hub_stem}"]
---
# {hub_stem}

决策由 expnote 的决策库保存（编号 `{prefix}-nnnn`），不再是笔记。在 `expnote web` 里浏览时间线与确认；命令行用 `expnote decision list|show|new|set`。库的备份导出在 `ops/decisions.jsonl`，由工具生成，不要手改。

本页是静态入口，不再生成任何内容。旧笔记里的 `[[{hub_stem}]] #N` 引用照常有效，对应决策 `{prefix}-000N`（最后四位为原编号）。
