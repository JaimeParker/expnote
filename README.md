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
expnote init --workspace stackcube-demo

expnote moc add \
  --workspace stackcube-demo \
  --moc-id stackcube \
  --title "StackCube training" \
  --json

expnote topic add "260703-StackCube SAC reproduction" \
  --workspace stackcube-demo \
  --moc-id stackcube \
  --json

expnote run add \
  --workspace stackcube-demo \
  --moc-id stackcube \
  --topic "260703-StackCube SAC reproduction" \
  --run-id a7zf90k7 \
  --purpose "Reproduce seed=1 trajectory on current main" \
  --analysis "Initial observation notes" \
  --status running \
  --meta algo=sac \
  --meta env_id=StackCube-v1 \
  --json

expnote web --workspace stackcube-demo --no-open
```

`expnote run create --id ...` is also accepted as an alias for
`expnote run add --run-id ...`.

When a training run is tracked by wandb, prefer using the wandb run id as the
expnote run id. That keeps lookup simple across expnote, Obsidian, and wandb.

The same workflow without installing:

```bash
PYTHONPATH=. python -m expnote.cli init --workspace stackcube-demo
```

## Obsidian layout

For an Obsidian vault that is synced by WebDAV, keep expnote state outside the
vault and write only Markdown into the vault:

```bash
expnote init \
  --workspace mani-skill-training \
  --workspace-dir ~/.local/share/expnote/workspaces/mani-skill-training \
  --obsidian-root "/home/hazyparker/Documents/Cyber Brain" \
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
  10 Projects/AI Lab RFT 项目/ManiSkill Training/analyses/
    <doc_id>.md
```

If `--workspace-dir` is omitted, expnote uses
`~/.local/share/expnote/workspaces/<workspace>`. For synced Obsidian vaults,
keep `--workspace-dir` outside the vault and store only generated Markdown under
`--obsidian-root`.
If `--docs-dir` is omitted, expnote writes analysis documents next to the run
notes directory: `.../runs` becomes `.../analyses`.

SQLite is the source of truth. Markdown is a projection for reading, linking, and
searching. `Purpose`, `Relation`, `Result`, `Metadata`, and `Analysis` are stored
in SQLite and returned by `expnote run show <run_id> --json`.

SQL MOC records are the first-level organization. Topics belong to a MOC; runs
belong to one topic. Obsidian MOC files are only Markdown projections of that
SQLite hierarchy.

Generated Markdown is wrapped in managed markers. Edit structured fields through
the CLI. The only Obsidian-editable field is the run note `Analysis` section
inside `expnote:analysis` markers. To import those edits back into SQLite, run:

```bash
expnote sync markdown --pull-analysis
```

If Analysis was changed in Obsidian, plain `expnote sync markdown` refuses to
overwrite it. Use `--pull-analysis` to keep the Obsidian text, or `--force` to
restore the SQLite version.

## Cross-run analysis documents

Use `doc` commands when one analysis document compares or summarizes multiple
runs. The document body and run links are stored in SQLite; Obsidian receives a
generated `analyses/<doc_id>.md` projection.

```bash
expnote doc add \
  --doc-id calql-baseline-summary \
  --moc-id calql \
  --title "Cal-QL baseline summary" \
  --run-id a7zf90k7 \
  --run-id 53ojw3kc \
  --body "Initial cross-run comparison." \
  --json

expnote doc update calql-baseline-summary \
  --append-body "Seed 1 converged faster than seed 3." \
  --json

expnote doc show calql-baseline-summary --json
expnote sync markdown
```

If a document body is edited in Obsidian, plain `expnote sync markdown` refuses
to overwrite it. Use `--pull-docs` to import the Obsidian body into SQLite, or
`--force` to restore the SQLite version.

## Read-only web UI

Start a local read-only web UI that reads directly from SQLite:

```bash
expnote web --workspace example
```

The web UI is independent from Obsidian. It shows SQL MOCs, topics, runs, run
details, rendered Analysis, and MOC-level analysis documents. It defaults to
`127.0.0.1`; pass `--host 0.0.0.0` only when you explicitly want LAN access.

## Markdown MOC section tables

Add a run to a managed table under a specific level-two heading:

```bash
expnote markdown table add lu8qk41s \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC"
```

Markdown table membership is stored in SQLite. The table itself is rendered inside
`expnote:moc-table` markers under the requested `##` heading. A run can appear in
multiple Markdown files or sections.

