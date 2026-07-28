# Agent Quickstart

This guide is for agents that need to operate expnote through the CLI.

If the agent can only run expnote commands and cannot read repository files, start
with the built-in guide:

```bash
expnote guide agent --json
```

Without installation, use:

```bash
PYTHONPATH=. python -m expnote.cli guide agent --json
```

## Core Rules

- SQLite is the source of truth.
- Markdown is a projection for Obsidian, Git, WebDAV, and search.
- Use `--json` for automation.
- Reuse the same `--root` and `--state-dir` for every command in the workspace.
- Edit `Purpose`, `Relation`, `Result`, `Metadata`, and `Analysis` through CLI
  commands, except human-authored Obsidian Analysis imports.

## Minimal Workflow

```bash
expnote init \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --notes-dir "00 Inbox/runs" \
  --json

expnote topic add "StackCube SAC" \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote run add \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --topic "StackCube SAC" \
  --run-id stackcube-sac-seed1 \
  --purpose "Train SAC on StackCube seed=1" \
  --relation "Baseline run" \
  --result "Training in progress" \
  --analysis "Watch success rate and critic loss stability." \
  --status running \
  --meta algo=sac \
  --meta seed=1 \
  --json

expnote moc add stackcube-sac-seed1 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote sync markdown \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json
```

## Query Records

Do not grep generated Markdown for facts. Query SQLite through expnote:

```bash
expnote run show stackcube-sac-seed1 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote run query \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --where "status = 'running' AND topic = 'StackCube SAC'" \
  --order-by updated_at DESC \
  --json
```

## Obsidian Analysis Imports

Generated Markdown is managed by expnote. The only Obsidian-editable area is the
run note content inside `expnote:analysis` markers.

If plain sync refuses to overwrite a changed Analysis section:

```bash
expnote sync markdown \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --pull-analysis \
  --json
```

Use `--force` only when SQLite should overwrite Obsidian Analysis.

## MOC Table Checks

MOC section tables are managed inside `expnote:moc-table` markers under `##`
headings.

```bash
expnote moc diff \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote moc sync \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json
```

## Handoff Checklist

Run these before handing off long-lived notes:

```bash
expnote validate \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote moc diff \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json
```

See `docs/templates/training-record-example.md` for a concrete MOC and run note
projection example.
