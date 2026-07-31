# Training Record Example

This file shows the intended Obsidian projection for one training MOC and one
generated run note. SQLite remains the source of truth; Markdown is for reading,
linking, and review.

## Example Layout

```text
Cyber Brain/
  10 Projects/Robot Learning/Training MOC.md
  10 Projects/Robot Learning/runs/
    stackcube-sac-seed1.md
  10 Projects/Robot Learning/analyses/
    stackcube-seed-summary.md
~/.local/share/expnote/workspaces/robot-learning/
  expnote.sqlite
  events.jsonl
  config.toml
```

## Setup Commands

```bash
expnote init \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --root "/home/user/Documents/Cyber Brain" \
  --notes-dir "10 Projects/Robot Learning/runs"

expnote moc add \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --moc-id robot-learning \
  --title "Robot Learning Training"

expnote topic add "StackCube SAC" \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --moc-id robot-learning

expnote run add \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --moc-id robot-learning \
  --topic "StackCube SAC" \
  --run-id stackcube-sac-seed1 \
  --purpose "Train SAC on StackCube with seed=1" \
  --relation "Baseline run for later ablations" \
  --result "Training in progress" \
  --analysis "Watch success rate and critic loss stability." \
  --status running \
  --meta algo=sac \
  --meta env_id=StackCube-v1 \
  --meta-json seed=1

expnote markdown table add stackcube-sac-seed1 \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --moc-path "10 Projects/Robot Learning/Training MOC.md" \
  --section "StackCube SAC"

expnote doc add \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --doc-id stackcube-seed-summary \
  --moc-id robot-learning \
  --title "StackCube seed summary" \
  --run-id stackcube-sac-seed1 \
  --body "Initial cross-run comparison."
```

## MOC Example

```markdown
# Training MOC

## StackCube SAC

<!-- expnote:moc-table:start -->

| # | run | purpose | relation | result | status |
| --- | --- | --- | --- | --- | --- |
| 1 | [[stackcube-sac-seed1]] | Train SAC on StackCube with seed=1 | Baseline run for later ablations | Training in progress | running |

<!-- expnote:moc-table:end -->
```

## Run Note Example

```markdown
<!-- expnote:managed:start -->

# stackcube-sac-seed1

- status: `running`
- started_at: `2026-07-28T12:00:00+00:00`

## Purpose

Train SAC on StackCube with seed=1

## Relation

Baseline run for later ablations

## Result

Training in progress

## Metadata

- `algo`: sac
- `env_id`: StackCube-v1
- `seed`: 1

## Analysis

<!-- expnote:analysis:start -->

Watch success rate and critic loss stability.

<!-- expnote:analysis:end -->

<!-- expnote:managed:end -->
```

## Analysis Document Example

```markdown
<!-- expnote:managed:start -->

# StackCube seed summary

- id: `stackcube-seed-summary`
- moc: Robot Learning Training
- updated_at: `2026-07-28T12:00:00+00:00`

## Metadata

_No metadata recorded._

## Related Runs

| # | run | role | note | status | result |
| --- | --- | --- | --- | --- | --- |
| 1 | [[stackcube-sac-seed1]] |  |  | running | Training in progress |

## Body

<!-- expnote:doc-body:start -->

Initial cross-run comparison.

<!-- expnote:doc-body:end -->

<!-- expnote:managed:end -->
```

Human run-note edits should stay inside `expnote:analysis` markers. Human
analysis-document edits should stay inside `expnote:doc-body` markers. Import
those edits back into SQLite with:

```bash
expnote sync markdown \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --pull-analysis \
  --pull-docs
```
