---
type: project-profile
schema: "3"
project_id: "off2on"
images:
  required_prefix: "o2o_"
  dir: "40 Resources/42 Assets/Images/New Off2On RL"
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
  close_title: "关闭 {plan}"
  close_decided: "关闭 {plan}（用户在 expnote 中手动结项）。"
  close_forced_decided: "关闭 {plan}：最终 RES 或其他流程闸尚未走完，用户决定直接结项（强制关闭）。"
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
  dir: "templates"
  files:
    plan: "模板--人读方案 v3.md"
    exec: "模板--执行方案 v3.md"
    ev: "模板--证据看图页 v3.md"
    appendix: "模板--证据附表 v3.md"
    res: "模板--最终RES v3.md"
labels:
  value_columns: ["1"]
  remark_prefix: "备注"
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
split:
  figure_prefix: "图"
  endpoint_prefix: "表"
  run_identity: "运行身份"
  agent_filled_marker: "代填"
  md5_heading_suffix: "（文件校验；图见看图页）"
  page_note: "> 数值、运行身份、来源与核对见附表 [[{appendix}]]。本页只放图、终点表与用户标签；标签确认前不写观察。"
  appendix_note: "看图页：[[{page}]]。以下为原 EV 卡除图与用户标签外的全部内容，原样保留。"
  endpoint_heading: "终点表（附表 {endpoint} 的逐行复制）"
  observation_heading: "观察（协调方；用户标签确认后填写）"
  observation_comment: "标签确认前留空；expnote check 会拦截"
migration:
  supersedes_words: ["取代"]
  number_separators: "、,，/和"
  user_words: ["用户"]
  closing_words: ["结项", "关闭", "终止"]
  approval_words: ["批准", "审批", "启动"]
  final_card_words: ["综合", "最终", "总结"]
  final_result_words: ["最终"]
  partial_word: "部分"
  data_layer_marker: "数据层参考"
  rewrite_table_header: "原状态"
  layering_note: "文档分层约定"
  retired_keywords: ["进展看板", "研究视图", "vault.py", "check_vault", "状态尾"]
  retired_replacements:
    "决策记录--研究视图": "已退役（改用 `expnote web` 的决策时间线）"
    'templates/check_vault\.py|check_vault\.py': "`expnote check --project off2on`"
    'vault\.py': "`expnote`"
  template_names:
    todo: "模板--待办.md"
  flagged_decision_rows:
    "11": "partial revision"
    "12": "partial revision"
    "13": "partial revision"
    "14": "partial revision"
    "78": "partial revision"
    "82": "partial revision"
    "85": "partial revision"
    "87": "partial revision"
    "47": "execution-status word"
    "54": "execution-status word"
    "61": "execution-status word"
    "75": "unmapped legacy status"
    "80": "unmapped legacy status"
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
