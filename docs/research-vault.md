# Research vault guide

Set up an Obsidian research vault where plans, evidence and results are plain
Markdown, decisions and run records live in expnote's SQLite outside the vault,
a localhost GUI shows the state and takes the few decisions only you may make,
and coding agents (Claude Code, Codex) drive everything through a CLI with
dry-run-by-default writes.

Every `bash` block below was executed, in order, in one shell against an empty
vault (the example project is called `demo`; its topic is a learning-rate warmup
study), and the whole sequence ends with `expnote check` reporting `FAIL 0`.
Blocks marked `console` or `text` are illustrative and were not run. Set `VAULT`
before the first block to put the vault somewhere other than `~/research-vault`.

## 1. What lives where

| Place | Holds | Who writes it |
|---|---|---|
| **Vault** (Obsidian, synced by WebDAV, Syncthing, ...) | plans, execution plans, evidence pages and appendices, results, theory and protocol notes, deviation register, the profile note, the decision and TODO backups `ops/decisions.jsonl` and `ops/todos.jsonl` | you and your agents (prose); expnote (a few frontmatter keys, through commands) |
| **State dir** (`~/.local/share/expnote`; `$XDG_DATA_HOME` or `$EXPNOTE_DATA_HOME` relocate it) | the decision and manual TODO stores (`research/projects/<id>/decisions.sqlite`, `todos.sqlite`), the rebuildable note index (`research/index/`), audit log and pre-image journal, run workspaces (`workspaces/<name>/`) | expnote only |
| **Registry** (`~/.config/expnote/projects.toml`; `$XDG_CONFIG_HOME` or `$EXPNOTE_CONFIG_HOME` relocate it) | which vault folder is which project | `expnote project init` |
| **GUI** (`expnote web`) | generated views only: timelines, gates, open items, health | reads the vault and state; never stores anything of its own |

Rules that follow from this:

- **Markdown in the vault is the truth** for everything a human writes. Edit prose
  with Obsidian or your agent's file tools.
- **Decisions are records, not notes.** They live in SQLite, their text never
  changes (you supersede instead), and a JSONL backup is exported into the vault so
  your sync client carries it off the machine.
- **The vault gets no generated files and no run notes.** Servers write run records
  into run workspaces (state dir), never into the vault. To enforce this, expnote
  has a *projection guard*: `expnote init --obsidian-root <vault>` (which would
  project run notes as Markdown into a vault) is refused when that vault, or a
  folder inside or around it, is registered as a research project. Runs are viewed
  in `expnote web` and cited from notes by their ids (see 5.5).

## 2. Vault layout (one project folder)

```text
research-vault/                    # the folder you open in Obsidian (has .obsidian/)
  Attachments/demo/                # figures (any folder inside the vault; set at init)
  Research/demo/                   # the project folder
    expnote-profile.md             # the profile note: all conventions (required)
    plans/                         # human-written plans          (default folder for `new plan`)
    ops/                           # exec plans, evidence appendices, logs, deviation register
    evidence/                      # evidence pages (figures + labels)
    results/                       # one final result (RES) per plan
    theory/  protocols/            # free notes, any layout (conventions, not read specially)
    templates/                     # your note templates (optional)
    ops/deviations.md              # deviation register (you create it once, see 5.6)
    ops/decisions.jsonl            # decision backup (written by expnote)
    ops/todos.jsonl                # manual TODO backup (written by expnote)
```

Required: the profile note, and a deviation register once you record a deviation.
The folders `plans/ ops/ evidence/ results/` are only the *defaults* of the
profile's `folders:` map; the tool creates them when it writes the first note.
Notes are recognised by their frontmatter `type:` (`plan`, `exec-plan`, `evidence`,
`evidence-appendix`, `result`), not by their folder, and any other note (theory,
protocols, meeting notes) is simply indexed for search and link checks.
A note whose path contains a `templates` folder is skipped by lifecycle checks, and
one inside `archive` is treated as archived (both names are configurable, see
`scan.*` in the profile).

A *decision hub* note is not needed in a new vault: any note can cite a decision as
`[[DEMO-DEC-0001]]` and the link resolves against the store. (Migrated vaults keep
a static hub note so that old `[[Hub]] #N` references still work.)

## 3. Setup from zero

### 3.1 Install

```console
$ git clone <this repository> && cd expnote
$ pip install -e ".[research]"        # core + PyYAML, needed by the research commands
```

Python 3.11 or newer. The research commands need PyYAML; `expnote doctor` reports it.

To try everything in a sandbox without touching your real configuration, point the
state at a scratch folder first:

```console
$ export XDG_CONFIG_HOME=/tmp/expnote-try/cfg XDG_DATA_HOME=/tmp/expnote-try/data
```

### 3.2 Create the vault folders and a run workspace

```bash
export VAULT="${VAULT:-$HOME/research-vault}"    # the folder you open in Obsidian
export PROJ="$VAULT/Research/demo"               # one project = one folder in the vault
export IMAGES="$VAULT/Attachments/demo"          # figures, inside the vault
mkdir -p "$VAULT/.obsidian" "$PROJ" "$IMAGES"    # Obsidian creates .obsidian/ for you
expnote init --workspace demo-runs               # run workspace: state dir only, no vault
```

A *workspace* is where your training jobs register runs (`expnote run add`,
`run update`; see the main README). It is a SQLite database in the state dir. The
project is linked to it so that notes can cite runs and the GUI can show them.

### 3.3 Bind the folder as a project

