# expnote

`expnote` is a local-first CLI for training runs, experiment records, and
benchmark matrices. It stores structured records in SQLite. Use the local
Web UI to browse runs, compare benchmark cells, and edit analysis.

Research links a run workspace to a vault of plans, evidence, and results.
Training frameworks and metric trackers can integrate through adapters.

- **Tutorials:** [English](docs/index.rst) · [简体中文](docs/zh/index.rst)
- **Workflows:** [Training and benchmarks](docs/tutorials/experiments.rst) ·
  [Research](docs/tutorials/research.rst)
- **Reference:** [Agent Quickstart](docs/agent-quickstart.md) ·
  [Research vault guide](docs/research-vault.md)
- **Maintainers:** [Build and publish the docs](docs/README.md)

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
expnote run id. That keeps lookup simple across expnote and wandb.

The same workflow without installing:

```bash
PYTHONPATH=. python -m expnote.cli init --workspace stackcube-demo
```

## Benchmarks

Create a task-by-algorithm matrix, link finished or failed runs, and open the cells in
the Web UI. Follow the [training and benchmarks tutorial](docs/tutorials/experiments.rst).

## Cross-run analysis documents

Use `doc` commands when one analysis document compares or summarizes multiple
runs. SQLite stores the document body and run links. Open the document under
its MOC in the Web UI.

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
```

`--body-file`/`--append-body-file` (and the `run` equivalents
`--analysis-file`/`--append-analysis-file`) accept a file path or `-` for
stdin, avoiding shell substitution like `--body "$(cat notes.md)"`:

```bash
expnote doc update calql-baseline-summary \
  --append-body-file notes.md \
  --json
```

Doc bodies can reference Web-only charts with placeholders:

```markdown
Seed 1 converges faster early on.

{{ chart:eval_return }}

The later plateau suggests the bottleneck is not sample count.
```

Put chart data and configuration in the workspace state directory:

```text
<workspace-dir>/doc-assets/<doc_id>/
  charts.json
  metrics.csv
  plot.py
  curve.png
  curve.plotly.json
```

For the default local workspace layout, `<workspace-dir>` is usually `.expnote`.
Agents should copy or write data files there, write `charts.json`, then add a
matching `{{ chart:<chart_id> }}` placeholder with `expnote doc update`.

For simple CSV/NPZ series charts, use `charts.json`:

```json
[
  {
    "id": "eval_return",
    "title": "Eval Return",
    "type": "series",
    "source": "metrics.csv",
    "x": "step",
    "y": ["eval/return", "eval/success_rate"],
    "max_points": 2000
  }
]
```

For advanced charts, use a Python chart. The Web UI runs the script on demand
from the doc asset directory and expects both a static image and Plotly JSON:

```json
[
  {
    "id": "custom_curve",
    "title": "Custom Curve",
    "type": "python",
    "script": "plot.py",
    "png": "custom_curve.png",
    "plotly": "custom_curve.plotly.json",
    "timeout_seconds": 60
  }
]
```

## Web UI

Start a local Web UI that reads directly from SQLite:

```bash
expnote web --workspace stackcube-demo
```

Run and document detail pages include explicit Edit/Save controls. Saving from
the Web UI writes to SQLite and the audit log.

To start it in the background and return to the shell immediately:

```bash
expnote web --workspace stackcube-demo --detach --no-open
```

The Web UI shows MOCs, topics, runs, benchmark matrices, and MOC-level
analysis documents. It defaults to
`127.0.0.1`; pass `--host 0.0.0.0` only when you explicitly want LAN access.

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

Use `--analysis-file`/`--append-analysis-file` to read long analysis text from
a file or `-` for stdin instead of an inline string.

Use `[[run_id]]` to link to a run from another record or from an analysis
document. The Web UI opens the matching run detail.

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
- Update run fields with `expnote run update` and document bodies with
  `expnote doc update`.
- Record a one-line headline metric in `Result`, such as the final return or
  success rate. Put interpretation, comparisons, and next steps in
  `Analysis` or cross-run `doc` records.
- Refer to runs as `[[run_id]]` inside Purpose, Relation, Result, Analysis, and
  doc bodies when you want a clickable reference in the Web UI.
- Run `expnote guide agent --json` and read the `run_record_template` key
  for a full example of a finished record with good Purpose, Relation,
  Result, Analysis, and Metadata content, plus a short checklist.
- Use `expnote validate --json` before handing off long-lived notes.

## Move a workspace

Pack the SQLite workspace on the source machine and unpack it on the
destination.

```bash
expnote workspace pack training.tar.gz --workspace stackcube-demo
expnote workspace unpack training.tar.gz --workspace stackcube-demo --no-obsidian
```

Open the workspace with `expnote web --workspace stackcube-demo`. The
`--no-obsidian` flag clears the projection settings carried by older archives.

## Validate locally

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
RUFF_CACHE_DIR=/tmp/expnote-ruff-cache ruff check .
```
