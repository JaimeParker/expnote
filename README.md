# expnote

`expnote` is a local-first experiment note CLI. It stores structured experiment
records in SQLite and renders Markdown for Obsidian, Git, WebDAV, and plain-text
search.

The core tool is generic. RL frameworks, Weights & Biases, MLflow, or lab-specific
layouts should integrate through adapters instead of changing the core schema.

## Install for development

From this repository:

```bash
python -m pip install -e ".[dev]"
```

Without installing, run commands with:

```bash
PYTHONPATH=. python -m expnote.cli --help
```

## Quick start

```bash
expnote init --notes-dir notes --moc-path notes/experiments.md

expnote topic add "260703-StackCube SAC reproduction" --json

expnote run add \
  --topic "260703-StackCube SAC reproduction" \
  --run-id lu8qk41s \
  --purpose "Reproduce seed=1 trajectory on current main" \
  --status running \
  --meta algo=sac \
  --meta env_id=StackCube-v1 \
  --json

expnote sync markdown
```

The same workflow without installing:

```bash
PYTHONPATH=. python -m expnote.cli init --notes-dir notes --moc-path notes/experiments.md
```

## Obsidian layout

For an Obsidian vault, initialize inside the vault or pass `--root`:

```bash
expnote init \
  --root "10 Projects/AI Lab RFT 项目" \
  --notes-dir "ManiSkill Training" \
  --moc-path "ManiSkill Training MOC.md"
```

This creates:

```text
.expnote/
  expnote.sqlite
  events.jsonl
  config.toml
ManiSkill Training MOC.md
ManiSkill Training/
  <run_id>.md
```

SQLite is the source of truth. Markdown is a projection for reading, linking, and
searching. Generated Markdown is wrapped in managed markers; write manual
analysis outside those blocks or in the generated run note analysis section.

## Agent-friendly output

Most mutating and query commands accept `--json` and return stable JSON.

```bash
expnote run query \
  --where "status = 'finished' AND topic = 'StackCube ablations'" \
  --order-by started_at desc \
  --json
```

`run query` uses a restricted SQL-like syntax. `--where` supports simple
comparisons joined by `AND`; `--order-by` supports one whitelisted field plus
optional `ASC` or `DESC`. Metadata queries, `OR`, `LIKE`, functions, and
subqueries are not supported yet.

Agent contract:

- Prefer `--json` for automation.
- Treat non-zero exit status as command failure.
- Do not edit generated Markdown directly unless editing outside managed blocks.
- Run `expnote sync markdown` after structured updates if Markdown was not synced
  by the caller workflow.
- Use `expnote validate --json` before handing off long-lived notes.

## rl-garden adapter

Import a resolved training config:

```bash
expnote import rlgarden runs/<run_name>/config.json \
  --topic "StackCube ablations" \
  --status running \
  --json
```

The adapter reads local resolved `config.json` files. W&B and MLflow network
integrations are not part of the current core CLI.

## Validate locally

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
RUFF_CACHE_DIR=/tmp/expnote-ruff-cache ruff check .
```
