# expnote

`expnote` is a local-first experiment note CLI. It stores structured experiment
records in SQLite and renders Markdown for Obsidian, Git, WebDAV, and plain-text
search.

The core tool is generic. Training frameworks, metric trackers, and lab-specific
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
  --analysis "Initial observation notes" \
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

For an Obsidian vault that is synced by WebDAV, keep expnote state outside the
vault and write only Markdown into the vault:

```bash
expnote init \
  --state-dir ~/.local/share/expnote/workspaces/mani-skill-training \
  --root "/home/hazyparker/Documents/Cyber Brain" \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --notes-dir "10 Projects/AI Lab RFT 项目/ManiSkill Training/runs"
```

This creates:

```text
~/.local/share/expnote/workspaces/mani-skill-training/
  expnote.sqlite
  events.jsonl
  config.toml
Cyber Brain/
  10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md
  10 Projects/AI Lab RFT 项目/ManiSkill Training/runs/
    <run_id>.md
```

If `--state-dir` is omitted, expnote uses `<root>/.expnote`, which is convenient
for a simple local workspace but not recommended for a synced Obsidian vault.

SQLite is the source of truth. Markdown is a projection for reading, linking, and
searching. `Purpose`, `Relation`, `Result`, `Metadata`, and `Analysis` are stored
in SQLite and returned by `expnote run show <run_id> --json`.

Generated Markdown is wrapped in managed markers. Edit structured fields through
the CLI. The only Obsidian-editable field is the run note `Analysis` section
inside `expnote:analysis` markers. To import those edits back into SQLite, run:

```bash
expnote sync markdown --pull-analysis
```

If Analysis was changed in Obsidian, plain `expnote sync markdown` refuses to
overwrite it. Use `--pull-analysis` to keep the Obsidian text, or `--force` to
restore the SQLite version.

## MOC section tables

Add a run to a managed table under a specific level-two heading:

```bash
expnote moc add lu8qk41s \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC"
```

MOC table membership is stored in SQLite. The table itself is rendered inside
`expnote:moc-table` markers under the requested `##` heading. A run can appear in
multiple MOCs or sections.

See [docs/templates/training-record-example.md](docs/templates/training-record-example.md)
for an example MOC and generated `runs/<id>.md` note.

Useful operations:

```bash
expnote moc list \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote moc diff \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote moc remove lu8qk41s \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC"
```

## Agent-friendly output

Most mutating and query commands accept `--json` and return stable JSON.
Agents that cannot read repository files can learn the workflow from the CLI:

```bash
expnote guide agent
expnote guide agent --json
```

Agents that can read files should also read
[docs/agent-quickstart.md](docs/agent-quickstart.md).

```bash
expnote run query \
  --where "status = 'finished' AND topic = 'StackCube ablations'" \
  --order-by started_at desc \
  --json
```

To fetch one field when the run id is known:

```bash
expnote run show lu8qk41s --field purpose
expnote run show lu8qk41s --field status
expnote run show lu8qk41s --field metadata --json
```

Metadata values written with `--meta` are strings. Use `--meta-json` for typed
values, and `--unset-meta` to delete a key:

```bash
expnote run update lu8qk41s --meta-json seed=1 --meta-json use_wandb=true
expnote run update lu8qk41s --unset-meta seed
```

`run query` uses a restricted SQL-like syntax. `--where` supports simple
comparisons joined by `AND`; `--order-by` supports one whitelisted field plus
optional `ASC` or `DESC`. One-level metadata keys can be queried with
`metadata.<key>`:

```bash
expnote run query --where "metadata.seed = 1" --json
expnote run query --where "metadata.algo = 'sac'" --json
```

`OR`, `LIKE`, functions, nested metadata fields, and subqueries are not
supported yet.

Agent contract:

- Prefer `--json` for automation.
- If a workspace was initialized with `--state-dir`, pass the same `--state-dir`
  on follow-up commands.
- Treat non-zero exit status as command failure.
- Do not edit generated Markdown directly except Analysis inside
  `expnote:analysis` markers.
- Use `expnote moc diff --json` before trusting a manually edited MOC table.
- Run `expnote sync markdown` after structured updates if Markdown was not synced
  by the caller workflow.
- Use `expnote validate --json` before handing off long-lived notes.

## Validate locally

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
RUFF_CACHE_DIR=/tmp/expnote-ruff-cache ruff check .
```
