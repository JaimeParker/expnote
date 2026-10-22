## schema 3 与 expnote（提议；本节取代上文“schema 2 与 vault.py”一节）

本节补充上文，冲突时以本节为准。

- **每个计划**：人读方案页（正文完全由人写，没有生成的状态尾）+ 执行方案（`type: exec-plan`，「规则摘要（须确认）」经 `expnote exec freeze` 冻结）+ 看图页与附表 + 一张最终 RES。决策时间线、证据、偏离与开放项在 `expnote web` 计划页或 `expnote plan bundle <ID>` 里看。
- **决策**：保存在 expnote 决策库（SQLite），备份导出 `ops/decisions.jsonl`；状态词 proposed / confirmed / superseded / rejected；`决策记录.md` 是静态入口，旧的 `[[决策记录]] #N` 引用仍有效；记录与确认见 `expnote decision new|set` 或 `expnote web`。
- **frontmatter**：键与状态词用 ASCII；新笔记写 `schema: 3`；wikilink 与 run id 一律加引号。字段表见各 `templates/模板--* v3.md`。
- **收尾必跑**（取代上文第 10 条的命令）：`expnote check --project off2on`。输出须 FAIL 0，WARN 逐条说明。检查只读，不再写任何生成文件。
- **常用命令**：`expnote new plan|exec|ev|res`、`expnote decision new|set`（默认干跑，`--apply` 落盘）、`expnote ev split`、`expnote exec freeze`、`expnote plan advance`、`expnote todo add|done`、`expnote search`。
- **数据层**：expnote 运行记录不再投影到本库；笔记中用「`run id`」文本引用运行，索引会在工作区数据库里解析它们。