```bash
expnote project init --project-dir "$PROJ" --images-dir "$IMAGES" \
  --id demo --name "Demo" --profile-note "$PROJ/expnote-profile.md" \
  --workspace demo-runs                          # dry-run: prints the profile it would write, exit 4
expnote project init --project-dir "$PROJ" --images-dir "$IMAGES" \
  --id demo --name "Demo" --profile-note "$PROJ/expnote-profile.md" \
  --workspace demo-runs --apply
```

`project init` finds the vault root (nearest folder with `.obsidian/`), writes the
profile note if the folder has none, registers the project and makes it the active
one. Running it again changes nothing. Without `--profile-note` the scaffold gets a
default file name with a non-English word in it; pass an explicit name as above.
`--id` is the project id you pass as `--project` to other commands; the first
project becomes the default, so with a single project you never type it.

With the project registered, the projection guard is active. Trying to project run
notes into the vault is refused:

```bash
expnote init --workspace attempt --obsidian-root "$VAULT" --notes-dir runs    # exit 2: projection guard
```

### 3.4 The profile note

The profile note's frontmatter is the project's configuration. expnote reads it and
never rewrites it, so edit it freely (Obsidian shows it as Properties). The scaffold
is generic English; the keys you are most likely to change:

| Key | Meaning |
|---|---|
| `headings.rules` | heading (prefix match) of the exec plan's rules section, the part frozen by `exec freeze` |
| `headings.labels`, `headings.observation` | the label section of an evidence page and the section that stays empty until the labels are in |
| `decisions.id_prefix` | decision ids are `<prefix>-0001`, ... (default `<PROJECT>-DEC`) |
| `decisions.layers` | allowed `--layer` values; `options_warn_layers` are the layers whose decisions should list alternatives |
| `paths.deviation_register`, `paths.decision_export`, `paths.todo_export` | vault-relative paths (`ops/decisions.jsonl`, `ops/todos.jsonl` by default) |
| `paths.todo_file` | the old manual TODO note; read only by `todo migrate` and, until then, for listing |
| `images.dir`, `images.required_prefix` | figures folder; an embedded image whose name starts with the prefix must live there |
| `ids.plan_regex`, `ids.exec_regex`, `ids.res_prefix` | what plan ids, exec ids and result ids look like |
| `runs.id_regex` | what a run id looks like, so `a7zf90k7` is recognised as a run |
| `folders`, `names` | where `new plan/exec/ev/res` put notes and how they name them |
| `templates.dir`, `templates.files` | your own note templates (section 4) |
| `labels.*`, `deviation.*` | label table and deviation register column names |
| `scan.*` | folders to skip, template folders, archive folder names, extra sync-conflict globs |

The headings and column names are *words in your language*: change them to whatever
your notes use (the tool matches them as text), and keep the profile and your
templates in agreement. Two edits we make now: figures must carry the prefix
`demo-`, and `new plan` uses our own template.

```bash
PROFILE="$PROJ/expnote-profile.md"
sed -i 's|required_prefix: ""|required_prefix: "demo-"|' "$PROFILE"     # GNU sed; or edit in Obsidian
sed -i 's|^templates:|templates:\n  dir: "templates"\n  files:\n    plan: "template--plan.md"|' "$PROFILE"
expnote project show demo --json | head -c 300                          # resolved paths, profile status
```

### 3.5 Doctor, sync probe, GUI

```bash
expnote doctor                  # environment and hygiene: vault reachable, profile parses, index fresh, ...
```

If your vault is synced by a WebDAV or Syncthing-style client, run the sync probe
once. Every write goes through a dot-prefixed temp file that is then atomically
renamed; some clients never upload dot-files (fine, they are transient) and some
upload them and then trip over the rename. The probe writes one of each into the
project folder so you can see what your client does:

```bash
expnote doctor --probe-sync --apply     # writes expnote-probe/.expnote.probe.*.tmp and a plain twin
# ... look at what your sync client uploaded, then:
expnote doctor --probe-sync --cleanup --apply
```

Start the GUI whenever you want to read or decide (it binds to 127.0.0.1):

```console
$ expnote web --no-open                 # http://localhost:8765/research/ ; --port N, or --detach to background it
```

## 4. Templates

`expnote new plan|exec|ev|res` builds a note from a template. Lookup order:

1. the profile's `templates.files.<kind>` (kinds `plan`, `exec`, `ev`, `appendix`,
   `res`), read from `<project>/<templates.dir>/<file>`;
2. a built-in English skeleton, used when the key is missing or the file does not
   exist. A fresh vault works at once; no command scaffolds template files, so to
   customise, copy the shape below.

A template is an ordinary note with a frontmatter block (schema 3) and `{{...}}`
placeholders. Placeholders are filled in frontmatter and body: `{{id}}`, `{{title}}`,
`{{date}}`, `{{plan}}`, `{{plan_id}}`, `{{stage}}`, `{{ev}}`, `{{appendix}}`, plus
`{{rules_heading}}`, `{{label_heading}}` and `{{observation_heading}}` from the
profile (use them so headings and profile stay in agreement). Text between the
`templates.doc_markers` lines (a comment block explaining the template) is removed
from the generated note. Keep templates inside a `templates` folder so checks skip
them.

```bash
mkdir -p "$PROJ/templates"
cat > "$PROJ/templates/template--plan.md" <<'EOF'
---
schema: 3
type: plan
id: "{{id}}"
title: "{{title}}"
status: draft
created: "{{date}}"
owner: ""
---
<!-- template-doc:start -->
> Template note: this block is removed by `expnote new plan`.
<!-- template-doc:end -->
# {{id}}: {{title}}

## 1. Question

One sentence: what do we want to learn?

## 2. Hypothesis and success criterion

## 3. Out of scope
EOF
expnote check       # a fresh project: FAIL 0 (an INFO about the empty images folder is normal)
```