The generated auto index defaults to `workspace_dir/index.md`, outside the
Obsidian vault. `markdown table` commands own curated MOC section tables inside
the vault.

See [docs/templates/training-record-example.md](docs/templates/training-record-example.md)
for an example MOC and generated `runs/<id>.md` note.

Useful operations:

```bash
expnote markdown table sections \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --json

expnote markdown table add-topic \
  --topic "260703-StackCube SAC reproduction" \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote markdown table list \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote markdown table diff \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC" \
  --json

expnote markdown table remove lu8qk41s \
  --moc-path "10 Projects/AI Lab RFT 项目/ManiSkill Training MOC.md" \
  --section "StackCube SAC"
```

`markdown table sync` re-renders registered rows for an exact section name. It
does not discover runs by topic. Use `markdown table sections` to inspect
existing section names and `markdown table add-topic` to register all active
runs from a topic.

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

For direct status lookup:

```bash
expnote run status running --json
expnote run query --status running --json
expnote run list --status finished --json
```

`status` is a manual expnote field. When a training job finishes, update it
explicitly:

```bash
expnote run update lu8qk41s --status finished
```

To fetch one field when the run id is known:

```bash
expnote run show lu8qk41s --field purpose
expnote run show lu8qk41s --field status
expnote run show lu8qk41s --field metadata --json
```

Metadata values written with `--meta` are strings. Use `--meta-json` for typed
`key=json` values, `--metadata-json` for a whole JSON object, and `--unset-meta`
to delete a key:

```bash
expnote run update lu8qk41s --meta-json seed=1 --meta-json use_wandb=true
expnote run update lu8qk41s --metadata-json '{"algo":"calql","seed":1}'
expnote run update lu8qk41s --unset-meta seed
```

Use `--append-analysis` to add an observation without replacing existing
Analysis. expnote inserts one blank line between the old and new text:

```bash
expnote run update lu8qk41s --append-analysis "Reward plateaued after 300k steps."
```

When one SQL text field refers to another run, prefer `[[run_id]]`. Obsidian
renders that as a note link, and the web UI renders it as a run-detail link. The
web UI also links bare active run ids when it can do so safely, but `[[run_id]]`
is the clearest format for agents.

`run query` uses a restricted SQL-like syntax. `--where` supports simple
comparisons joined by `AND`; `--order-by` supports one whitelisted field plus
optional `ASC` or `DESC`. One-level metadata keys can be queried with
`metadata.<key>`:

```bash
expnote run query --status running --where "metadata.seed = 1" --json
expnote run query --where "metadata.seed = 1" --json
expnote run query --where "metadata.algo = 'sac'" --json
```

`OR`, `LIKE`, functions, nested metadata fields, and subqueries are not
supported yet.

Agent contract:

- Prefer `--json` for automation.
- Use `expnote workspace use <name>` once, or pass `--workspace <name>` on
  follow-up commands.
- Treat non-zero exit status as command failure.
- Do not edit generated Markdown directly except Analysis inside
  `expnote:analysis` markers and document body inside `expnote:doc-body` markers.
- Keep `Result` concise and outcome-only. Put interpretation, diagnosis,
  comparisons, and reasoning in run `Analysis` or cross-run `doc` records.
- Refer to runs as `[[run_id]]` inside Purpose, Relation, Result, Analysis, and
  doc bodies when the reference should be clickable in both Obsidian and web.
- Use `expnote markdown table diff --json` before trusting a manually edited
  MOC table.
- Run `expnote sync all` after structured updates if Markdown and curated MOCs
  were not synced by the caller workflow. `sync markdown` updates run notes,
  analysis documents, and the auto index only.
- Use `expnote validate --json` before handing off long-lived notes.

## Validate locally

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
RUFF_CACHE_DIR=/tmp/expnote-ruff-cache ruff check .
```
