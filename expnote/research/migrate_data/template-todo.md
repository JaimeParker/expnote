---
type: template-spec
schema: 3
project: New Off2On RL
created: 2026-10-21
---
# 模板--待办

手工待办只在一个文件里：项目目录的 `待办.md`（`type: todo-list`）。用 `expnote todo add|done|adopt` 改，或在 Obsidian 里直接勾选。

## 行语法

```
- [ ] <正文，可带 [[链接]]> [id:: T-0007] [plan:: PLAN-004A] [due:: 2026-10-25] [by:: agent]
- [x] <正文> [id:: T-0003] [done:: 2026-10-21]
```

规则：一行一个任务；字段写在正文后的 `[key:: value]` 里；id 为 `T-nnnn`，由工具分配；缩进行是续行文字；没有 `[id::]` 的行仍然被识别，第一次经工具编辑时获得 id。工具只做三件事：追加一行、勾选/取消勾选（同时写或删 `[done::]`）、给无 id 的行补 id。