## 5. The research lifecycle

The state machines (`expnote guide research` prints them):

```text
plan       draft > approved > running > read-out > confirmed > closed     (superseded, withdrawn: terminal)
decision   proposed > confirmed > superseded      or  proposed > rejected
exec-plan  draft > frozen > running > done | aborted
evidence   rendered > labelled | label-na
result     draft > first-read > confirmed | superseded | withdrawn   (the user returns first-read to draft)
```

`expnote plan bundle PLAN-001` (or `plan advance` as a dry-run) shows the unmet gate
codes for the next move. Plan gates: *approved* needs a confirmed decision;
*running* needs a frozen exec plan; *read-out* needs exactly one final result and
labelled evidence (and refuses while that result is still a `draft`: submit it first); *confirmed* needs that result confirmed; *closed* needs a
confirmed closing decision.

**Authorization.** Some actions are the human's: confirming or rejecting a
decision, closing a plan, labelling evidence, reviewing a result. From the CLI they need
`--authorization "<the user's own words>"` (or the id of an earlier confirmed
decision) or the command exits 7 (`USER_ONLY`). In the GUI the user just clicks.
An agent must never invent the words. The quotes below stand for what you
would have said in chat.

### 5.1 Plan

```bash
expnote new plan --id PLAN-001 --title "Learning-rate warmup study"            # dry-run (exit 4)
expnote new plan --id PLAN-001 --title "Learning-rate warmup study" --apply
```

Write the plan body in Obsidian (the question, scope, how to read the results; no
numbers, those belong to evidence pages). Here as a file write, keeping the
frontmatter the tool created:

```bash
PLAN="$PROJ/plans/PLAN-001--Learning-rate warmup study.md"
cat > "$PLAN" <<'EOF'
---
schema: 3
type: plan
id: "PLAN-001"
title: "Learning-rate warmup study"
status: draft
created: "2026-10-22"
owner: "me"
---
# PLAN-001: Learning-rate warmup study

## 1. Question

Does a linear learning-rate warmup make early training more stable?

## 2. Hypothesis and success criterion

The warmup arm shows fewer early loss spikes than the arm without warmup.

## 3. Out of scope

Other schedules, other model sizes.
EOF
```

**Start from your own draft.** If you already wrote the idea as a plain note, you do
not have to retype it. `plan adopt` turns it into a standard plan and leaves your
text alone:

```bash
expnote plan next-id                                           # a free id, e.g. PLAN-002
expnote plan adopt "plans/my draft.md" --id PLAN-002           # dry-run (exit 4)
expnote plan adopt "plans/my draft.md" --id PLAN-002 --apply
```

`NOTE` is a path (relative to the vault or to the project folder) or the note's name.
The command

- completes the frontmatter: it sets `schema: 3`, `type: plan`, `id` and `title`
  (`--title`, else the draft's `title`, else its first `#` heading, else the file
  name), keeps every other key and value, and adds the keys the plan template has
  and the draft lacks (`created`, `owner`, ...). A `status` that is not a plan status
  becomes `draft`, and the old value is kept as `legacy_status`;
- never edits the body. It lists the template's section headings the draft does not
  have (`missingSections`, numbering ignored) so you can add them by hand;
- moves the note into the plans folder under the `names.plan` pattern and rewrites the
  wikilinks that point at it everywhere in the vault (`[[draft]]`,
  `[[draft|alias]]`, `[[draft#heading]]`, embeds, frontmatter links);
- is one audited write (compare-and-swap on every file, quiet window, journal), like
  any other write command.

It refuses an id that does not match `ids.plan_regex` (exit 2), an id another plan
already has or a target name that is taken (exit 5), and a note whose `type` is
something other than `plan`. A note that already is the plan with that id and sits at
its target path is a no-op (exit 0). One that has the id but sits elsewhere gets only
the remaining move and link rewrite, so running the same command again finishes an
adopt that stopped halfway (the frontmatter was written, the rename failed). `plan next-id` takes the most common id prefix, its highest number plus one,
zero-padded like the existing ids. When the project has no plan yet (choose the first
id yourself) or the result would not match `ids.plan_regex`, `id` is `null` in
`--json` and a short explanation is printed otherwise.

Notes in the plans folder that are not standard plans show up as the TODO
`plan-draft-unadopted` (archive and template folders and `scan.ignore_globs` are
skipped, so list an index note there if you keep one). A plan that already is
standard is never flagged, even when its file name differs from `names.plan`; the
move of an interrupted adopt is finished by repeating the command. In the GUI, its detail pane has
"Adopt as plan": an id (the next free one) and a title, a preview of the change
(including the missing sections), then Apply.

### 5.2 Decision, approval, advance

```bash
expnote decision new --title "Approve PLAN-001 as written" --layer research \
  --decided "Run the warmup comparison as described in PLAN-001." \
  --reason "The question is small and the arms are cheap to run." \
  --options "Run a schedule search instead; rejected, too broad for a first look." \
  --plans PLAN-001 --targets "[[PLAN-001--Learning-rate warmup study]]" --apply
expnote next --owner user                  # what waits on the human
expnote decision set DEMO-DEC-0001 --status confirmed --apply            # exit 7: user-only
expnote decision set DEMO-DEC-0001 --status confirmed \
  --authorization "Yes, approved, go ahead with PLAN-001." --apply
expnote plan advance PLAN-001 --to approved --decision DEMO-DEC-0001 --no-quiet --apply
```

