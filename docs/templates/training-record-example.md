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
  --moc-path "10 Projects/Robot Learning/Training MOC.md" \
  --notes-dir "10 Projects/Robot Learning/runs"

expnote topic add "StackCube SAC" \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning

expnote run add \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --topic "StackCube SAC" \
  --run-id stackcube-sac-seed1 \
  --purpose "Train SAC on StackCube with seed=1" \
  --relation "Baseline run for later ablations" \
  --result "Training in progress" \
  --analysis "Watch success rate and critic loss stability." \
  --status running \
  --meta algo=sac \
  --meta env_id=StackCube-v1 \
  --meta seed=1

expnote moc add stackcube-sac-seed1 \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --moc-path "10 Projects/Robot Learning/Training MOC.md" \
  --section "StackCube SAC"
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

Human edits should stay inside `expnote:analysis` markers. Import those edits
back into SQLite with:

```bash
expnote sync markdown \
  --root "/home/user/Documents/Cyber Brain" \
  --state-dir ~/.local/share/expnote/workspaces/robot-learning \
  --pull-analysis
```
