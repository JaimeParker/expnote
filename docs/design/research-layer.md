# Research layer for expnote: design (P0)

Status: DRAFT r2 for user approval. No code is written until the user approves this document.
Date: 2026-10-21. Author: design agent for the coordinator.
Evidence base: `rl-garden/temp/docs/research-engine/{inventory_expnote_vault,survey_research_cli}.md`,
expnote `cli.py`/`workspace.py`/`web.py`/`db.py` as of commit HEAD, and the Off2On vault
(`/mnt/d/Documents/Cyber Brain`, project dir `10 Projects/New Off2On RL/`) as read on 2026-10-21.
Notation: **[FACT]** read in a file; **[INFERENCE]** my conclusion; **[UNVERIFIED]** not checked.

## 1. Goals and non-goals

Goals

| # | Goal |
|---|---|
| G1 | Port the vault research engine (`templates/vault.py`, 2,021 lines, PyYAML) into expnote as a tested module, keeping every B.3 rule (lifecycle gates, freeze, label rules, one final RES, uniqueness, link/image resolution). |
| G2 | Make the engine multi-project: nothing Off2On-specific in module code; Off2On lives in a profile. |
| G3 | Give agents a one-call, token-bounded view of a plan (`plan bundle`) plus search over notes, links and images. |
| G4 | Give the user a read-mostly localhost GUI (dashboard, plan page, decision timeline, TODOs) with a three-action write set. |
| G5 | Remove all code and all generated files from the synced vault. |
| G6 | Migrate every legacy note to the new schema without losing prose, with a reviewable diff and a backup outside the vault. |

Non-goals

- No research object (plan, decision, EV, RES, deviation) becomes SQLite truth. Runs and metrics stay in the existing expnote SQLite, schema unchanged.
- No rich Markdown editor in the GUI. Prose is edited in Obsidian or by agents.
- No GUI write for plan status, exec-plan freeze, RES status, deviations, or new notes (write set is exactly three actions, section 8/9).
- No change to the 73 existing commands' output format (`_emit` prints bare JSON; the new envelope applies to research commands only, section 6).
- No MCP server in P1-P5 (optional adapter after P5, section 12).
- No cloud/auth/multi-user. The GUI binds 127.0.0.1.
- No automatic interpretation: generated views only copy, filter and count, as vault.py's generators do today.

## 2. Architecture

### 2.1 Three places

| Place | Holds | Writer | Disposable? |
|---|---|---|---|
| Vault (`/mnt/d/Documents/Cyber Brain`, WebDAV/sync-visible) | Markdown prose and records: plans, exec-plans, EV + appendix, RES, decision files, deviation register, manual TODO file, theory, templates, images | Humans in Obsidian; agents editing prose directly; the `expnote` tool for structured edits | No (truth) |
| Workspace state (existing: `~/.local/share/expnote/workspaces/<ws>/expnote.sqlite`, `events.jsonl`) | Runs, relations, artifacts, topics, docs, benchmarks | Existing expnote commands | No (truth for runs) |
| Research state (new, `~/.local/share/expnote/research/`, ext4, outside the vault) | Vault index (SQLite FTS5 + link graph), per-project audit log, write journal, spill files | `expnote` only | Index yes (rebuildable); audit log and journal no |
| GUI (localhost FastAPI) | Nothing persisted; renders from index + files + workspace DB | Calls the same write layer as the CLI | n/a |

### 2.2 Data flow

```
 Obsidian (user)        agents (Claude, edit prose)        remote servers (6017/9990/9981)
      |  edits .md             |  edits .md / calls CLI           | train, write runs/ outputs
      v                        v                                  | (NEVER write the vault)
 +------------------- VAULT (Markdown + images) -------------------+      |
 |  plans/ ops/ evidence/ results/ decisions/ theory/ templates/    |      | ingest (existing
 +---^------------------------------^-------------------------------+      | import/sync flows)
     | structured edits only        | read (walk, parse, hash)              v
     | CAS + atomic + audit         v                              +--------------------+
 +---+-------------------+   +-----------------------+           | workspace SQLite   |
 | write layer (module)  |<--| INDEX (SQLite, FTS5)  |  RO join  | runs, artifacts    |
 +---^-------------------+   | state dir, rebuildable|---------->| (unchanged)        |
     |                       +-----------^-----------+           +--------------------+
     |  CLI (--json envelope)            |  API
 +---+-------------+           +---------+----------------+
 | agents / scripts|           | GUI: expnote web (127.0.0.1) |  <- user (3 write actions)
 +-----------------+           +------------------------------+
```

### 2.3 Single-writer rule (precise)

"Single writer" means: the `expnote` process (CLI or GUI) is the only *programmatic* writer of structured fields and of derived/derivable content in the vault. It does **not** mean agents stop writing prose: coordinators and executors still edit plan bodies, RES bodies, EV figures, execution logs and theory notes with their own file tools, and the user edits anything in Obsidian. Consequences:

- Every tool write is compare-and-swap on the file's sha256 and refuses recently modified files (section 8), because other writers exist.
- `check` re-validates invariants after the fact (e.g. a frozen rules section edited by hand is still a FAIL).
- Nothing is ever *generated* into the vault. Generated views exist only in GUI/CLI output.

### 2.4 Remote servers

Remote servers never write the vault. They produce runs/outputs outside the synced tree (memory rule: remote outputs go to `runs/` or `/workspace/_outputs`), which reach the local expnote workspace via existing flows (`import`, `sync`). The research layer only reads the local workspace DB. Mutagen/vault sync of the vault itself is not touched by the module.

### 2.5 Module placement

`expnote/research/` (package, own tests under `tests/research/`), registered as Typer sub-apps from `expnote/cli.py` with one import line each, and an `APIRouter` mounted by `web.py`. Core files (`db.py`, `workspace.py`, `markdown.py`) are not edited except: (a) `sync markdown`/`sync all` gain a guard for research-bound workspaces (section 3.5), (b) `_AGENT_GUIDE` gains a `research` section, (c) `create_app` mounts the router, (d) `web()` currently resolves a workspace through `_workspace_context`, which raises when none is active, so research-only `expnote web` needs that call made optional when projects are registered. Suggested layout:

| File | Content |
|---|---|
| `registry.py`, `profile.py` | project registry (TOML), loading the profile from its vault note, path revalidation |
| `textkit.py` | NFC/casefold, code stripping, wikilinks, headings, tables, sha helpers (ported pure helpers, about 350 lines in vault.py) |
| `frontmatter.py` | strict loader, minimal-edit setters (text-level, no YAML re-dump) |
| `model.py`, `schemas.py` | `Note`, entity dataclasses, field tables (`Field` as in vault.py) |
| `checks.py` | findings with stable codes; lifecycle, links, uniqueness, decisions, deviations |
| `index.py` | SQLite schema, incremental refresh, FTS, queries |
| `todos.py`, `bundle.py`, `runs.py` | derived TODOs, plan bundle, read-only workspace join |
| `writeback.py` | CAS, atomic write, journal, audit |
| `cli.py`, `web_routes.py`, `static/` | Typer groups, FastAPI router, front-end assets |
| `migrate.py` | P4 one-shot migration (removed after P5 acceptance) |