If the user already said yes in chat, record it in one step with
`decision new ... --status confirmed --authorization "<words>"`. Otherwise leave the
decision `proposed`; it shows up under *Waiting on you* in the GUI, where the user
approves or rejects it with a click. Plans, decisions and evidence cite each other by
`[[wikilink]]`; cite a decision as `[[DEMO-DEC-0001]]`.

### 5.3 Exec plan, rules, freeze

The exec plan holds servers, commands and the *rules summary* (stage rules and
pre-registered thresholds). Once the user confirmed the rules in a decision, `exec
freeze` stores a hash of that section; editing it afterwards needs a registered
deviation.

```bash
expnote new exec --plan PLAN-001 --stage S1 --title "warmup vs none" --apply
EXEC="$PROJ/ops/exec--PLAN-001 warmup vs none.md"
cat > "$EXEC" <<'EOF'
---
schema: 3
type: exec-plan
id: "PLAN-001-S1"
plan: "[[PLAN-001--Learning-rate warmup study]]"
stage: "S1"
status: draft
created: "2026-10-22"
servers: []
expnote_topic: ""
---
# Execution plan: PLAN-001 warmup vs none

Plan: [[PLAN-001--Learning-rate warmup study]].

## 1. Stages

One stage, S1, with two arms.

## 2. Materials and commands

Training script `train.py`, flag `--warmup`.

## Rules summary (needs confirmation)

- Arms: no warmup, and a linear warmup over the first steps.
- Seeds: the same set of seeds for both arms.
- Verdict rule: the arm with fewer loss spikes is more stable; ties count as no effect.

## 3. Risks and resources
EOF
expnote decision new --title "Confirm the S1 rules summary" --layer research \
  --decided "The rules summary of PLAN-001-S1 is final." \
  --reason "The verdict rule must be fixed before any data is seen." \
  --options "Leave the rule open until the data arrive; rejected, it invites post-hoc choices." \
  --plans PLAN-001 --status confirmed --authorization "Yes, those rules are fine." --apply
expnote exec freeze PLAN-001-S1 --rules-decision DEMO-DEC-0002 --no-quiet --apply
expnote plan advance PLAN-001 --to running --apply
expnote exec advance PLAN-001-S1 --to running --apply
```

### 5.4 Runs

Training jobs register runs in the workspace; they never touch the vault. A run id
must match `runs.id_regex` (eight lowercase letters or digits in the scaffold; the
wandb run id is a good choice).

```bash
expnote moc add --workspace demo-runs --moc-id demo --title "Demo runs"
expnote topic add "warmup study" --workspace demo-runs --moc-id demo
expnote run add --workspace demo-runs --moc-id demo --topic "warmup study" \
  --run-id a7zf90k7 --purpose "Linear warmup arm, seed 1" --status finished --meta algo=sgd
```

(`project register ... --workspace NAME` links an extra workspace to an existing
project later.)

### 5.5 Evidence page and appendix, labels

`new ev` creates two notes: the evidence page (figures, one endpoint table, the label
table, an observation) and its appendix twin (run identity, full tables, checks),
which keeps numbers and provenance off the page the user reads.

```bash
cp my-plot.png "$IMAGES/demo-warmup-loss.png"        # prefix demo- from the profile
expnote new ev --plan PLAN-001 --exec PLAN-001-S1 --title "Loss curves" --apply
EV="$PROJ/evidence/EV--PLAN-001 Loss curves.md"
cat > "$EV" <<'EOF'
---
schema: 3
type: evidence
created: "2026-10-22"
plan: "[[PLAN-001--Learning-rate warmup study]]"
exec_plan: "[[exec--PLAN-001 warmup vs none]]"
question: "Does warmup reduce early loss spikes?"
status: rendered
figures: ["demo-warmup-loss.png"]
runs: ["a7zf90k7"]
appendix: "[[appendix--EV--PLAN-001 Loss curves]]"
---
# EV--PLAN-001 Loss curves

Numbers, run identity and provenance: see the appendix [[appendix--EV--PLAN-001 Loss curves]].

## Figures

![[demo-warmup-loss.png]]

## Endpoint table

| Arm | Final loss |
|---|---|
| no warmup | 0.10 |
| linear warmup | 0.10 |

## Figure labels

| Arm | Spikes in the first steps |
|---|---|
| no warmup |  |
| linear warmup |  |

## Observation
EOF
cat > "$PROJ/ops/appendix--EV--PLAN-001 Loss curves.md" <<'EOF'
---
schema: 3
type: evidence-appendix
created: "2026-10-22"
parent: "[[EV--PLAN-001 Loss curves]]"
plan: "[[PLAN-001--Learning-rate warmup study]]"
runs: ["a7zf90k7"]
expnote_snapshot: ""
generated_by: ""
---
# appendix--EV--PLAN-001 Loss curves

Evidence page: [[EV--PLAN-001 Loss curves]].

## Run identity

Run a7zf90k7 is one of the seeds of each arm; command line in the exec plan.

## Tables

| Arm | Final loss |
|---|---|
| no warmup | 0.10 |
| linear warmup | 0.10 |

## Checks

Both arms trained for the same number of steps.
EOF
expnote check
```

