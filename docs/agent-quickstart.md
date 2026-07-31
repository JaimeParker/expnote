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
- SQL MOC records are the first-level organization. Topics belong to a MOC.
- Markdown is a projection for Obsidian, Git, WebDAV, and search.
- Use `--json` for automation.
- Reuse the same `--root` and `--state-dir` for every command in the workspace.
- Edit `Purpose`, `Relation`, `Result`, `Metadata`, and `Analysis` through CLI
  commands, except human-authored Obsidian Analysis imports.
- Keep `Result` concise and outcome-only. Put interpretation, diagnosis,
  comparisons, and reasoning in run `Analysis` or cross-run `doc` records.

## Minimal Workflow

```bash
expnote init \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --notes-dir "00 Inbox/runs" \
  --json

expnote moc add \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-id stackcube \
  --title "StackCube training" \
  --json

expnote topic add "StackCube SAC" \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-id stackcube \
  --json

expnote run add \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-id stackcube \
  --topic "StackCube SAC" \
  --run-id a7zf90k7 \
  --purpose "Train SAC on StackCube seed=1" \
  --relation "Baseline run" \
  --result "Training in progress" \
  --analysis "Watch success rate and critic loss stability." \
  --status running \
  --meta algo=sac \
  --meta-json seed=1 \
  --json

expnote markdown table add a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote sync all \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json
```

When a training run is tracked by wandb, prefer using the wandb run id as the
expnote run id. That keeps lookup simple across expnote, Obsidian, and wandb.

Prefer `expnote sync all` when handing off a workspace with curated MOCs. It
updates run notes, the auto index, and all registered curated MOC sections.
`sync markdown` updates run notes, analysis documents, and the auto index only.

For read-only browsing independent from Obsidian:

```bash
expnote web \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example
```

## Query Records

Do not grep generated Markdown for facts. Query SQLite through expnote:

```bash
expnote run show a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --field purpose

expnote run show a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote run query \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --where "status = 'running' AND metadata.seed = 1" \
  --order-by updated_at DESC \
  --json

expnote run status running \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote run update a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --status finished \
  --json

expnote run update a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --unset-meta seed \
  --json

expnote run update a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --append-analysis "Reward plateaued after 300k steps." \
  --json

expnote run update a7zf90k7 \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --metadata-json '{"algo":"sac","seed":1}' \
  --json
```

Use `--append-analysis` when adding observations to existing Analysis. It inserts
one blank line between old and new text. Use `--analysis` only when replacing the
whole Analysis field.

Use `run status <status> --json` for direct status lookup before handoff.
`status` is manual. If external checks show a run has completed, update SQLite
with `run update <id> --status finished`.

Use `--metadata-json '{...}'` to merge a whole metadata object. Use
`--meta-json key=json` only for one typed key at a time.

## Cross-run Documents

Use `doc` commands when one analysis document compares or summarizes multiple
runs. The body and run links are stored in SQLite; Obsidian receives an
`analyses/<doc_id>.md` projection.

```bash
expnote doc add \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --doc-id stackcube-seed-summary \
  --moc-id stackcube \
  --title "StackCube seed summary" \
  --run-id a7zf90k7 \
  --body "Initial cross-run comparison." \
  --json

expnote doc link stackcube-seed-summary other_wandb_id \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --role ablation \
  --json

expnote doc update stackcube-seed-summary \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --append-body "Seed 1 reached success earlier than the ablation." \
  --json

expnote doc show stackcube-seed-summary \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json
```

If plain sync refuses to overwrite a changed document body, use
`sync markdown --pull-docs` to import the Obsidian body into SQLite. Use
`--force` only when SQLite should overwrite Obsidian document body edits.

## Obsidian Analysis Imports

Generated Markdown is managed by expnote. The only Obsidian-editable area is the
run note content inside `expnote:analysis` markers and the document body inside
`expnote:doc-body` markers.

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

The generated auto index defaults to `state_dir/index.md`, outside Obsidian. The
curated MOC path is owned by `markdown table add/remove/update/sync`.

```bash
expnote markdown table diff \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote markdown table sections \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --json

expnote markdown table add-topic \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --topic "StackCube SAC" \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote markdown table sync \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json
```

Section names are exact strings. If unsure, run `markdown table sections` before
`markdown table sync`. `markdown table sync` only re-renders already registered
rows; use `markdown table add` or `markdown table add-topic` to register new
runs.

## Handoff Checklist

Run these before handing off long-lived notes:

```bash
expnote validate \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --json

expnote markdown table diff \
  --root "/path/to/obsidian/vault" \
  --state-dir ~/.local/share/expnote/workspaces/example \
  --moc-path "00 Inbox/Training MOC.md" \
  --section "StackCube SAC" \
  --json
```

See `docs/templates/training-record-example.md` for a concrete MOC and run note
projection example.