Dependency: PyYAML (vault.py's `StrictLoader` needs it; expnote has none). Recommendation: optional extra `research = ["pyyaml>=6"]`, imported lazily; commands fail with `ENV_MISSING_DEPENDENCY` and the install remediation. TOML is read with stdlib `tomllib`; written with a small deterministic emitter (expnote already hand-writes TOML in `workspace.py`). `fastapi`, `uvicorn`, `markdown` are already core deps.

## 3. Project registry and profile

### 3.1 Files

| File | Location | Format | Contents | Portable? |
|---|---|---|---|---|
| Project registry | `~/.config/expnote/projects.toml` (via `config_home()`) | real TOML, read with `tomllib` | per-project bindings, abs paths | No (machine-specific) |
| Project profile | one static Markdown note in the project dir, suggested name `expnote 项目配置.md`, `type: project-profile` | YAML frontmatter (nested maps allowed for this type only; every other type stays flat) | the profile (3.4) and the project identity (`project_id`, `schema`) | Yes (travels with the vault; the vault's git tracks `*.md`, so it has history) |

The profile note is edited only by the user or the coordinator in Obsidian or with their own file tools; the `expnote` tool never writes it (it can scaffold a first copy with `new profile`, which refuses to overwrite). It is re-read at every command start and cached by sha256; a changed sha forces a full reparse of the project's notes at the next refresh.

Why a separate `projects.toml`: [FACT] `workspace.py::_read_registry` is a line parser for flat `key = "value"` pairs (it splits on the first `=` and strips quotes); nested TOML tables added to `config.toml` would be misparsed and would be rewritten flat by `_write_registry`. The existing `config.toml` is untouched.

### 3.2 Registry format

```toml
active_project = "off2on"

[projects.off2on]
name = "New Off2On RL"
vault_id = "7c1e5b0a-..."                      # uuid minted at register; keys the index; survives rebind
vault_root = "/mnt/d/Documents/Cyber Brain"
vault_root_alternates = ["D:/Documents/Cyber Brain"]   # tried in order if vault_root is unavailable
project_dir = "/mnt/d/Documents/Cyber Brain/10 Projects/New Off2On RL"
images_dir = "/mnt/d/Documents/Cyber Brain/40 Resources/42 Assets/Images/New Off2On RL"
profile_note = "/mnt/d/Documents/Cyber Brain/10 Projects/New Off2On RL/expnote 项目配置.md"   # absolute; the registry only points to it
workspaces = ["off2on-baseline"]                # expnote workspaces whose runs this project joins
quiet_window_seconds = 10
```

Per settled decision 4 all paths are stored absolute. `project register` requires `--project-dir`, `--images-dir` and `--profile-note` to lie inside `--vault-root` (else `USAGE_PATH_OUTSIDE_VAULT`), because link resolution is vault-wide. The index stores only vault-relative paths, and `project rebind` rewrites the common prefix of all four registry paths in one step (drive letter or mount change).

Two projects in one vault share a `vault_root` and therefore one `vault_id` and one index (resolution scans all of the vault's about 1,917 files [FACT, inventory]; two indexes would duplicate it).

### 3.3 Path revalidation (`/mnt/d` vs Windows paths)

Every research command starts with `revalidate(project)` (a few `stat` calls):

1. `vault_root/.obsidian` is a directory and `project_dir` exists; the profile note exists, parses, and its `project_id` equals the registry id.
2. If not, try `vault_root_alternates` in order, including the mechanical conversion `D:/x` <-> `/mnt/d/x`. The first that passes is used for this run and reported in `meta.pathNote`; it is persisted only by `project rebind`.
3. Otherwise exit 6 `VAULT_UNAVAILABLE` with remediation `expnote project rebind off2on --vault-root <path>`.

The index never stores absolute paths (only `vault_id` and vault-relative paths), so a rebind never invalidates it.

### 3.4 Profile contents for Off2On

The profile is data, held in the frontmatter of the profile note (YAML nested maps); module code contains no Chinese heading text, no `o2o_`, no `PLAN-` regex. In the table below `[section] key` denotes the YAML path `section.key` (for example `[images] required_prefix` is `images:` then `required_prefix:`); paths inside the profile are relative to the project dir. Illustrative excerpt (values for Off2On; not a verbatim file):

```yaml
---
type: project-profile
schema: "3"
project_id: "off2on"
images: {required_prefix: "o2o_"}
headings: {rules: "规则摘要（须确认）", labels: ["看图标签", "用户批注"], observation: "观察"}
decisions: {id_prefix: "O2O-DEC", strict_from: "123", layers: ["研究", "工程"]}
paths: {deviation_register: "ops/偏离登记.md", todo_file: "待办.md", decisions_dir: "decisions"}
runs: {id_regex: "^[a-z0-9]{8}$"}
---
```

Every value is read as a string by the strict loader and coerced by the profile reader. Vocabularies (status tuples) stay in module code as the generic lifecycle (renaming states would change gate semantics; adding a profile layer for them is future-facing, so not done). Every hard-coded constant of vault.py and where it goes:

| vault.py constant / literal | Value | Destination |
|---|---|---|
| `PROJECT_NAME` | "New Off2On RL" | registry `name`; frontmatter `project:` value written by `new` |
| `IMAGES_REL` | `40 Resources/42 Assets/Images/New Off2On RL` | registry `images_dir` |
| `o2o_` prefix literal in `check_links` | `o2o_` | profile `[images] required_prefix` (images with this prefix must live under `images_dir`) |
| `PREVIEW_DIRS`, `PREVIEW_PREFIX`, `canonical()` prefix strip, `is_preview`, `--overlay-preview`, `_refactor_preview` | preview folders | dropped: dry-run is in memory (section 6); profile `[scan] ignore_globs` contains `**/_refactor_preview/**` only until migration deletes the folder |
| `SKIP_DIR_NAMES` | `.git .obsidian .trash __pycache__ .pytest_cache` | core default plus profile `[scan] skip_dirs` (extends) |
| `REGISTRY_SKIP_PARTS` / `LINKCHECK_SKIP_PARTS` | `templates`, `_refactor_preview` | profile `[scan] template_dirs = ["templates"]` (parsed but excluded from entity registry and link check) |
| literal `"归档"` in `plan_page` and the duplicate-id check | archive folder name | profile `[scan] archive_dir_names = ["归档"]` |
| `DECISION_LOG`, `RESEARCH_VIEW` | `决策记录.md`, `决策记录--研究视图.md` | removed from the engine; profile `[legacy] decision_table = "决策记录.md"` used only by `migrate.py` |
| `DEVIATION_LOG` | `ops/偏离登记.md` | profile `[paths] deviation_register` |
| `DECISION_CUTOVER` | 123 | profile `[decisions] strict_from = 123` (decisions below it are `legacy` and exempt from body-section rules) |
| `LEGACY_STATUS_RULES` (regex table) | 6 rules | profile `[legacy] decision_status_rules` (used by migration and the parity harness) |
| 7-column decision table | `# 日期 决定 理由 落到 状态 层` | profile `[legacy] decision_columns` (migration only) |
| layer values | `研究`, `工程` | profile `[decisions] layers = ["研究","工程"]`; `layer` is required on decision files |
| `STATUS_START/END` | `<!-- vault:status:start/end -->` | profile `[forbidden] markers`; present = finding `GEN.STATUS_TAIL_PRESENT` |
| `TEMPLATE_DOC_START/END` | `<!-- vault:template-doc:... -->` | profile `[templates] doc_markers` (block stripped by `new`) |
| `RULES_HEADING` | `规则摘要（须确认）` | profile `[headings] rules` |
| `LABEL_HEADING_KEY(S)` | `看图标签`, `用户批注` | profile `[headings] labels` |
| `OBSERVATION_HEADING_KEY` | `观察` | profile `[headings] observation` |
| `LABEL_CN` display strings | 已填/部分/未填/无标签节 | GUI i18n table in profile `[display]` |
| label "first value column" rule in `label_state` | column index 1 | profile `[labels] value_columns = [1]` |
| `first_clause` delimiters | `：。；` and CJK brackets | profile `[decisions] title_delimiters` (used by migration to derive titles) |
| exec-plan name prefixes in `effective_type` | `执行细则-- 设计说明-- 执行方案--` | dropped (type-based only) |
| `LEGACY_TYPE_MAP` | `result-card -> result` | profile `[legacy] type_map` |
| `LEGACY_RELAXED` | evidence: exec_plan, figures, appendix | core: `legacy: true` relaxations are defined per entity in `schemas.py` |
| `LEGACY_RES_STATUS` | published->confirmed, draft->first-read, pre-registered->first-read, withdrawn->withdrawn | profile `[legacy] res_status_map` |
| `IMPACT_CN` | 无/弱/中/影响结论 | profile `[deviation] impact_cn` |
| `DEV_FOUND`, deviation columns | 见数据前/见数据后/不适用; headers 编号 日期 实验 偏离内容 对结论有效性的影响 处置 相关实验卡 状态 发现时点 | profile `[deviation] found_values`, `[deviation] columns` (matched by header name, not position) |
| `D-\d+` | deviation id regex | profile `[deviation] id_regex` |
| `PLAN-xxx`, `PLAN-xxx-Sn`, `RES-PLAN-xxx` id shapes | | profile `[ids] plan_regex`, `exec_regex`, `res_prefix`; decision id prefix `O2O-DEC` |
| file-name patterns and folders in `cmd_new` | `{id}--{title}`, `执行方案--{pid} {title}`, `EV--...`, `附表--...`, `RES--...`, folders plans/ops/evidence/results | profile `[names]` and `[folders]` |
| `COMMON_KEYS` | project date created updated revised aliases tags related author cssclasses decision description | core default; profile `[schema] extra_common_keys` |
| `ATTACHMENT_EXTS` | png ... ipynb | core default; profile extends |
| run-id shape (new) | 8 chars `[a-z0-9]` | profile `[runs] id_regex` (section 5.4) |

Profile location and versioning (resolved, 13.2): the profile is a static, hand-edited vault note, not code and not generated, so it does not violate "vault = storage only"; vault git history versions it. Machine-specific absolute paths never enter the note; they stay in `projects.toml`, which points to the note. `doctor` checks that the note parses, that required keys are present, and that its sha256 equals the one recorded in the index.

### 3.5 Project identity and workspace linkage

The separate manifest is folded into the profile note: it carries `project_id` and `schema: 3`, so the index and `doctor` detect a registry bound to the wrong folder and a rebuild on a new machine rediscovers the project id from the vault. Decision: the MOC (`New Off2On RL MOC.md`, `type: project-moc` [FACT]) gets **no** new frontmatter keys. Reasons: one place for identity and conventions; one fewer edit to a note the user reads; the MOC stays a hand-written hub. `new profile` scaffolds the note for a fresh project and is the only way a project is bootstrapped from the CLI (register needs the note to exist).

`project register --workspace off2on-baseline` also records `projection = "off"` in that workspace's `config.toml`; `sync markdown` and `sync all` then refuse with `PROJECTION_DISABLED` unless `--allow-projection` is passed (opt-in). `markdown table` commands are not affected.

## 4. Entity schemas (schema 3)

Common rules (unchanged from schema 2 unless noted): strict YAML loader (every scalar stays a string, duplicate keys rejected, unparsable frontmatter is a finding, never silently repaired); keys ASCII; wikilinks and run ids always quoted; `legacy: true` relaxes the gates listed per entity. Writer roles: **engine** = CLI/GUI only, never hand-edited; **coordinator/agent** = agents may write; **user** = user (GUI, Obsidian, or CLI with user authorization).

### 4.1 What changes versus schema 2

| # | Change |
|---|---|
| 1 | `schema: 3`. Until P4 completes the engine reads 2 and 3 (finding `SCHEMA.OLD`, INFO) and writes only 3. |
| 2 | No generated status tail. `<!-- vault:status:... -->` markers are a WARN before P4 and a FAIL after (`GEN.STATUS_TAIL_PRESENT`). Plan bodies are 100% human-owned. |
| 3 | Decisions are files (4.7); decision references in typed fields become ids (`O2O-DEC-0121`). Readers also accept the schema-2 forms `121` and `#121` and normalise them, so unmigrated notes stay checkable. |
| 4 | Plan status vocabulary gains terminal `superseded` and `withdrawn` (with `superseded_by`/decision). Needed because [FACT] legacy plans use `awaiting-approval` (PLAN-001), `superseded` (PLAN-002) and `approved` while finished (PLAN-002A), none of which are in vault.py's `PLAN_STATUS`. |
| 5 | Decision gains `confirmed_by`/`authorization` (mirrors EV `labelled_by`/`label_authorization`). |
| 6 | A static profile note (`type: project-profile`, nested YAML allowed for this type only) carries the project profile and `project_id`; the MOC is not touched. |
| 7 | New content check `PROSE.RAW_NUMBERS` (WARN, section 7.3). Not in vault.py. |
| 8 | Findings have stable codes (section 6.4). |

### 4.2 Project profile note

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `type` | str | yes | user | `project-profile` |
| `schema` | int | yes | user | 3 |
| `project_id` | str | yes | user | equals the registry id |
| other keys | nested maps / lists / strings | per 3.4 | user, coordinator | validated against the profile key list in `schemas.py`; unknown keys are INFO |

The tool never writes this note. Its body is free prose (explaining local conventions to the human reader).

### 4.3 Plan (`type: plan`, human-readable plan page)

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `schema`, `type` | | yes | engine | |
| `id` | str | yes | coordinator | `PLAN-xxx[-suffix]`, profile regex |
| `title` | str | yes | coordinator | |
| `status` | enum | yes | CLI `plan advance` | draft, approved, running, read-out, confirmed, closed, superseded, withdrawn |
| `owner`, `parent` | str, link | no | coordinator | |
| `approved_decision` | decision-id | status >= approved | CLI | |
| `closed_decision` | decision-id | status = closed | CLI | |
| `result` | link | status >= confirmed | CLI | the final RES |
| `superseded_by` | link | status = superseded | CLI | |
| `legacy` | bool | no | coordinator | exempts exec-plan/EV gates; demotes the confirmed-RES gate to WARN while the RES is not confirmed (4.6) |

Body: human prose only. Sections of the current template (问题, 范围与非目标, 要检验什么, 怎样读结果, 备选方案) are convention, not enforced.

### 4.4 Exec-plan (`type: exec-plan`)

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `id` | str | yes | coordinator | `PLAN-xxx-Sn` |
| `plan` | link | yes | coordinator | |
| `stage` | str | yes | coordinator | |
| `status` | enum | yes | CLI | draft, frozen, running, done, aborted (`closed` legacy only) |
| `servers`, `expnote_topic` | strs, str (quoted) | no | coordinator/agent | |
| `rules_decision` | decision-id | status >= frozen | CLI | user-confirmed rules |
| `rules_sha256`, `frozen_on` | sha256, date | status >= frozen | engine | written only by `exec freeze` |
| `rules_amended_by` | strs (D-xx) | no | CLI | each must exist in the deviation register |
| `legacy` | bool | no | coordinator | exempts rules section and freeze |

Body must contain a heading starting with the profile's rules heading (`规则摘要（须确认）`). Hash input = that section excluding its heading, LF, trailing whitespace stripped, NFC, leading/trailing blank lines dropped (unchanged `normalize_for_hash`).

### 4.5 Evidence (`type: evidence`) and appendix (`type: evidence-appendix`)

Figures enter the vault by direct file copy, not through a structured edit: the agent copies each PNG into `images_dir` (the absolute path is `project show` -> `absImagesDir`) with the profile's required prefix (`o2o_`), under a name unique in the whole vault, then embeds it as `![[name.png]]` in the EV body and lists it in `figures`. The write layer's CAS and quiet window do not apply to new image files; `check` validates afterwards (`LINK.IMAGE_UNRESOLVED`, `LINK.IMAGE_OUTSIDE_DIR`, `NAME.DUPLICATE_ATTACHMENT`, `EV.FIGURE_NOT_EMBEDDED`), and `doctor` lists prefixed images outside `images_dir`.

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `plan` | link | yes (legacy: inferred) | agent | |
| `exec_plan` | link | yes (legacy relaxed) | agent | must be `type: exec-plan` |
| `question`, `runs`, `figures` | str, strs (quoted), strs (quoted) | runs no, figures yes (legacy relaxed) | agent | figures must resolve and be embedded |
| `status` | enum | yes | agent -> user | rendered, labelled, label-na |
| `appendix` | link | yes (legacy relaxed) | engine | twin; twin's `parent` points back |
| `labelled_by` | enum user / agent-for-user | status = labelled | GUI/CLI | |
| `labelled_on` | date | no | GUI/CLI | |
| `label_authorization` | decision-id or quote | agent-for-user | coordinator | must resolve to a confirmed decision when it is an id |
| `legacy`, `legacy_status` | bool, str | no | migration | `legacy_status` keeps the pre-migration status text |

Appendix: `parent` (link, engine), `plan` (link), `runs` (strs), `expnote_snapshot`, `generated_by` (strs, engine). Body sections of the EV page: figures, at most one endpoint table, the label table under a label heading, the observation heading (empty until labelled). Label table = first table in the label section; a row counts as labelled when its first value column is non-empty.

### 4.6 Result (`type: result`, final RES)

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `id` | str | yes | coordinator | `RES-PLAN-xxx` for finals; legacy cards get `RES-<slug of file name>` |
| `plan` | link | yes | coordinator | |
| `status` | enum | yes | CLI | first-read, confirmed, superseded, withdrawn |
| `evidence` | links | yes | coordinator | |
| `decisions` | decision-ids | no | coordinator | |
| `confirmed_decision` | decision-id | status = confirmed | CLI | that decision must link the RES (via `targets` or body) |
| `deviation_impact` | enum none/weak/moderate/affects | status = confirmed | CLI | |
| `superseded_by` | link | status = superseded | CLI | same plan |
| `reviewed_by`, `legacy` | str, bool | no | | `legacy: true` result: no `confirmed_decision`/`deviation_impact` required (INFO) |

At most one non-superseded, non-withdrawn RES per plan (`LIFE.RES_MULTIPLE_ACTIVE`). Legacy stage cards of a closed plan end `superseded` with `superseded_by` = the plan's drafted final RES (section 11).

### 4.7 Decision (one file per decision)

| Aspect | Design |
|---|---|
| Folder / filename | `decisions/<ID>--<slug>.md`; stem unique vault-wide (the id prefix alone makes it unique, so slug collisions cannot occur). Example: `decisions/O2O-DEC-0121--PLAN-003A 最终 RES 判读确认.md`. **Slug rule** (the target is NTFS through drvfs, and [FACT] first clauses contain `/` (#1 "Q-contrast / Q-cliff"), `#N`, `[[...]]` and `$...$`): take the title, strip `[[ ]]` and keep the alias or stem text, drop `$...$` math and `#N` tokens, replace each of `/ \ : * ? " < > | # ^ [ ]` and control characters with a space, collapse whitespace, NFC, truncate to 40 characters on a character boundary, trim trailing dots and spaces. The full title lives only in frontmatter `title`. |
| Id scheme | `id = <profile prefix>-<4-digit number>`, prefix `O2O-DEC` for Off2On; `number` is the original `#N` (1..124 preserved; 125+ allocated as max+1 by `new decision`). Reasons for the prefix: [FACT] `D-NN` is already deviations; [FACT] `50 Archive/Projects/Q-Contrast/decisions/DEC-001..005--*.md` exist, and filename uniqueness is vault-wide and multi-project. The id is also an `aliases` entry, so `[[O2O-DEC-0121]]` resolves in Obsidian whatever the title. |
| Reference forms | typed fields: id (or legacy `121`); prose: `[[O2O-DEC-0121]]`, or the legacy hub form `[[决策记录]] #121` (section 5.5). A bare `#121` in prose is not parsed. |

| Key | Type | Req | Writer | Notes |
|---|---|---|---|---|
| `id`, `number` | str, int | yes | engine | |
| `date` | date | yes | engine | creation date |
| `title` | str | yes | agent | one sentence, readable alone; includes plan ids |
| `status` | enum | yes | CLI/GUI | proposed, confirmed, superseded, rejected |
| `layer` | enum (profile) | yes | agent | 研究 / 工程 |
| `plans` | strs | no | agent | plan ids this decision concerns (replaces vault.py's regex guess over 落到/决定 text) |
| `targets` | links | no | agent | the old 落到 column; notes affected |
| `supersedes` | decision-ids | no | agent | back-reference required by the check |
| `superseded_by` | decision-id | status = superseded | CLI | |
| `confirmed_by` | enum user / agent-for-user | status = confirmed | GUI -> user; CLI -> agent-for-user | |
| `authorization` | str | agent-for-user | CLI | verbatim user words or id of an earlier confirmed decision (mirrors #120, #123) |
| `confirmed_on` | date | status = confirmed | engine | |
| `body_sha256` | sha256 | status = confirmed | engine | WARN `DEC.BODY_CHANGED` if the body changes after confirmation (ADR immutability; my addition) |
| `review_by` | date | no | agent | surfaces as a derived TODO when due |
| `legacy`, `legacy_status` | bool, str | migrated rows | migration | `legacy_status` holds the original status cell; legacy = number below `strict_from` |

Body (MADR-like, headings from the profile): `## 决定` (outcome), `## 理由` (context and drivers; decision-makers line), `## 备选` (considered options) and `## 落到` (consequences) are optional. Required for non-legacy decisions: `## 决定` and `## 理由` only (`DEC.SECTION_MISSING`, FAIL). A missing `## 备选` is `DEC.NO_OPTIONS`: WARN for layer 研究, INFO otherwise, never FAIL. Transitions: proposed -> confirmed -> superseded, or proposed -> rejected. After confirmation only `status`, `superseded_by` and the audit-owned keys change; the body is not edited (amend by superseding).

### 4.8 Deviation register (recommendation: keep the single table)

Keep `ops/偏离登记.md` as one table (37 rows, [FACT]) in schema 3, parsed by header name via the profile (not by position). Justification:

- The GUI never writes deviations, so the table's GUI-side fragility does not apply.
- Rows are appended once and closed once; 62 notes link `[[偏离登记]]` [FACT], and `D-xx` ids are already the stable cross-reference form.
- Per-file would add 37 files and a second migration for little gain now.
- Risk accepted: long rows are awkward to edit; mitigated by `deviation add|close` doing a keyed single-row edit under CAS (P3).

The deviation store sits behind one interface (`DeviationStore`: `list/get/add/set_status`), so a per-file store can replace it later without touching lifecycle code. Resolved (13.5): the user accepted the single table.

### 4.9 Manual TODO file and line grammar

One file per project, `待办.md` in the project dir (profile `[paths] todo_file`), frontmatter `type: todo-list`. Grammar (Dataview-style inline fields; renders as plain text in Obsidian without any plugin, and [FACT] `.obsidian/community-plugins.json` lists no Tasks plugin):

```
- [ ] <free text, may contain [[links]]> [id:: T-0007] [plan:: PLAN-004A] [due:: 2026-10-25] [by:: agent]
- [x] <text> [id:: T-0003] [done:: 2026-10-21]
```

Rules: one task per line; fields are `[key:: value]` after the text; ids `T-NNNN` allocated max+1 by the tool; indented lines are continuation text; a line without `[id:: ...]` is accepted by the index (synthetic id = `T~` + first 8 hex of the line sha) and receives a real id at its first tool edit (ticking or `todo adopt`). Allowed edits: add a line, flip `[ ]`/`[x]` and set/remove `[done::]`, nothing else.

### 4.10 Theory and other notes

Light schema: `type` (any string), optional `schema`, `plan`, `status`, `aliases`; indexed (FTS, links, images) and link-checked, no lifecycle. Covers hypothesis, execution-log, verification-report, coordinator-handover, audit, discussion, research-analysis, engineering-* and the other long-tail types ([FACT] each at most 7 notes in the inventory's `type:` distribution). `progress-board` is no longer a known type (generic note, INFO).

## 5. Index

### 5.1 Location and key

`~/.local/share/expnote/research/index/<vault_id>/index.sqlite` (+ `-wal`, `-shm`, `index.lock`), keyed by the registry's `vault_id`, on the local ext4 filesystem, never in the vault and never on `/mnt/d` (SQLite WAL needs shared memory on one host, and a cross-OS mount is a corruption hazard; survey section C). One index per vault, shared by all projects registered on it. Disposable: `expnote reindex --full` rebuilds from files; an `index_schema` mismatch triggers an automatic rebuild. Single writer via `flock(index.lock)`; readers use `mode=ro`.

### 5.2 Tables

| Table | Columns (key ones) | Purpose |
|---|---|---|
| `meta` | key, value | `index_schema`, `vault_id`, `built_at`, `engine_version`, `profile_sha256` per project |
| `files` | id, rel (vault-relative, NFC), kind (`md`/`attachment`/`other`), size, mtime_ns, sha256, project_id, is_conflict, ignored_reason | every file in the vault walk |
| `notes` | file_id, stem, cstem, type, entity_id, schema, status, title, plan_id, fm_json, fm_error, body, body_sha256 | one row per `.md` |
| `aliases` | file_id, alias_cstem | frontmatter `aliases` |
| `headings` | file_id, level, text, line | outline, section anchors |
| `links` | src_file_id, kind (`link`/`embed`), raw, target_stem, resolved_file_id, resolution (`file`/`alias`/`run`/`none`), line, in_frontmatter | link graph; backlinks = reverse query |
| `decision_refs` | src_file_id, number, form (`field`/`hub`/`id-link`), line | decision citations (5.5) |
| `decisions`, `deviations`, `todos` | parsed entity rows (number/status/layer/..., D-id/status/found/..., id/state/line/line_sha) | fast timeline, TODO and bundle queries |
| `fts_notes` | FTS5 over (stem, title, headings, body) | search |

Images are `files` rows with `kind='attachment'` plus `links` rows with `kind='embed'`; orphan images = attachments with no inbound embed. Thumbnails are not stored.

### 5.3 Search tokenizer

[FACT] SQLite 3.53.2 here supports `fts5(... tokenize='trigram')` (checked). The default `unicode61` tokenizer does not segment CJK, so Chinese search would return nothing useful; the FTS table uses `trigram` (case-insensitive). Queries shorter than 3 characters (common for Chinese 2-character terms such as the entropy term) fall back to `LIKE` over `notes.body` and are marked `"matchMode":"like"` in the result. `doctor` verifies trigram support (needs SQLite >= 3.34) and reports `ENV_FTS_TRIGRAM_MISSING` otherwise, in which case search degrades to `LIKE` entirely.

### 5.4 Incremental rebuild, links, runs

- Refresh runs at the start of every read command (skip with `--no-refresh`): walk the vault (skip dirs per profile), compare `(size, mtime_ns)` per file; on change compute sha256; same sha only updates mtime; different sha reparses that file. Deleted files drop their rows. Any add/delete/rename of a file or alias re-resolves all links in one set-based pass (cheap), because resolution depends on the global name set. Target: incremental refresh under 3 s on the real vault [UNVERIFIED: drvfs `stat` cost over about 1,900 files is not measured; P1 gate measures it].
- Resolution rules are vault.py's: stem lookup, NFC + casefold, alias-aware, path-suffix targets, code spans/fences/HTML comments stripped before extraction.
- **Run links.** [FACT] the project holds about 628 lines in 4 notes with `[[8-char id]]` links (281 distinct ids; the three PLAN-002 EVs and `执行日志--PLAN-002B`), and a whole-vault scan finds 1,056 link occurrences in 18 notes pointing into the projection, resolving today only because the 684-file projection exists; `[[run_id]]` is also expnote's documented convention. A link whose stem matches the profile's `run_id_regex` and exists in a linked workspace DB (or equals a workspace `docs.id`) resolves as `resolution='run'` / `'doc'` (read-only lookup at refresh time, cached in `links`). Otherwise it is unresolved as before. Obsidian itself will show these as dead links; migration rewrites them (section 11).
- **Runs join (decision: in-process read-only SQLite, not the JSON CLI).** `runs.py` opens each linked workspace with `expnote.db.connect_readonly` and reads `runs` (id, topic, status, result, updated_at) for ids named in `runs:` frontmatter and `[[id]]` links. Reasons: same package and version, so no schema-drift contract to freeze; no subprocess cost inside a bundle; `connect_readonly` already exists. Run rows are not copied into the vault index (they change independently); the bundle reads them live. A missing/locked DB yields `warnings: RUNS_UNAVAILABLE`, never a failure. [FACT] run `status` is free text (`finished`, `completed`, `done`, ...) so the bundle passes it through verbatim.
- **Conflict files.** Any file whose name matches `*.sync-conflict-*`, `*conflicted copy*`, `* (conflict *)*`, or a plugin-style duplicate suffix is recorded with `is_conflict=1`, excluded from `notes`, links, FTS and uniqueness checks, and surfaced by `doctor` and as a derived TODO. The sync client is a Windows WebDAV app (name unknown, 13.6), so its conflict-file naming is [UNVERIFIED]; the patterns above come from Syncthing and WebDAV plugins and the list is profile-extensible (`scan.conflict_globs`).

### 5.5 Decision references in prose

Only these forms are parsed into `decision_refs`: typed frontmatter fields; `[[O2O-DEC-NNNN...]]` links; and the legacy hub form `[[决策记录]] #N` plus the comma list that follows it (`#81、#83、#84`). [FACT] this form occurs about 342 times in 110 notes (365 `[[决策记录]]` occurrences). A bare `#N` elsewhere is never treated as a decision (it also appears as issue/step numbers).

## 6. CLI surface

### 6.1 Conventions (all research commands)

- `--json` flag everywhere; stdout = data only; stderr = progress/diagnostics; `--fields a,b` selects top-level keys of each item; `--limit/--cursor` on lists.
- Envelope: success `{"ok":true,"schemaVersion":1,"command":"plan show","data":{...},"warnings":[{"code","message"}],"meta":{"project":"off2on","index":{"refreshed":true,"filesChanged":3},"pathNote":null}}`; failure `{"ok":false,"schemaVersion":1,"command":"...","error":{"code","message","remediation","retryable","details"},"warnings":[]}`. Additive-only evolution.
- Exit codes: 0 ok; 1 findings/gate failed (`check` with FAIL, `plan advance` gate); 2 usage; 3 not found or ambiguous or unregistered; 4 dry-run, nothing written (needs `--apply`); 5 conflict (CAS mismatch, recently modified, file busy); 6 environment (vault unavailable, missing dependency, index locked); 7 refused as user-only.
- Vault-writing commands are dry-run by default: they return `data.changes[]` (`path`, `op`, `beforeSha256`, `afterSha256`, `unifiedDiff` truncated) and `data.confirmCommand`, exit 4. `--apply` performs the write. GUI actions apply immediately because the click is the confirmation.
- No prompts, no fuzzy command correction. Writing commands accept `--expected-sha256` (from a previous `show`); without it the sha read at command start is the CAS base.
- Existing commands keep bare `_emit` output. Migrating them to the envelope is out of scope (resolved, 13.7).
- Reads auto-refresh the index (`--no-refresh` to skip). `--project ID` everywhere, default `active_project`.

### 6.2 Name collisions with the existing 73 commands

[FACT] existing top-level: `init guide validate web` and groups `topic run relation artifact doc moc benchmark markdown sync import workspace`. New top-level: `project reindex search check doctor plan exec decision ev res todo deviation next new`. No exact collision. Notes: (1) `guide` exists with only the `agent` topic: extend `_AGENT_GUIDE` and add topic `research` rather than a second command; (2) `check` overlaps `validate` semantically: `validate` stays run/workspace health, `check` is research-content health (help text cross-references); (3) `init` stays workspace-only, `project register` is the research equivalent; (4) `new decision` is canonical and `decision new` an alias to the same function; (5) existing options `--workspace/--workspace-dir` are unrelated to `--project`; (6) the group name `doc` (analysis docs) is unrelated to `decision`.

### 6.3 Commands

W = writes the vault (dry-run default). Registry/index writes are not vault writes.

| Command | Args / options | `data` shape (top level) | W |
|---|---|---|---|
| `project register` | `--id --vault-root --project-dir --images-dir --profile-note PATH --workspace* --name` | `{project, absVaultRoot, absProjectDir, absImagesDir, absProfileNote, profileSha256, workspaces, profile:{found,parsed,projectId}}` | no |
| `project list` / `show ID` / `use ID` / `rebind ID --vault-root` | | list: `{projects:[{id,name,active,vaultOk}]}`; show: registry entry + resolved paths + profile sha + index stats | no |
| `reindex` | `--full --project` | `{files, notes, linksResolved, linksUnresolved, images, elapsedMs, rebuilt}` | no |
| `search QUERY` | `--type --plan --status --linked-to STEM --backlinks-of STEM --images --limit` | `{matchMode, hits:[{stem,path,type,entityId,status,line,snippet}]}` (snippet as `path:line: text`) | no |
| `check` | `--plan ID --level fail,warn,info --code CODE* --fields` | `{summary:{fail,warn,info}, findings:[{level,code,area,where,message,data}], scope}`; exit 1 when fail > 0 | no |
| `doctor` | `--fix-list --probe-sync [--hold-seconds N] [--apply]` | `{checks:[{id,status,message,remediation}]}` environment and hygiene (6.5) | no |
| `plan list` | `--status --legacy` | `{plans:[{id,title,status,legacy,path,openItems,decisionCount,evCount,resStatus}]}` | no |
| `plan show ID` | `--include-body` | plan frontmatter, sha256, outline, gates summary | no |
| `plan bundle ID` | `--depth 1\|2 --include-body --sections a,b --max-bytes N --out FILE --fields` | 6.4 | no |
| `plan advance ID` | `--to STATUS --decision ID --expected-sha256 --apply` | `{from,to,gates:[{code,ok,message}],changes}`; exit 1 `GATE_FAILED`; writes `status` plus the gate key it needs (`approved_decision` for approved, `result` for confirmed, `closed_decision` for closed) | W (2 keys) |
| `exec list/show` | | like plan | no |
| `exec freeze EXEC` | `--rules-decision ID --amend D-xx --apply` | `{rulesSha256,frozenOn,matchesPrevious,changes}` | W (3 keys + amend) |
| `exec advance EXEC` | `--to running\|done\|aborted` | | W (1 key) |
| `decision new` / `new decision` | `--title --layer --plans --targets --status proposed --body-file` | `{id,number,path,changes}` | W (new file) |
| `decision list` | `--plan --status --layer --since` | `{decisions:[{id,number,date,status,layer,title,plans,confirmedBy}]}` | no |
| `decision show ID` | `--include-body` | frontmatter + body, refs-in (who cites it) | no |
| `decision set ID` | `--status confirmed\|rejected\|superseded --authorization TEXT --by ID --apply` | `{changes}`; confirm via CLI requires `--authorization` and records `confirmed_by: agent-for-user` (exit 7 without it) | W (3-4 keys) |
| `ev list` | `--plan --status --label-state` | `{ev:[{stem,status,labelState,plan,execPlan,figures,appendix,legacy}]}` | no |
| `ev label show/set/commit EV` | `set --row KEY --value COL=TEXT --remark TEXT`; `commit [--authorization TEXT]` | label table as `rows:[{key,values:[]}]`, remark; commit flips status | W (label cells / 3 keys) |
| `ev split EV` | | proposal for 看图页 + 附表 split (port of `split-ev`; P3) | W |
| `res list` | `--plan --status` | `{results:[{id,stem,plan,status,confirmedDecision,deviationImpact,legacy}]}` | no |
| `res confirm RES` | `--decision ID --deviation-impact X --apply` | `{changes}`; requires the decision confirmed and linking the RES | W (3 keys) |
| `todo list` | `--owner user\|agent --kind --plan --source derived\|manual\|all` | `{todos:[{id,source,kind,owner,severity,ref,message,action}]}` | no |
| `todo add TEXT` / `todo done ID [--undo]` / `todo adopt` | `--plan --due --by` | `{changes}` | W (1 line) |
| `deviation list/show` | `--plan --status` | `{deviations:[{id,date,status,found,impact,experiment,cards}]}` | no |
| `deviation add` / `close D-xx` | row fields | `{changes}` (P3) | W (1 row) |
| `next` | `--owner agent` (default) | ordered agent-actionable steps with the exact command to run | no |
| `new plan\|exec\|ev\|res\|decision\|profile` | `--id --title --plan --exec --stage ...` | `{created:[paths], leftoverPlaceholders:[]}` templates read from the vault `templates/` folder (`new ev` also creates the appendix twin); no writes if a target exists | W |
| `guide agent --json` / `guide research` | | built-in guide: lifecycle, id schemes, the five commands an agent needs, exit codes | no |

### 6.4 `plan bundle` JSON (full shape; all values are illustrative, not read from the vault)

```json
{
  "ok": true, "schemaVersion": 1, "command": "plan bundle",
  "data": {
    "plan": {"id": "PLAN-003A", "title": "...", "status": "closed", "legacy": true,
             "path": "10 Projects/New Off2On RL/plans/PLAN-003A--unlearning-mechanisms.md",
             "absPath": "/mnt/d/.../PLAN-003A--unlearning-mechanisms.md", "sha256": "...",
             "owner": "...", "approvedDecision": "O2O-DEC-0115", "closedDecision": "O2O-DEC-0122",
             "result": "RES-PLAN-003A", "summary": "first sentence under the question heading",
             "outline": [{"level": 2, "text": "1. ..."}], "body": null},
    "gates": {"current": "closed",
              "next": [{"to": "superseded", "ok": false, "unmet": [{"code": "LIFE.X", "message": "..."}]}]},
    "decisions": [{"id": "O2O-DEC-0121", "number": 121, "date": "2026-10-21", "status": "confirmed",
                   "layer": "研究", "role": "approval|rules|closing|label-auth|result|other",
                   "title": "...", "confirmedBy": "user", "path": "...", "supersedes": [], "supersededBy": null}],
    "execPlans": [{"id": "PLAN-003A-S1", "stem": "...", "stage": "...", "status": "done",
                   "frozen": true, "rulesShaMatches": true, "rulesDecision": "O2O-DEC-0xxx",
                   "amendedBy": [], "path": "..."}],
    "evidence": [{"stem": "...", "status": "labelled", "labelState": "filled|partial|empty|missing|na",
                  "labelledBy": "agent-for-user", "labelAuthorization": "O2O-DEC-0123",
                  "execPlan": "...", "figures": ["o2o_....png"], "appendix": {"stem": "...", "path": "..."},
                  "runs": ["wyhegqq3"], "legacy": false, "path": "..."}],
    "results": [{"id": "RES-PLAN-003A", "stem": "...", "status": "confirmed",
                 "confirmedDecision": "O2O-DEC-0121", "deviationImpact": "moderate",
                 "supersededBy": null, "evidence": ["..."], "path": "..."}],
    "deviations": [{"id": "D-36", "date": "...", "status": "closed", "found": "见数据后",
                    "impact": "有", "summary": "first clause"}],
    "related": [{"stem": "...", "type": "execution-log|research-analysis|verification-report|coordinator-handover|ops|...",
                 "status": null, "path": "...", "title": "...", "oneLine": "...", "via": "plan-field|inbound-link"}],
    "analysisDocs": [{"id": "...", "workspace": "off2on-baseline", "title": "...", "oneLine": "...",
                      "linkedRuns": ["wyhegqq3"]}],
    "runs": [{"id": "wyhegqq3", "workspace": "off2on-baseline", "topic": "...", "status": "finished",
              "result": "one-line headline", "updatedAt": "...", "citedBy": ["EV stem"]}],
    "openItems": [{"id": "derived:ev-unlabelled:<stem>", "kind": "ev-unlabelled", "owner": "user",
                   "severity": "action", "message": "...", "ref": "<stem>"}],
    "findings": {"fail": 0, "warn": 2, "items": [{"level": "WARN", "code": "...", "where": "...", "message": "..."}]},
    "links": null
  },
  "warnings": [],
  "meta": {"project": "off2on", "bytes": 18234, "maxBytes": 65536, "truncated": false,
           "spillFile": null, "nextCursor": null, "sections": ["plan","gates","decisions","execPlans","evidence","results","deviations","related","analysisDocs","runs","openItems","findings","links"]}
}
```

**One call returns all materials of a plan** (evidence, results, analysis) at the default depth. `related` lists every note that is not a plan-entity (types other than plan, exec-plan, evidence, evidence-appendix, result, decision, todo-list, project-profile) and either has `plan:` resolving to this plan or links to the plan page: theory and analysis notes, execution logs, handover notes, design notes, verification reports, ops documents; template folders and conflict files excluded. Each entry is a mechanical summary `{stem, type, status, path, title, oneLine}` where `oneLine` is the frontmatter `description` if present, else the first sentence of the first paragraph after the H1, copied and truncated to 160 characters (no interpretation); `via` says why it is included. `analysisDocs` are expnote analysis docs (workspace `docs` rows) joined through `doc_runs` to any run the plan's EVs, appendices or exec-plans cite, as summaries `{id, workspace, title, oneLine, linkedRuns}`. Default depth 1 = summaries only for everything (each entry has id, type, status, path, one-line `summary`); `--depth 2` fills `links` with `{inbound: [{stem,type}], unresolved: []}` (the raw backlink list) and adds cited-but-unrelated notes; bodies only with `--include-body`. If the serialized payload exceeds `--max-bytes` (default 65,536), the full JSON is written to `~/.local/share/expnote/research/spill/bundle-<plan>-<sha8>.json` (outside the vault) and the response carries the first sections that fit, `meta.truncated: true`, `meta.spillFile`, and `meta.nextCursor` (next section name). `--out FILE` forces the file. The bundle is computed from the index, the live workspace DB and the current files' shas, so `sha256` values can be fed straight to a write command as `--expected-sha256`.

### 6.5 Finding codes and `doctor`

`check` findings are `{level, code, area, where, message, data}`; `code` is stable (`AREA.NAME`), `message` is free text (in the profile language). Parity with vault.py is compared on `(level, code, where)` (message text excluded) over the vault.py-mapped codes only. Codes with no vault.py counterpart (`GEN.STATUS_TAIL_PRESENT`, which fires on the two schema-2 plans that still carry tails; `PROSE.RAW_NUMBERS`, which fires on non-legacy schema-2 notes such as the final RES-PLAN-003A and on decisions #123/#124; `SCHEMA.OLD`; `DEC.AUTH_WEAK`; `DEC.BODY_CHANGED`) are listed under `data.newCodes` and are suppressed by `--compat vault-py`. Initial code set, one per vault.py check: `FM.PARSE`, `FM.DUPLICATE_KEY`, `NAME.DUPLICATE_STEM`, `NAME.DUPLICATE_ATTACHMENT`, `NAME.RESERVED_CHAR`, `LINK.UNRESOLVED`, `LINK.IMAGE_UNRESOLVED`, `LINK.IMAGE_OUTSIDE_DIR`, `LINK.ORPHAN_IMAGES` (INFO), `DEC.STATUS_VOCAB`, `DEC.SUPERSEDED_NO_SUCCESSOR`, `DEC.NO_BACKREF`, `DEC.NUMBER_DUPLICATE`, `DEC.NOT_CONFIRMED` (a gate cites a non-confirmed decision), `DEV.STATUS_VOCAB`, `DEV.FOUND_VOCAB`, `DEV.OPEN_FOR_PLAN` (WARN), `SCHEMA.MISSING_FIELD`, `SCHEMA.BAD_VALUE`, `SCHEMA.UNQUOTED`, `SCHEMA.OLD` (INFO), `SCHEMA.UNKNOWN_KEYS` (INFO), `SCHEMA.ID_DUPLICATE`, `LIFE.*` (one per gate in section 7), `EXEC.NO_RULES_SECTION`, `EXEC.RULES_CHANGED`, `EXEC.AMEND_UNREGISTERED`, `EV.NO_APPENDIX`, `EV.TWIN_MISMATCH`, `EV.LABEL_*`, `EV.OBSERVATION_BEFORE_LABEL`, `EV.EXTRA_TABLES`, `EV.FIGURE_*`, `RES.*`, `GEN.STATUS_TAIL_PRESENT`, `PROSE.RAW_NUMBERS`.

`doctor` (environment and hygiene, never content): registry/vault reachable; the profile note exists, parses, has its required keys, and its sha256 equals the one recorded in the index; PyYAML and trigram present; index schema and freshness; workspace DBs readable; sync-conflict files; stale `.expnote.*.tmp` temps; dot-directories in the vault (the empty `results/.aws` [FACT]); code files in the vault (`*.py`, `__pycache__`); generated artifacts (status markers, projection folder, research view); filenames with Windows-illegal characters; recently modified files that would block writes. `doctor --probe-sync --apply` (dry-run lists the plan) creates a scratch folder `expnote-probe/` inside the project dir, writes one dot-prefixed temp file named like the write layer's temps (`.expnote.probe.<rand>.tmp`) and one ordinary file, holds both for `--hold-seconds` (default 120) so the user can see in the WebDAV client whether each is uploaded, then removes both and the folder; the report is `data.probe {dir, files, createdAt, removedAt}`. It is the only `doctor` mode that writes the vault, and it is part of the P3 acceptance gate.

### 6.6 Agent workflow (content of `guide research`)

Every writing command below is dry-run until `--apply`; add `--project off2on --json` throughout. `expnote next --json` after each step lists what is actionable and what waits for the user. Agents edit prose bodies and agent-owned frontmatter keys directly with their file tools (paths come from `data.created`); structured keys marked CLI-only go through the commands.

**(a) Start a plan**

1. `expnote new plan --id PLAN-004A --title "..." --apply` (copies `templates/模板--人读方案.md`, strips template-doc and forbidden markers). Edit the body.
2. `expnote decision new --title "PLAN-004A 第一步批准：..." --layer 研究 --plans PLAN-004A --targets "[[PLAN-004A--...]]" --body-file approval.md --apply` creates a `proposed` decision; it shows up as the user's TODO `decision-proposed`.
3. The user approves in the GUI, or says so in chat; in the latter case the agent records it: `expnote decision set O2O-DEC-0125 --status confirmed --authorization "<user's words>" --apply`.
4. `expnote plan advance PLAN-004A --to approved --decision O2O-DEC-0125 --apply`.

**(b) Run a stage**

1. `expnote new exec --plan PLAN-004A --stage S1 --title "..." --apply`; write the rules section.
2. `expnote decision new ... --targets "[[执行方案--PLAN-004A S1 ...]]"` for the rules confirmation; user confirms (as in (a) step 3).
3. `expnote exec freeze PLAN-004A-S1 --rules-decision O2O-DEC-0126 --apply` writes `status`, `rules_sha256`, `frozen_on`; then `expnote plan advance PLAN-004A --to running --apply` and `expnote exec advance PLAN-004A-S1 --to running --apply`.
4. Run on the servers; register runs with the existing workspace commands (`expnote run add`, `run update`). Servers never write the vault.
5. Figures: render PNGs locally, copy them into `absImagesDir` (from `expnote project show --json`) with the `o2o_` prefix and a vault-unique name (a plain file copy is allowed; section 4.5).
6. `expnote new ev --plan PLAN-004A --exec PLAN-004A-S1 --title "..." --apply` creates the 看图页 and its appendix twin. Edit: embed figures, fill `figures`/`runs`, endpoint table, appendix tables. Then `expnote check --plan PLAN-004A --json` (FAIL must be 0).
7. The user labels in the GUI (`ev-unlabelled` TODO). If the user instead gives labels in chat: `expnote ev label set EV --row "<arm>" --value "跌落=..." --apply` then `expnote ev label commit EV --authorization "<user's words or decision id>" --apply`.
8. After all EVs are labelled: write the 观察 sections, then proceed to (c).

**(c) Close**

1. `expnote new res --plan PLAN-004A --title "..." --apply`; write the final RES (status `first-read`).
2. `expnote decision new ...` to confirm the RES (`--targets` must link the RES); user confirms.
3. `expnote res confirm RES-PLAN-004A --decision O2O-DEC-0127 --deviation-impact none --apply`.
4. `expnote plan advance PLAN-004A --to read-out --apply`, then `--to confirmed` (writes `result`), then a closing decision and `--to closed --decision O2O-DEC-0128 --apply`. Each step fails with `GATE_FAILED` and lists the unmet gates if a precondition is missing.

## 7. Lifecycle state machines and gates

Legend for "who": **CLI** = agent or user through `expnote`; **GUI** = the write set (section 9); **user-only** = the transition's *cause* is a user act (a confirmed decision); the tool records it but the agent cannot invent it.

### 7.1 Gates (ported from vault.py B.3; codes are the stable finding codes)

| Entity | Transition | Gate (FAIL unless noted) | Code | Who |
|---|---|---|---|---|
| plan | draft -> approved | `approved_decision` resolves to a **confirmed** decision | `LIFE.PLAN_NO_APPROVAL` / `DEC.NOT_CONFIRMED` | CLI after the user-only decision |
| plan | approved -> running | at least one exec-plan of the plan in frozen/running/done (not for `legacy`) | `LIFE.PLAN_NO_FROZEN_EXEC` | CLI |
| plan | running -> read-out | exactly one active final RES; every EV labelled or label-na (not for `legacy`) | `LIFE.PLAN_RES_COUNT`, `LIFE.PLAN_EV_UNLABELLED` | CLI |
| plan | read-out -> confirmed | `result` points at the plan's single active RES, which is `confirmed` | `LIFE.PLAN_RESULT_MISSING`, `LIFE.PLAN_RESULT_NOT_CONFIRMED` (WARN, not FAIL, for `legacy` while the RES is first-read) | CLI |
| plan | confirmed -> closed | `closed_decision` is a confirmed decision | `LIFE.PLAN_NO_CLOSING` | CLI after user-only decision |
| plan | any -> superseded / withdrawn | decision recorded; superseded needs `superseded_by` | `LIFE.PLAN_TERMINAL` | CLI after user-only decision |
| plan | (static) | at most one active RES | `LIFE.RES_MULTIPLE_ACTIVE` | |
| exec-plan | draft -> frozen | rules section exists; `rules_decision` confirmed; `exec freeze` writes `rules_sha256`, `frozen_on` | `EXEC.NO_RULES_SECTION`, `LIFE.EXEC_NO_RULES_DECISION` | CLI only (`exec freeze`) |
| exec-plan | frozen/running/done (static) | recomputed hash equals stored; otherwise a registered deviation must be listed in `rules_amended_by` and re-frozen with `--amend` | `EXEC.RULES_CHANGED`, `EXEC.AMEND_UNREGISTERED` | CLI only |
| exec-plan | frozen -> running -> done / aborted | none beyond the above | | CLI |
| evidence | rendered -> labelled | label table filled (every row's first value column); `labelled_by` set; if `agent-for-user`, `label_authorization` present and (if an id) a confirmed decision | `EV.LABEL_NOT_FILLED`, `EV.LABELLED_BY_MISSING`, `EV.AUTHORIZATION_MISSING`, `DEC.NOT_CONFIRMED` | GUI (user) or CLI with authorization |
| evidence | rendered -> label-na | no label table required | | CLI or GUI |
| evidence | (static) | no observation text while rendered; appendix twin exists and points back; at most one non-label table; figures resolve and are embedded (WARN for not embedded) | `EV.OBSERVATION_BEFORE_LABEL`, `EV.NO_APPENDIX`, `EV.TWIN_MISMATCH`, `EV.EXTRA_TABLES`, `EV.FIGURE_*` | |
| result | first-read -> confirmed | `confirmed_decision` confirmed and linking the RES; `deviation_impact` set | `RES.CONFIRM_NO_DECISION`, `RES.DECISION_NOT_LINKED`, `RES.NO_IMPACT` | CLI (`res confirm`) after user-only decision |
| result | -> superseded | `superseded_by` is an existing RES of the same plan | `RES.SUPERSEDED_INVALID` | CLI |
| result | evidence links | each resolves | `RES.EVIDENCE_UNRESOLVED` | |
| decision | proposed -> confirmed | actor user (GUI) or CLI with `--authorization`; engine writes `confirmed_by`, `confirmed_on`, `body_sha256` | `DEC.AGENT_NO_AUTH` | GUI (user) / CLI agent-for-user |
| decision | proposed -> rejected | same actor rule | | GUI / CLI |
| decision | confirmed -> superseded | successor exists and lists the predecessor in `supersedes` | `DEC.SUPERSEDED_NO_SUCCESSOR`, `DEC.NO_BACKREF` (WARN if successor is a legacy decision) | CLI |
| global | | case-folded filename uniqueness; reserved characters (`# ^ [ ] |`) WARN; duplicate ids WARN; links and image embeds resolve (images FAIL, notes WARN); `required_prefix` images under `images_dir` WARN | `NAME.*`, `LINK.*` | |

`legacy: true` exemptions: exec-plan freeze/rules gates, EV twin/exec_plan/figures gates, plan frozen-exec and EV-labelled gates, result `confirmed_decision`/`deviation_impact`. Legacy never exempts uniqueness, link resolution or frontmatter parsing.

### 7.2 Which transitions the GUI may do

| Transition | CLI | GUI |
|---|---|---|
| decision proposed -> confirmed / rejected | yes (agent-for-user, needs `--authorization`) | **yes** (records `confirmed_by: user`) |
| EV label cells + status -> labelled (`labelled_by: user`) | yes (`--authorization` for agent-for-user) | **yes** |
| TODO add / tick | yes | **yes** |
| everything else (plan advance, exec freeze/advance, res confirm, decision supersede, deviation edits, new notes) | yes | no |

Honest limit: a localhost GUI cannot prove a human clicked. The guarantees are (a) the audit actor field (`gui` vs `cli`, plus `EXPNOTE_ACTOR`), (b) the `confirmed_by`/`authorization` keys making agent confirmations visibly different, and (c) an Origin/Host check plus a per-process token on every GUI write (CSRF and DNS-rebinding defence). This matches the workflow the vault already uses: [FACT] #120 and #122 are user answers in chat recorded by the coordinator with the quote, so the CLI path with `--authorization` is required, not a loophole. `check` WARNs when many decisions are `agent-for-user` without a quote of at least 8 characters (`DEC.AUTH_WEAK`).

### 7.3 Content rules the engine enforces

| Rule | Check | Level |
|---|---|---|
| No 观察 text before labelling | `EV.OBSERVATION_BEFORE_LABEL` | FAIL |
| Raw numbers only in EV cards and appendices, none in RES 判读, plans or decisions | `PROSE.RAW_NUMBERS`: a heuristic over non-legacy `plan`/`result` bodies and decisions with number >= `strict_from`; flags decimals, percentages and integers with units, ignoring code spans, ids (`PLAN-`, `D-`, `#`, `O2O-DEC-`), dates, headings numbering, version/step labels via a profile allow-list `[prose] number_allow_regexes` | WARN only (a heuristic must not block a gate) |
| One final RES per plan | `LIFE.RES_MULTIPLE_ACTIVE` | FAIL |
| Filename uniqueness, link/image resolution | `NAME.*`, `LINK.*` | FAIL / WARN as in vault.py |

[FACT] legacy decision rows already contain numbers (e.g. #32 "50/47.5/45"), hence the cutover and WARN-only.

## 8. Write-back protocol

All vault writes (CLI `--apply`, GUI actions, `new`, migration) go through `writeback.py`:

| # | Rule |
|---|---|
| 1 | **Single programmatic writer.** One write layer, one process-level `flock` (`research/<vault_id>/write.lock`); no other module opens vault files for writing. |
| 2 | **Compare-and-swap.** Each write names `expected_sha256` of the target file. The layer re-reads, hashes (bytes), and refuses on mismatch with `CAS_MISMATCH` (HTTP 409 / exit 5) carrying `currentSha256`, `expectedSha256`, and a diff hint. |
| 3 | **Minimal addressable edits.** A write is one of: set one frontmatter key (text-level line edit; formatting and key order of the rest preserved, no YAML re-dump), edit one table row addressed by key (EV label row, deviation row), edit one task line addressed by `[id::]`, create one new file (decision, note). Never rewrite a whole page body. |
| 4 | **Atomic replace, sync-safe temp names.** Write `.<name>.expnote.<pid>.<rand>.tmp` in the same directory (dot-prefixed so name-based sync filters that skip dot files ignore it; [UNVERIFIED] for this vault's client), `fsync`, `os.replace`. New files use `os.link` (no-clobber) with fallback to exists-check + replace under the lock. On `/mnt/d` (drvfs) `os.replace` can raise `PermissionError` when a Windows process holds the file: retry 3 times with 200 ms backoff, then `FILE_BUSY` (retryable, exit 5). `doctor` deletes stale temps older than 1 hour. |
| 5 | **Write only if bytes differ.** If the new bytes equal the current bytes, do nothing (no mtime bump, no event, `changes: []`). |
| 6 | **Deterministic output.** Same inputs, same bytes: stable ordering, no timestamps except the explicit ones (`confirmed_on`, `labelled_on`, `frozen_on`, `[done::]`), preserve BOM and CRLF/LF of the existing file, NFC only where the profile says. |
| 7 | **Quiet window.** Refuse a write when the target's mtime is within `quiet_window_seconds` (default 10) of now: `RECENTLY_MODIFIED`, retryable, with `retryAfterMs`. Obsidian merges external changes into an open buffer after about 2 s and users report stale-buffer overwrites [survey section C]; the window plus tiny edits plus CAS is the mitigation. `--no-quiet` exists for the CLI only and is audited. |
| 8 | **Audit.** Every applied write appends a record to `research/projects/<id>/events.jsonl` in the existing `append_event` shape (`id`, `type`, `ts`, `payload`): `type` `research.write`, payload = actor (`gui`/`cli`/`migration`), command, path, `beforeSha256`, `afterSha256`, key or row address, authorization text if any, and `journal` = path of a pre-image copy `research/projects/<id>/journal/<ts>-<name>.orig`. Pre-images (kept 30 days) make any write reversible without git. Dry-runs are not audited. |
| 9 | **Immediate re-index** of the touched file(s) after the write, before returning, so the response and the next read agree. |
| 10 | **Structured errors** (envelope `error.code`, HTTP status): `CAS_MISMATCH` 409, `RECENTLY_MODIFIED` 409, `FILE_BUSY` 409, `NOT_FOUND` 404, `ADDRESS_NOT_FOUND` 422 (task/row/key absent), `GATE_FAILED` 422, `USER_ONLY` 403, `VAULT_UNAVAILABLE` 503, `DRY_RUN` (exit 4 only, CLI). |
| 11 | **Batching.** One command's edits to several files run in one burst (sequentially, each atomic); partial failure reports exactly which files changed. There is no cross-file transaction; `plan advance` etc. are single-key edits so none is needed. |

## 9. GUI

### 9.1 Structure decision

`web.py` holds about 1,300 lines of SPA as one Python string `_INDEX_HTML`, loads lucide from unpkg (a CDN dependency in a localhost tool) and is single-workspace (`create_app(root, state_dir)`). Recommendation: do not grow that string. Ship research pages as static assets inside the package (`expnote/research/static/{research.html, research.js, research.css}`, served by FastAPI at `/research/`, listed as hatch package data), plain ES modules, no build step, no CDN, inline SVG icons. The existing run-centric SPA stays untouched, with a nav link to `/research/`. `create_app` mounts the research `APIRouter` whenever the registry has at least one project; the router is registry-driven, not bound to a workspace, so `expnote web` works with research only (no active workspace) or both. `--no-research` disables it.

Markdown rendering: server-side with the `markdown` package plus a wikilink/embed extension (note links to `/research/p/<id>/note/<stem>`, embeds to the image endpoint, `[[run_id]]` to the existing run page, hub `#N` to the decision page). Raw HTML in notes is escaped before rendering and a CSP `default-src 'self'; img-src 'self' data:` is sent, because `markdown` passes HTML through.

### 9.2 Pages

| Page | Route | Content |
|---|---|---|
| Home dashboard (all projects) | `/research/` | per project card: plans by state (counts + list), decisions awaiting user, TODOs (derived + manual, user-owned first), recent runs (from linked workspaces), index freshness, `doctor` red flags |
| Project | `/research/p/<id>` | plan table (status, legacy, open items), decision timeline preview, deviations open, TODO list, search box |
| Plan | `/research/p/<id>/plan/<PLAN>` | rendered human-plan body with vault images; generated status block (never written to the vault): decision timeline (role-tagged), exec-plans with freeze state and hash match, EV table with label state and appendix link, RES table with confirmed decision, deviations, related notes (theory, analysis, logs, handover, design, verification) and linked expnote analysis docs (the same `related` and `analysisDocs` sections as `plan bundle`), open items, linked runs; gates panel (what blocks the next transition) |
| Decision timeline | `/research/p/<id>/decisions` | filter by plan/layer/status (the old 研究视图 is the `layer=研究` filter); supersession arrows; Approve / Reject buttons on proposed rows |
| Decision | `/research/p/<id>/decision/<ID>` | rendered file, refs-in |
| EV | `/research/p/<id>/ev/<stem>` | figures, endpoint table, label form (cells + remark + "Commit labels"), appendix link |
| TODOs | `/research/p/<id>/todos` | derived (read-only, with deep links) and manual (add, tick) |
| Search | `/research/p/<id>/search?q=` | FTS results with snippets, filters by type/plan |
| Note (any) | `/research/p/<id>/note/<stem>` | read-only rendering for theory and other notes |

### 9.3 API

| Method + path | Purpose | Write |
|---|---|---|
| `GET /api/research/projects` | registry + health | no |
| `GET /api/research/projects/{id}/home` | dashboard payload | no |
| `GET .../plans`, `.../plans/{plan}`, `.../plans/{plan}/bundle` | same shapes as the CLI (`plan list/show/bundle`) | no |
| `GET .../decisions`, `.../decisions/{id}` | | no |
| `GET .../ev`, `.../ev/{stem}` | includes `sha256` for CAS | no |
| `GET .../todos`, `.../search`, `.../check`, `.../doctor` | | no |
| `GET .../note/{stem}` | rendered HTML + frontmatter | no |
| `GET .../image/{name}` | vault image, read-only | no |
| `POST .../decisions/{id}/approve`, `.../reject` | body `{expected_sha256, note?}`; sets status, `confirmed_by: user`, `confirmed_on`, `body_sha256` | **W1** |
| `PUT .../ev/{stem}/labels` | body `{expected_sha256, rows:[{key,values}], remark?, commit?:bool}`; `commit` flips `status: labelled`, `labelled_by: user`, `labelled_on` only if every row is filled | **W2** |
| `POST .../todos` ; `POST .../todos/{id}/done` | body `{text,plan?,due?,expected_sha256}` ; `{done: bool, expected_sha256}` | **W3** |

Image serving: resolve the stem through the index to a vault-relative path; require the resolved real path to be inside `vault_root` and `kind='attachment'` with an allow-listed image extension; reject `..`, absolute paths and symlinks escaping the root; `Cache-Control: private, max-age=60`, ETag = file sha. Errors use the structured 409/404/422 forms of section 8. Write endpoints require header `X-Expnote-Token` (random per process, embedded in the served page) and an `Origin`/`Host` equal to the bound address.

[FACT] the existing optimistic-concurrency pattern is `expected_updated_at` with a 409 `HTTPException`; the research API uses `expected_sha256` and the structured body instead because the vault has no `updated_at`.

### 9.4 Derived TODO catalogue (computed, never written to the vault)

| Kind | Condition | Owner | GUI action |
|---|---|---|---|
| `decision-proposed` | decision status proposed | user | Approve / Reject |
| `ev-unlabelled` | EV status rendered, not label-na | user | open EV, label |
| `plan-awaiting-approval` | plan draft whose approval decision is proposed | user | via the decision |
| `exec-rules-unconfirmed` | exec-plan draft with rules section and a proposed rules decision | user | via the decision |
| `res-awaiting-confirmation` | plan read-out/closed-legacy with RES first-read and a proposed decision | user | via the decision |
| `decision-confirmed-not-applied` | confirmed approval/closing/rules/RES decision whose note has not advanced | agent | none (`next` shows the command) |
| `deviation-open` | deviation open | agent/user | none |
| `plan-gate-blocked` | plan advanced past a failed gate (check FAIL) | agent | none |
| `legacy-no-final-res` | legacy closed plan without a confirmed RES | agent/user | none |
| `decision-review-due` | `review_by` passed | user | none |
| `sync-conflict-file` | conflict file present | user | none |
| `stale-running-runs` | workspace `validate` stale runs | agent | none |
| `ev-unmapped` | legacy EV with no `plan:` | agent | none |

Manual TODOs (4.9) are shown beside them with `source: manual`.

## 10. Sync-safety rules

- Index, journal, audit log, spill files and caches live under `~/.local/share/expnote/research/`, never in the vault and never on `/mnt/d`.
- The vault contains only Markdown, images and hand-edited config. No `.py`, `.db`, `.json` caches, `__pycache__`, dot-directories. `doctor` reports violations ([FACT] today: three `.py` files and an empty `results/.aws`).
- Nothing generated is written into the vault. Previews are in memory or on stdout.
- Atomic writes with dot-prefixed temp names in the same directory, `os.replace`; retry on drvfs `PermissionError`, then `FILE_BUSY`. Resolved (13.6): the vault is synced by a Windows WebDAV application outside Obsidian (name unknown; `.obsidian/community-plugins.json` lists only `obsidian-style-settings` and `highlightr-plugin`). [UNVERIFIED] whether it ignores dot-prefixed temps; `doctor --probe-sync` answers this empirically and is a P3 gate. Because the client is a Windows process, it may hold files open, which is the source of the drvfs `PermissionError` retry.
- New image files are copied in directly by agents (section 4.5); they are not covered by CAS or the quiet window, only by `check`.
- Write only when bytes differ; deterministic output; one burst per command to avoid repeated upload cycles.
- CAS on every write; refuse files modified within the quiet window; tiny targeted edits only.
- Conflict files are detected, listed and excluded from the index, never silently resolved or merged.
- Remote servers never write the vault; they hand data to the local workspace.
- No credentials in the tree; `doctor` flags dot-directories such as `.aws`.
- Stable identity: ids live in frontmatter or filenames' first token, paths are locators; renames in Obsidian do not break ids because references use ids (decisions, plans, RES) and link text resolves by stem.
- Migration (section 11) is the only operation allowed to touch many files, runs with the user away from the folder, and takes an external backup first.

## 11. Migration map (P4)

Principles: the user does not edit the project folder and no agent edits notes by hand during migration; a backup outside the vault is taken first; every scripted step is idempotent, produces a reviewable plan (`migration-plan.json` in the research state dir plus a unified diff) and applies through the section 8 write layer (CAS, atomic, audited, journal). Order: M0 backup and golden capture, M1 profile note first, then the scripted mechanical steps, M2 agent-judgement steps with review tables, M3 user confirmations (decisions), M4 verification, M5 deletions.

### 11.1 Per file type

Counts are [FACT] from the inventory or my reads on 2026-10-21 unless marked.

| Current file type / location | What happens | Kind |
|---|---|---|
| Schema-2 plans: PLAN-002C, PLAN-003A (both `legacy: true`) | `schema: 3`; `approved_decision: 115` -> `"O2O-DEC-0115"` (same for `closed_decision`); remove the block between `<!-- vault:status:start/end -->` including surrounding blank lines; everything else byte-identical (verified by diffing the body outside the block) | mechanical |
| Legacy plans: PLAN-001, 002, 002A, 002B, 002B-gammafix (no `schema`, ad hoc keys `approval`, `constraints`, `sibling`, `instrument`) | add `schema: 3`, `legacy: true`, missing `title` (from H1); keep unknown keys; map status: `closed` kept, `awaiting-approval` (001), `superseded` (002), `approved` (002A, finished) are proposals needing the coordinator's reading of the decision rows; `approved_decision`/`closed_decision` derived from `approval` text (`#82、#92` ...) as a proposal; missing closing decisions are drafted as `proposed` decisions | script proposes, **agent judgement**, user confirms via decisions |
| Final RES for closed legacy plans lacking one | agent drafts one final RES per plan with status `first-read` (the closest existing state; I read the user's "proposed" as: the *decision* confirming it is `proposed`, so no new RES status), plus one proposed decision "confirm RES X"; the plan's older stage cards become `superseded` with `superseded_by` = the final. Gate fields (`closed_decision`, `approved_decision` where missing, `result`, `confirmed_decision`, RES `status: confirmed`) are written **only after** the corresponding decision is confirmed; until then the pending items exist only as proposed decisions and derived TODOs (9.4), so M4 (FAIL 0) does not depend on how fast the user clicks and does not block M5. [FACT] stage cards in `results/`: 8 `RES--PLAN-002A*` (including a published `综合`), 6 for PLAN-002B, 2 for gammafix, 2 for PLAN-002; 15 more in `results/归档/` (PLAN-001's E-x cards and `RES--PLAN-001 综合`). An existing `综合` card may be promoted to final instead of drafting a new one | **agent judgement** (drafting), user confirms |
| Exec docs: `ops/执行细则--PLAN-003A 第一步`, two `ops/设计说明--PLAN-003A ...` (schema-2 `type: exec-plan`, 3 notes) | `schema: 3`, decision refs canonicalised; assert recomputed `rules_sha256` equals the stored value (frontmatter edits cannot change it, since the hash covers only the rules section) | mechanical + assertion |
| `ops/设计说明--研究记录体系重构 试点`, `ops/调研--...` | left as is (historical design notes mentioning vault.py); indexed as ops notes | none |
| Schema-2 EV (14 in `evidence/`: 2 for PLAN-002C `legacy: true` unsplit, 12 for PLAN-003A) and appendices (12 in `ops/`) | `schema: 3`; `label_authorization: "123"` -> `"O2O-DEC-0123"`; the `decision: "[[决策记录]] #115"` key kept as free text | mechanical |
| Legacy EV (56 in `evidence/`; many carry only `type/project/date` and no `plan:`; `status:` values across all 70 files in `evidence/` are `current` 25, `draft` 4, `label-na` 4, `labelled` 10, the last two including schema-2 notes) | add `schema: 3`, `legacy: true`, `legacy_status` (original), status mapping `current`/`draft`/absent -> `label-na` (no label table exists), `labelled`/`label-na` kept; `plan:` inferred by a script from the file-name `PLAN-xxx` token, the 数据层参考 link, and RES `evidence:` back-references, written as a **review table** (EV, proposed plan, evidence of inference, confidence); E-x EVs inferred to PLAN-001 and unmatched ones (code audits) need a coordinator decision, some may belong to no plan; `figures` from embeds. No split into 看图页 + 附表 in P4: legacy EVs stay unsplit, `ev split` (port of `split-ev`) is available on demand | script + **agent judgement** for plan membership |
| RES: schema-2 results (3: PLAN-002C, two PLAN-003A) | `schema: 3`, decision ids canonicalised (`confirmed_decision: 121`, `decisions: [...]`) | mechanical |
| Legacy result-cards (34: 19 in `results/`, 15 in `results/归档/`; the 35th `type: result-card` file is the 实验卡模板) | `type: result`, `legacy: true`, `id: RES-<slug of file name>`, status via profile map (published -> confirmed, draft/pre-registered -> first-read, withdrawn -> withdrawn); `plan:` already present in the sample read; superseded as in the final-RES row | mechanical + judgement for supersession |
| `hypothesis` (7: H-01..H-06 in `theory/归档/`, plus the template), `execution-log` (7), `verification-report`/核查报告 (4), `coordinator-handover` (2), audits, discussion, research-analysis, other long-tail `type:` values | `schema: 3` and `plan:` where inferable from the file name; otherwise untouched | mechanical |
| `progress-board` (4 files in `plans/归档/`, 3 typed; 2 more already in `50 Archive/New Off2On RL/进展看板/`) | untouched (archived); type no longer special; `plans/归档` keeps them | none |
| `决策记录.md` (124 rows, 83 KB) | one decision file per row in `decisions/`: id `O2O-DEC-<n>`, `number`, `date`, `layer`, `title` = first clause (profile delimiters), file name from the 4.7 slug rule, body sections from 决定/理由/落到 columns (text verbatim, wikilinks unescaped from table form), `targets` from 落到 links, `plans` from links and plan-id regex, `status` from the current column (already rewritten to the 4-word vocabulary by #124) with the original cell in `legacy_status`, `supersedes`/`superseded_by` parsed from `superseded by #n`/`取代 #m`; the rows that `模板--决策行.md` lists as needing human review (11-14, 78, 82, 85, 87 "部分修订"; 47, 54, 61 execution-status words; 75, 80 unmapped) go into a review table (after the #124 rewrite their status cells begin with `confirmed`, so the vault.py mapper no longer flags them by itself). Round-trip gate: regenerate the 7-column table in memory from the 124 files and compare cell by cell with the source | mechanical; flagged rows **agent judgement** |
| Old `决策记录.md` after conversion | replaced by a short static, hand-written hub note with the same stem (so the 365 `[[决策记录]]` links keep resolving), `aliases: [决策记录]`, text "decisions live in decisions/ (O2O-DEC-nnnn); browse in the GUI". Not generated | one-time write |
| `决策记录--研究视图.md` | deleted; its 14 inbound mentions re-pointed to `[[决策记录]]` (mechanical regex) | mechanical |
| Plan status tails | removed (above) | mechanical |
| `ops/偏离登记.md` (37 rows, 9 columns) | unchanged; header-name parsing | none |
| `New Off2On RL MOC.md` | frontmatter untouched; body wording that mentions 看板/研究视图/vault.py is reviewed by the coordinator, user approves | judgement |
| **New:** `expnote 项目配置.md` (profile note, `type: project-profile`) | **first step of M1**: created from the section 3.4 table (all Off2On constants), reviewed and approved by the user before anything else is migrated; afterwards only the user/coordinator edit it | one-time write (`new profile`) + judgement |
| `templates/模板--*.md`, `文档分层约定.md`, `实验卡/假说卡/进展看板模板` | rewritten for schema 3 and `expnote` (no status markers, `expnote` commands instead of `vault.py`); `模板--决策行.md` (which says "不拆成一文件一决策", contradicting settled decision 6) is replaced by `模板--决策.md`; new `模板--待办.md`; old card templates kept as legacy | **agent judgement**, user reviews |
| `templates/vault.py`, `check_vault.py`, `tests/test_vault.py` | ported into `expnote/research/` and `tests/research/`; removed from the vault only after P5 acceptance; kept in the external backup | port (P1), delete (M5) |
| `50 Archive/Expnote/off2on-baseline/` (684 files, 3.5 MB) | `sync markdown` disabled for the bound workspace (3.5); tar to the external backup; delete the folder. [FACT] a whole-vault scan (link targets equal to a projected run/analysis stem or `Baseline MOC`) finds 18 notes with 1,056 link occurrences: 4 inside the project (`EV--PLAN-002 历史运行检索索引`, `历史复盘材料与配置`, `历史退化复盘`, `PLAN-002--coverage-difficulty-extension`) and 14 outside it (`00 Inbox/DISS-004`, one WSRL literature note, `50 Archive/Archive MOC`, ten archived Q-Contrast notes, `60 System/Research Project Architecture Guide`). In-project notes: links rewritten to `` `id` `` in running text (mechanical regex). Outside notes (resolved, 13.12): run/analysis links are rewritten to plain `` `id` `` text only in the 3 non-archived ones (`00 Inbox/DISS-004`, the WSRL literature note in `30 Zettelkasten`, `60 System/Research Project Architecture Guide`); the 11 archived ones (ten Q-Contrast notes and `50 Archive/Archive MOC`) are left alone and show dead links in Obsidian, while the index still resolves run ids and analysis doc ids against the workspace DB (`resolution='run'`/`'doc'`) | mechanical |
| `results/.aws` (empty dot-directory) | verify empty, delete | M5 |
| `ops/_refactor_preview/` (1 sample exec-plan preview, `type: exec-plan`) | delete after backup | M5 |
| Images (319 PNG, 91 MB, `40 Resources/42 Assets/Images/New Off2On RL/`) | untouched; orphan count reported by `check` as INFO | none |

### 11.2 Cross-references to decisions

| Form | Count / source | Handling |
|---|---|---|
| Typed fields: `approved_decision: 115`, `closed_decision`, `rules_decision`, `label_authorization: "123"`, RES `decisions: [...]`, `confirmed_decision` | frontmatter of the 40 schema-2 notes | canonicalised to `"O2O-DEC-nnnn"`; engine also accepts the old forms on read |
| `[[决策记录]] #N` hub form in prose and legacy frontmatter (`approval`, `constraints`) | about 342 occurrences in 110 notes [FACT: 365 hub links, 342 followed by `#N`] | **not rewritten** (resolved, 13.3): the stub keeps links resolving, the index parses the form into `decision_refs`, the GUI renders it as a link to the decision. Reason: the vault rule "不改 DIAG/PLAN 原文" (文档分层约定 rule 9) protects exactly these texts; a mass rewrite also creates 110 sync-visible edits. A rewrite pass is explicitly out of scope |
| `[[O2O-DEC-nnnn]]` in new decision `targets` and in new notes | new | normal links |
| Decision files cite each other | `supersedes`/`superseded_by` | ids |

### 11.3 Verification gates (all required before M5 deletions)

1. External backup exists, tar of the project dir, `50 Archive/Expnote/off2on-baseline/`, `.obsidian` config and the vault's `git rev-parse HEAD` (the vault has a `.git` [FACT]); restore tested into a temp dir.
2. Golden: vault.py findings captured from a frozen copy before M1 (`(level, code-equivalent, where)` list).
3. Migrated `expnote check --project off2on`: FAIL 0 with the confirmable gates still pending (see the 11.1 final-RES row); every WARN either in the golden WARN list or explained in the migration report.
4. All links and image embeds resolve (counts equal the golden's unresolved counts minus the removed-by-design ones, listed).
5. `plan bundle` builds for every plan (7 plan pages in `plans/`: 001, 002, 002A, 002B, 002B-gammafix, 002C, 003A) without errors and with `findings.fail == 0`.
6. Decision round-trip: 124 files regenerate the original table cells exactly (modulo the #124 status rewrite already in the source).
7. Body-preservation assertion: for every migrated note, body bytes outside removed blocks are identical to the original.
8. `rules_sha256` unchanged for the 3 exec-plans.
9. Review tables (plan membership of legacy EVs, flagged decision rows, plan statuses, drafted final RES) signed off by the coordinator; the user confirms through the proposed decisions in the GUI.
10. Re-run of the migration is a no-op (idempotence).

## 12. Phased implementation

| Phase | Scope | Acceptance gate | Tests |
|---|---|---|---|
| P1 read-only core | `textkit`, `frontmatter`, `schemas`, `checks` (reads **schema 2 and 3 and the 7-column decision table**, because the real vault is still schema 2), registry and profile reader (`project register/list/show/use/rebind`; the profile is read from the vault note, which for the parity run is added by hand to the frozen copy of the vault, because the real vault gets its note only at M1), index + `reindex`, `search`, `check`, `doctor`, `plan list/show/bundle` (bundle includes `related` and `analysisDocs`), `decision list/show` (from the table until P4), `ev list`, `res list`, `deviation list`, `todo list` (derived only), `next`, `guide research`; envelope and exit codes | (a) parity: on a frozen copy of the real vault, `check --compat vault-py` reproduces vault.py's `run_checks` findings, compared on `(level, code, where)` over the codes that map to a vault.py finding. `--compat vault-py` suppresses codes vault.py never had (`GEN.STATUS_TAIL_PRESENT`, `PROSE.RAW_NUMBERS`, `SCHEMA.OLD`, `DEC.AUTH_WEAK`, `DEC.BODY_CHANGED`); plain `check` reports them separately in `data.newCodes` (vault.py is imported from the backup copy as an oracle; a mapping table from vault.py `(area, message pattern)` to codes is part of the harness; message text ignored; generator side effects out of scope); (b) incremental refresh time on the real vault measured and reported (target < 3 s); (c) `search` finds Chinese 2- and 4-character terms; (d) no write to the vault anywhere (test patches `open` for write under the vault root) | `tests/research/` with CliRunner and a `mini_vault` fixture ported from `test_vault.py` (DECISIONS, PLAN, EXEC, EV, RES, DEVIATIONS fixtures, 41 existing cases mapped one-to-one); an `env`-gated parity test (`EXPNOTE_PARITY_VAULT`), skipped in CI; index tests (incremental, rename, delete, conflict file excluded, run-link resolution); NFC, CRLF and BOM cases |
| P2 read-only GUI | router, static pages, markdown renderer, image endpoint, home/project/plan/decision/EV/TODO/search pages, `expnote web` mounting | every page renders for the real vault without console errors; plan page shows the same facts as `plan bundle`, including related notes and analysis docs (test compares fields); image endpoint rejects traversal/symlink/non-image; no write endpoint exists yet; XSS fixture (raw HTML in a note) is neutralised | FastAPI `TestClient` tests, golden JSON for `GET /home`, path-confinement tests, CSP header test |
| P3 write set and `new` | `writeback.py`; `decision new/set`, `ev label`, `todo add/done/adopt`, `plan advance`, `exec freeze/advance`, `res confirm`, `deviation add/close`, `ev split`, `new ...` including `new profile` (reads vault templates, works with schema-2 templates by writing schema 3 and ignoring unknown placeholders with a warning; real schema-3 templates arrive in P4; `new` always strips forbidden markers such as the `vault:status` pair that the schema-2 人读方案 template contains), GUI write actions | `doctor --probe-sync` run once on the real vault with the result (uploaded or not) reported by the user; CAS conflict test (two writers, second gets 409); crash-safety test (kill between temp write and replace leaves original intact and a stale temp that `doctor` removes); quiet-window test; dry-run default test; write-only-if-different test (second apply is a no-op, mtime unchanged); audit + journal test (undo from pre-image restores bytes); user-only test (CLI confirm without `--authorization` exits 7; GUI records `confirmed_by: user`); agent-for-user path | `tests/research/test_writeback.py` (CAS, atomic, busy-file simulation via a monkeypatched `os.replace` raising `PermissionError`), `test_gates.py` (every row of 7.1 as pass/fail pair), `test_gui_writes.py` |
| P4 vault migration | `migrate.py`, M0-M5 of section 11, schema-3 templates, `文档分层约定` rewrite, hub stub, profile note (first step of M1), projection off + deletion | all gates in 11.3 | `test_migration.py` on a synthetic legacy mini-vault (all legacy shapes incl. unquoted ids, status tails, 7-col table with escaped pipes, the 部分修订 rows); idempotence; body-preservation property test |
| P5 switch-over | standing command becomes `expnote check --project off2on` (decision recorded by the user); vault.py/check_vault.py/tests removed from the vault; `AGENTS`/`agent-quickstart.md`/`guide` updated; `migrate.py` marked removable; the profile note stays in the vault (13.2) | one full week of normal work with FAIL 0 and no sync conflict files attributable to the tool; user acceptance | full suite `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` and `ruff check .` per expnote AGENTS; research tests live in the same suite |

Optional after P5 (not scheduled): a thin read-mostly MCP adapter over the same library: `search`, `get`, `plan_bundle`, `todo_list`, `decision_list`, plus one gated write `submit_event` that only creates a proposal. User-only approvals (decision confirm, label commit) are never exposed through MCP, so an agent cannot confirm via MCP at all; the CLI `--authorization` path stays the only agent route and stays audited. The survey's token-cost numbers for MCP vs CLI come from secondary sources [UNVERIFIED], so MCP is deferred on need, not on those numbers.

## 13. Questions and their resolution

All questions are resolved by the user (answers of 2026-10-21) or accepted from the coordinator; the answers are folded into the body at the place shown.

| # | Question | Resolution | Body |
|---|---|---|---|
| 13.1 | PyYAML hard dependency or extra? | resolved: optional `[research]` extra, lazy import, also in `dev` | 2.5 |
| 13.2 | Where does the profile live? | resolved: a static vault note `expnote 项目配置.md` (`type: project-profile`), frontmatter is the profile, never written by the tool, versioned by the vault git; absolute machine paths stay in `projects.toml`, which points to the note; MOC gets no keys | 3.1, 3.4, 3.5, 4.2 |
| 13.3 | Rewrite `[[决策记录]] #N` references? | resolved: no; keep the hub stub, index and GUI resolve the form; no rewrite pass (out of scope) | 11.2 |
| 13.4 | Legacy EVs: unsplit or split all? | resolved: keep unsplit with `legacy: true`; `ev split` on demand | 11.1 |
| 13.5 | Deviations: table or files? | resolved: keep the single table behind `DeviationStore` | 4.8 |
| 13.6 | Which client syncs the vault? | resolved: a Windows WebDAV app outside Obsidian (name unknown); dot-prefixed temps and drvfs retry kept; `doctor --probe-sync` is a P3 acceptance item | 6.5, 8, 10, 12 |
| 13.7 | Envelope for existing 73 commands? | resolved: not in this project; research commands only (a global `EXPNOTE_ENVELOPE=1` switch would be the first step later) | 6.1 |
| 13.8 | Plan statuses `superseded`/`withdrawn`, per-plan mapping | resolved: vocabulary approved; the per-plan mapping of PLAN-001/002/002A is made in P4 and confirmed by the user through decisions | 4.3, 11.1 |
| 13.9 | Drafted final RES as `first-read` plus proposed decision; stage cards superseded | resolved: accepted | 11.1 |
| 13.10 | `PROSE.RAW_NUMBERS` strength | resolved: WARN-only; revisit with measured false-positive rate | 7.3 |
| 13.11 | Register other projects (e.g. Q-Contrast)? | resolved: after Off2On is stable | 3.2 |
| 13.12 | Dead run links outside the project after the projection is deleted | resolved: rewrite only the 3 non-archived outside notes (Inbox DISS-004, the WSRL literature note, 60 System guide); accept dead links in the 11 archived ones | 11.1 |