The frontmatter keys `figures`, `runs` and the appendix link are the agent's to keep
right (`runs:` is what ties an evidence page to the runs shown in the GUI and in
`plan bundle`; write run ids there and as plain text in prose, since a `[[run-id]]`
wikilink is understood by the GUI but `check` still reports it as an unresolved link); `check` fails when `figures` is empty or a figure is missing from the vault.
The page stays unlabelled (`status: rendered`) and its *Observation* stays empty
until the user has looked at the figure and filled the label table. The user does
that in the GUI's Evidence page (fill the cells, save). Recorded from chat instead:

```bash
expnote next --owner user
expnote ev label show "EV--PLAN-001 Loss curves"                  # label table and the sha256 to pass as CAS base
expnote ev label set "EV--PLAN-001 Loss curves" --row "no warmup" \
  --value "Spikes in the first steps=many" \
  --authorization "I looked at the figure: many spikes without warmup." --no-quiet --apply
expnote ev label set "EV--PLAN-001 Loss curves" --row "linear warmup" \
  --value "Spikes in the first steps=few" --remark "Warmup curve is smoother." \
  --authorization "I looked at the figure: few spikes with warmup." --no-quiet --apply
expnote ev label commit "EV--PLAN-001 Loss curves" \
  --authorization "The labels are final, thanks." --no-quiet --apply
printf '\nThe warmup arm shows fewer early spikes; the endpoint is the same.\n' >> "$EV"
```

### 5.6 Deviations

Anything that departs from the frozen rules goes in the deviation register, a
Markdown table whose column names come from the profile (`deviation.columns`). Create
it once; the tool then appends rows with the next id and closes them.

```bash
cat > "$PROJ/ops/deviations.md" <<'EOF'
---
type: deviation-register
---
# Deviation register

| ID | Date | Experiment | Deviation | Impact on validity | Disposition | Related notes | Status | Found at |
|---|---|---|---|---|---|---|---|---|
EOF
expnote deviation add --experiment PLAN-001-S1 --content "One seed crashed and was rerun." \
  --impact "None: the rerun used the same settings." --cards "[[exec--PLAN-001 warmup vs none]]" \
  --found "after data" --no-quiet --apply
expnote next                                   # an open deviation is an agent item
expnote deviation close D-01 --disposition "Rerun with the same settings; no effect on the verdict." --no-quiet --apply
```

`--found` takes one of the profile's `deviation.found_values`. In the GUI the user
can close a deviation with a click.

### 5.7 Final result, confirmation, closing

There is **one final RES per plan** (per cycle: in a reopened plan `new res` ids the
new cycle's result `RES-<PLAN>-cycleN`, N = 2 after the first reopen, 3 after the
second; the first cycle keeps `RES-<PLAN>`). It cites its evidence, states the
pre-registered verdicts and the deviations that affect them. It is drafted
(`status: draft`), the agent submits it with `res submit` (`first-read`), and the
user reads and checks it in the GUI, then passes it (`confirmed`) or returns it with
remarks (back to `draft`). The review is not a decision.

```bash
expnote new res --plan PLAN-001 --title "Warmup result" --apply
RES="$PROJ/results/RES--PLAN-001 Warmup result.md"
cat > "$RES" <<'EOF'
---
schema: 3
type: result
id: "RES-PLAN-001"
plan: "[[PLAN-001--Learning-rate warmup study]]"
status: draft
evidence: ["[[EV--PLAN-001 Loss curves]]"]
decisions: []
reviewed_by: ""
created: "2026-10-22"
---
# RES--PLAN-001 Warmup result

## 1. Question and summary

Does a linear warmup make early training more stable? Yes: see [[EV--PLAN-001 Loss curves]].

## 2. Pre-registered verdicts

The warmup arm showed fewer early loss spikes, so by the rule in [[exec--PLAN-001 warmup vs none]] it is the more stable arm.

## 3. Exploratory analysis

None.

## 4. Scope and limits

One model size and one dataset, as scoped in the plan.

## 5. Deviations

One seed was rerun, see the deviation register; it does not affect the verdict.
EOF
expnote res submit RES-PLAN-001 --deviation-impact none --apply
expnote next                                   # the result waits for the user: res-awaiting-review
```

`res submit` moves the result to `first-read`, records your proposed
`deviation_impact` (`none|weak|moderate|affects`) and, when the plan is `running`
and its read-out gates pass (one active result, every evidence page labelled), moves
the plan to `read-out` in the same write. If a gate is unmet the result is still
submitted, the plan stays `running` and the unmet gates come back as warnings; run
`plan advance PLAN-001 --to read-out` once they pass.

The user opens the result in the GUI (the `res-awaiting-review` TODO links to it),
reads and checks it, may change the deviation impact, and clicks **Pass review** or
**Return for revision**. A return needs remarks: they are appended to a
`## Review remarks` section at the end of the note (`- <date> user: <remarks>`), the
result goes back to `draft` with `returned_on`, and the plan stays where it is.
The agent sees `res-returned`, revises the note, and submits again. A pass writes
`status: confirmed`, `reviewed_by`, `reviewed_on` and the deviation impact. The CLI
path is user-only and needs the user's own words:

```bash
expnote res review RES-PLAN-001 --verdict pass --deviation-impact none \
  --authorization "Yes, the result stands." --no-quiet --apply
expnote res review RES-PLAN-001 --verdict return --remarks "Add the seed count to section 4." \
  --authorization "Not yet, see my comment." --apply
```

Then the plan moves on:

