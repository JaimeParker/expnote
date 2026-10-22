---
type: project-profile
schema: "3"
project_id: "off2on"
images:
  required_prefix: "o2o_"
headings:
  rules: "规则摘要（须确认）"
  labels: ["看图标签", "用户批注"]
  observation: "观察"
  decision_required: ["决定", "理由"]
  decision_options: "备选"
  decision_consequences: "落到"
decisions:
  id_prefix: "O2O-DEC"
  strict_from: "123"
  layers: ["研究", "工程"]
  options_warn_layers: ["研究"]
  title_delimiters: "：。；"
paths:
  deviation_register: "ops/偏离登记.md"
  todo_file: "待办.md"
  decision_export: "ops/decisions.jsonl"
runs:
  id_regex: '^[a-z0-9]{8}$'
scan:
  skip_dirs: []
  template_dirs: ["templates"]
  archive_dir_names: ["归档"]
  ignore_globs: ["**/_refactor_preview/**"]
  conflict_globs: ["*.sync-conflict-*", "*conflicted copy*", "* (conflict *)*"]
legacy:
  decision_table: "决策记录.md"
  decision_columns:
    number: "#"
    date: "日期"
    decision: "决定"
    reason: "理由"
    lands: "落到"
    status: "状态"
    layer: "层"
  decision_status_rules:
    - pattern: '部分(修订|已推翻|取代)|部分被|被 ?#\d+ ?部分'
      status: confirmed
      review: '部分被后续决策修订：MADR 无“部分取代”，需人工决定标 confirmed 并在后继行注明修订范围'
    - pattern: '^已推翻|由 ?#\d+ ?接替|被 ?#\d+ ?(取代|接替)'
      status: superseded
    - pattern: '^(待用户决定|方案待用户审批|共识与方案草案)'
      status: proposed
    - pattern: '^阻塞'
      status: confirmed
      review: '“阻塞”是执行状态而非决策状态：执行状态移到执行日志'
    - pattern: '^(待执行|待标定)'
      status: confirmed
      review: '“待执行/待标定”是执行状态：决策本身视为 confirmed'
    - pattern: '^(有效|已确认|已采纳|已执行|已批准|已完成|已解决|用户批准|即日起有效|文档修订已确认|研究范围已确认|进行中|实现与验收进行中|本轮文档修订|已按指示)'
      status: confirmed
  type_map:
    result-card: result
  unmigrated_types: ["plan", "evidence", "result-card", "progress-board"]
  res_status_map:
    published: confirmed
    draft: first-read
    pre-registered: first-read
    withdrawn: withdrawn
forbidden:
  markers: ["<!-- vault:status:start -->", "<!-- vault:status:end -->"]
  marker_level: "warn"
  generated_notes: ["决策记录--研究视图.md"]
templates:
  doc_markers: ["<!-- vault:template-doc:start -->", "<!-- vault:template-doc:end -->"]
labels:
  value_columns: ["1"]
deviation:
  id_regex: 'D-\d+'
  columns:
    id: "编号"
    date: "日期"
    experiment: "实验"
    content: "偏离内容"
    impact: "对结论有效性的影响"
    disposition: "处置"
    cards: "相关实验卡"
    status: "状态"
    found: "发现时点"
  found_values: ["见数据前", "见数据后", "不适用"]
  impact_cn:
    无: none
    弱: weak
    中: moderate
    影响结论: affects
  closed_marker: "关闭"
ids:
  plan_regex: '^PLAN-\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*$'
  exec_regex: '^PLAN-\d+[A-Za-z]*-S\d+$'
  res_prefix: "RES-"
frontmatter:
  extra_common_keys: []
attachments:
  extra_exts: []
prose:
  number_allow_regexes: ['\bS\d+\b', '\bv\d+(?:\.\d+)*\b', '\b[Ss]tep ?\d+\b']
display:
  label_cn:
    filled: "已填"
    partial: "部分"
    empty: "未填"
    missing: "无标签节"
names:
  plan: "{id}--{title}"
  exec: "执行方案--{pid} {title}"
  ev: "EV--{pid} {title}"
  appendix: "附表--{stem}"
  res: "RES--{pid} {title}"
folders:
  plan: "plans"
  exec: "ops"
  ev: "evidence"
  appendix: "ops"
  res: "results"
---
# expnote 项目配置

Off2On 项目的 expnote 研究层配置：frontmatter 即配置，工具只读不写。