```bash
expnote decision new --title "Close PLAN-001" --layer research \
  --decided "PLAN-001 is finished: the warmup arm is the more stable one." \
  --reason "The result passed review." --plans PLAN-001 --apply
expnote decision set DEMO-DEC-0003 --status confirmed --authorization "Yes, close it." --apply
expnote plan advance PLAN-001 --to confirmed --apply
expnote plan advance PLAN-001 --to closed --decision DEMO-DEC-0003 --apply
expnote check
```

**The user's shortcut.** To drop or close a plan on the user's word in one step,
`plan close` records a confirmed decision and closes it; `--force` closes from any
open status and skips the gates (`check` keeps a WARN so a forced close stays
visible). The GUI offers the same as a button.

```bash
expnote new plan --id PLAN-002 --title "Cosine schedule" --apply
expnote plan close PLAN-002 --reason "Dropped: out of scope for this study." --force   # exit 7
expnote plan close PLAN-002 --reason "Dropped: out of scope for this study." --force \
  --authorization "Yes, drop it." --no-quiet --apply
```

**An approved plan always has a next step.** `expnote next` shows, for each plan in
`approved`: `plan-needs-exec` when it has no exec-plan (write it with `new exec`);
`exec-awaiting-freeze` for a draft exec-plan no decision cites: freeze it yourself,
`expnote exec freeze <EXEC_ID> --self-approve --no-new-rules --apply`, when it adds no
judgement rules beyond the approved plan (this records a confirmed decision "by agent"
and writes `approved_by: agent`, `new_rules: false`), otherwise propose the rules
decision for the user (`approved_by: user`, `new_rules: true`; `check` fails
`LIFE.EXEC_RULES_NOT_USER` when an agent decision backs an exec-plan that says
`new_rules: true`). New judgement rules always need the user and formal runs wait for
them; engineering preparation (implementation, material checks, smoke tests) never
does, and `plan-prep-while-rules-pending` says so while the user decides; the existing
steps cover a pending or confirmed decision (`exec freeze` after the user confirms); and `plan-ready-to-run` once an exec-plan is
frozen (start the runs per the exec-plan, then `plan advance PLAN-001 --to running`).

**Reopening a plan.** A closed or confirmed plan can start a new cycle. First the
user confirms a decision that concerns the plan, then:

```bash
expnote plan reopen PLAN-001 --decision DEMO-DEC-0007          # dry-run (exit 4)
expnote plan reopen PLAN-001 --decision DEMO-DEC-0007 --apply
```

The plan goes back to `approved` (`approved_decision` is the new decision,
`closed_decision` and `result` are cleared, `forced_close` is dropped), gets
`reopened_on`, and `earlier_cycle` lists every note of the finished cycle (it
accumulates over repeated reopens). The body is untouched. The gates then look at the
current cycle only: the new cycle needs its own frozen exec-plan, evidence and
ONE new final result (`new res` gives it the id `RES-PLAN-001-cycle2`). That result must link the earlier cycle's active result(s)
(`check` warns `LIFE.REOPEN_RES_NOT_MERGED` otherwise), and passing its review marks
those earlier results `superseded`. The GUI shows "Reopened <date>" and tags
earlier-cycle notes; reopening itself is CLI only.

A plan reopened by editing the frontmatter by hand is flagged by
`LIFE.PLAN_REOPEN_UNMARKED` (and the `plan-reopen-unmarked` TODO). Mark it with
`plan reopen PLAN-001 --decision <its approved_decision> --mark-existing`: members
dated before the decision (`frozen_on` for exec-plans, else `created`) go to the
earlier cycle, later ones stay current; the dry-run prints the split. Members with
no date must be placed with `--earlier <stem>` (repeatable).

### 5.8 TODOs

`expnote todo list` merges *derived* items, computed from the state of the notes and
never stored (a decision waiting for approval, unlabelled evidence, an open
deviation, a result waiting for the user's review (or returned to the agent), a decision review date that has come,
a draft in the plans folder that is not a plan yet, sync-conflict files, runs stuck in
`running`, ...), with *manual* ones from the project's TODO store. Items are split by
owner, the user's first. `expnote next` prints the same queue with the exact command
for each step.

Manual TODOs are rows of a SQLite file next to the decision store
(`<data home>/research/projects/<project>/todos.sqlite`), with ids `T-0001`, ...
and an event log. A row is `open`, `done` or `dropped` (final, with a reason); rows
are never deleted and each carries a `version` (`todo list --json`) that
`--expected-version N` checks, so a stale edit exits 5 (`CONFLICT`).

```bash
expnote todo add "Archive the raw training logs" --plan PLAN-001 --due 2026-11-01 --by user --apply
expnote todo list                                          # open; --include-done, --include-dropped
expnote todo add "Review the draft" --owner user --apply   # agents (--by agent, the default) must pass --owner user|agent
expnote todo edit T-0001 --owner user --apply              # hand a task to the user
expnote todo edit T-0001 --text "Review the draft" --plan PLAN-001 --due 2026-11-01 --apply
expnote todo edit T-0001 --clear-plan --clear-due --expected-version 2 --apply
expnote todo done T-0001 --apply                           # --undo reopens
expnote todo drop T-0002 --reason "superseded by T-0005" --apply
```

When the adder is an agent (`--by agent`, the default), `--owner user|agent` is
required and a missing owner exits 2 (`USAGE_MISSING_OPTION`): `--owner agent` for
the agent's own work, `--owner user` when the user must act, with the text starting
with the user's action. With `--by user` the owner is optional and defaults to the user.

`todo edit` needs at least one of `--text`, `--owner user|agent`, `--plan`,
`--clear-plan`, `--due`, `--clear-due`; the text may change (unlike a decision's).
A task that waits on the user is owned by the user and its text starts with the
action the user must take; an agent hands its tasks over with `--owner user` once
its work is done. A dropped TODO refuses `done`, `--undo` and `edit` (`GATE_FAILED`).
The GUI has matching `POST .../todos`, `.../todos/{id}/done|edit|drop`, all carrying
`expectedVersion`.

**Backup.** After every committed change expnote rewrites `ops/todos.jsonl` (TODOs
plus their event log); a file changed by someone else is reported as
`EXPORT_DIVERGED` and left alone until `todo export --apply --force`. Rebuild a lost
store with `todo restore --from ops/todos.jsonl --apply` (refuses a non-empty store
unless `--replace`). Never hand-edit the export.

**Migrating the old TODO note.** Older projects kept manual TODOs as task lines in
`paths.todo_file` (`todos.md`). While that file has tasks and no store exists,
`todo list` still reads the file but every TODO write exits 2 with
`TODO_NOT_MIGRATED`. Run `expnote todo migrate` (a dry run listing the rows) and then
`--apply`: ids are kept, id-less lines get the next ones, `[done:: date]` becomes the
done date, unknown inline fields are reported as warnings and not imported,
`ops/todos.jsonl` is written, a copy of the file goes to the audit journal
(`<data home>/research/projects/<project>/journal/`) and the file is deleted. It
refuses (`TODO_STORE_EXISTS`) once the store exists. `todo adopt` is gone.

## 6. Decisions

- A decision has an id `<prefix>-<4 digits>`, a title, a layer, *decided* and
  *reason* text (plus optional *options*, *lands*, *targets*, *plans*,
  `--review-by DATE`) and a status: `proposed`, `confirmed`, `rejected` or
  `superseded`.
- **Immutable.** The text never changes after it is recorded. A changed mind is a
  new decision with `--supersedes`; once the old one is confirmed, mark it
  superseded (`decision set OLD --status superseded --by NEW`). Only the status moves, and every move lands in an event log.
- Confirm/reject need the user (`--authorization` or the GUI). A short
  authorization (under 8 characters) draws a `DEC.AUTH_WEAK` warning.

```bash
expnote decision new --title "Use a longer warmup next time" --layer research \
  --decided "Try a longer warmup in a follow-up." --reason "The effect may grow with warmup length." \
  --options "Keep the current length; rejected, nothing more is learned." \
  --plans PLAN-001 --status confirmed --authorization "Yes, queue the longer warmup." --apply
expnote decision new --title "Use a longer warmup, with two seeds" --layer research \
  --decided "Try a longer warmup with two seeds." --reason "Cheaper first look." \
  --options "Three seeds; rejected for cost." --plans PLAN-001 --supersedes DEMO-DEC-0005 \
  --status confirmed --authorization "Yes, two seeds is enough." --apply
expnote decision set DEMO-DEC-0005 --status superseded --by DEMO-DEC-0006 --apply
expnote decision list
expnote decision show DEMO-DEC-0005
```

**Backup.** After every committed change expnote rewrites `ops/decisions.jsonl` in
the vault (decisions plus their event log; deterministic, written only when bytes
change). Do not edit it; if someone does, the tool reports `EXPORT_DIVERGED` and
leaves it alone until `decision export --force`. If the SQLite store is lost,
rebuild it from the export (restore refuses a non-empty store unless `--replace`):

```bash
expnote decision export                     # normally automatic; "no changes needed"
STORE="${XDG_DATA_HOME:-$HOME/.local/share}/expnote/research/projects/demo/decisions.sqlite"
mv "$STORE" "$STORE.lost"
expnote decision restore --from "$PROJ/ops/decisions.jsonl" --apply
expnote decision list
```

(The `mv` stands in for a lost disk.) `DEMO-DEC-0004` is the decision that `plan
close` recorded for the forced close in 5.7; ids simply count up.

One decision is deliberately left `proposed` so you can see what *waiting on you*
looks like in the GUI:

```bash
expnote decision new --title "Repeat the study on a second dataset" --layer research \
  --decided "Repeat PLAN-001 on a second dataset." --reason "One dataset may not generalise." \
  --options "Do nothing; rejected, the claim would stay narrow." --plans PLAN-001 --apply
```

## 7. Working with coding agents

Agents read and write the vault with their own file tools for **prose** and
agent-owned frontmatter keys, and use the CLI for everything the tool owns:
status gates, hashes, confirmations, decisions, manual TODOs.

Free notes need no ceremony; the index picks up anything you add, and agents can
search and link them:

```bash
mkdir -p "$PROJ/theory" "$PROJ/protocols"
cat > "$PROJ/theory/Why warmup helps.md" <<'EOF'
---
type: theory
---
# Why warmup helps

Adam's second-moment estimate is noisy early on. Decision [[DEMO-DEC-0001]] applied
this to [[PLAN-001--Learning-rate warmup study]].
EOF
printf '# Training protocol\n\nFixed seeds, fixed data order.\n' > "$PROJ/protocols/Training protocol.md"
```

Read commands (they refresh the index first, never write the vault):

| Command | Gives |
|---|---|
| `expnote next --json` | ordered next steps with the exact command; `--owner user` for the human's queue |
| `expnote plan bundle PLAN-001 --json` | one call: plan, gates, decisions, exec plans, evidence, results, deviations, related notes, runs, open items, findings. `--max-bytes N` spills big bundles to a file (`meta.spillFile`, `meta.nextCursor`) |
| `expnote search QUERY --json` | full text (3+ characters) with `--type --plan --status --linked-to --backlinks-of` |
| `expnote todo list --json` | derived and manual TODOs |
| `expnote check --json` | findings; the gate is exit code 0 |
| `expnote guide research --json`, `guide agent --json` | the workflow and state machines as data |

```bash
expnote next --json | head -c 300
expnote plan bundle PLAN-001 --json --max-bytes 20000 | head -c 300
expnote search "second-moment"
expnote search warmup --type plan --json | head -c 300
expnote todo list --json | head -c 300
```

Conventions an agent can rely on:

- `--json` prints one envelope: `ok`, `schemaVersion`, `command`, `data`,
  `warnings`, `meta`; failures carry `error: {code, message, remediation, details}`.
- **Writes are dry-run by default**: the command prints the diff and exits 4
  (nothing written). Add `--apply` to write.
- Exit codes: `0` ok, `1` findings or a failed gate (`GATE_FAILED` lists what is
  unmet), `2` usage, `3` not found or project not registered, `4` dry-run, `5`
  conflict (note changed, edited a moment ago, or held open: re-read and retry),
  `6` environment (vault unavailable, missing dependency, index locked), `7`
  refused as user-only.
- User-only actions (confirm/reject a decision, `plan close`, `ev label`) return 7
  without `--authorization`. Pass it only with words the user actually said.
- `--expected-sha256` (from `plan show`, `ev label show`, ...) makes a write refuse
  if the note changed meanwhile. Set `EXPNOTE_ACTOR=<agent name>` to label your
  writes in the audit log.
- **End-of-task gate:** `expnote check` must report `FAIL 0` (exit 0) before an
  agent hands work back. WARNs are advice (the printed `WARN` count does not include
  a few newer advisory codes, which are still listed above it).
- Keep raw numbers out of plans, exec plans and results; put them in evidence pages
  and appendices and cite those (`check` warns `PROSE.RAW_NUMBERS`).

Paste into your project's `AGENTS.md` / `CLAUDE.md`:

```markdown
## Research notes (expnote)

- Plans, evidence and results are Markdown in the vault; decisions and runs are in
  expnote. Never create run notes or generated files in the vault.
- Start with `expnote next --json` and `expnote plan bundle <PLAN_ID> --json`.
- Edit prose and agent-owned frontmatter with file tools. Anything the tool owns
  (status, hashes, confirmations, decisions, TODO ids) goes through `expnote`
  commands; writes are dry-run until `--apply`.
- Never invent a user decision. Confirm/reject a decision, close a plan, or label
  evidence only with `--authorization "<the user's own words>"`; exit 7 means ask
  the user (or let them click in `expnote web`).
- Cite runs by id in the `runs:` frontmatter of evidence pages; cite decisions as
  `[[<PREFIX>-0001]]`.
- Before finishing: `expnote check` must end `FAIL 0`.
```

## 8. Sync, backup and migration

**Sync.** Any client that moves plain files works. Writes are atomic (temp file
plus rename) and compare the file's hash first, so a note changed meanwhile gives
exit 5 instead of a lost edit. A *quiet window* (10 seconds by default; set per
project with `expnote project register ... --quiet-window-seconds N`) refuses to
write a note that was modified a moment ago, since an editor or the sync client may
still be holding it. When you script an edit followed by a write, pass
`--no-quiet` (audited). Sync-conflict files (`*.sync-conflict-*`, `*conflicted
copy*`, plus `scan.conflict_globs`) are excluded from the index and surface in
`doctor` and as a derived TODO; reconcile them by hand and delete the loser. Stale
`.expnote.*.tmp` files from a crashed write are removed with
`expnote doctor --apply`.

**Backup.** The vault is covered by your sync or Git. Also back up the state dir
(`~/.local/share/expnote`): the decision store, its event log, run workspaces and the
audit journal. The note index is derived and can be rebuilt (`expnote reindex`); the
decision store can be rebuilt from `ops/decisions.jsonl` (section 6), but run
workspaces cannot, and `workspace pack` / `unpack` moves them between machines (see
the main README). The registry file `~/.config/expnote/projects.toml` is small;
`expnote project rebind` repoints a project when the vault path or mount changes.

**Migrating an existing vault.** `expnote migrate` is a one-shot pipeline (backup,
profile, plan, apply steps 1 to 10, verify, cleanup; every step is a dry-run until
`--apply`) that was written for one particular vault and contains its assumptions:
`migrate profile` writes a pre-baked profile with that vault's wording, so skip it
and write your own profile (section 3.4) instead. Rehearse on a copy of the vault
with its own `XDG_*` state dir first, take `expnote migrate backup` before touching
the real thing, read the review tables that `migrate plan` writes, and only then
apply step by step. For a young vault it is usually easier to point `project init`
at the folder, run `expnote check`, and fix what it reports.

## 9. Closing the loop

```bash
expnote check
expnote doctor
```

The first ends with `FAIL 0` and, here, a few WARNs: the force-closed second plan
(its skipped gates stay visible on purpose) and the decision `plan close` recorded
for it, which lists no options. `doctor` lists `OK` for
every hygiene check and `INFO` for the rest. Open `expnote web` and go to
`/research/#/p/demo`: the Overview shows plans and their decisions on a
timeline, what waits on you, the agent queue, recent runs and the latest decisions;
the other tabs are Decisions, Evidence, TODOs, Search and Health. The GUI writes
only: approve or reject a decision, close a plan, adopt a draft as a plan, save
evidence labels, add, tick or drop a TODO, close a deviation. Everything else is read-only,
so the vault stays yours.
