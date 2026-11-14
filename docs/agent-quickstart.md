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
- Use `--json` for automation.
- Use `expnote workspace use <name>` once, or pass `--workspace <name>` on
  follow-up commands.
- Edit `Purpose`, `Relation`, `Result`, `Metadata`, and `Analysis` through CLI
  commands or the Web UI.
- Record a one-line headline metric in `Result`, such as the final return or
  success rate. Put interpretation, comparisons, and next steps in
  `Analysis` or cross-run `doc` records.
- Write clickable run references as `[[run_id]]` in Purpose, Relation, Result,
  Analysis, and doc bodies. The Web UI resolves that form.
- See `run_record_template` in `expnote guide agent --json` (or "Good Run
  Record Template" below) for a full example of a finished record with
  good Purpose, Relation, Result, Analysis, and Metadata content.
- Use `workspace pack` and `workspace unpack` to move SQLite state to another
  device.

## Minimal Workflow

```bash
expnote init --workspace example --json

expnote moc add \
  --workspace example \
  --moc-id stackcube \
  --title "StackCube training" \
  --json

expnote topic add "StackCube SAC" \
  --workspace example \
  --moc-id stackcube \
  --json

expnote run add \
  --workspace example \
  --moc-id stackcube \
  --topic "StackCube SAC" \
  --run-id a7zf90k7 \
  --purpose "Train SAC on StackCube seed=1" \
  --relation "Baseline run" \
  --analysis "Watch success rate and critic loss stability." \
  --status running \
  --meta algo=sac \
  --meta-json seed=1 \
  --json
```

When a training run is tracked by wandb, prefer using the wandb run id as the
expnote run id. That keeps lookup simple across expnote and wandb.

Open the workspace in the Web UI.

```bash
expnote web --workspace example
expnote web --workspace example --detach --no-open
```

Run and document detail pages include explicit Edit/Save controls. Web saves
write to SQLite and the audit log.

## TensorBoard Charts in the Web UI

If a run's metrics only exist as local TensorBoard event files (no wandb),
point `metadata.tensorboard_dir` at that logdir so the run page can chart
them:

```bash
expnote run update a7zf90k7 \
  --workspace example \
  --meta tensorboard_dir=/path/to/logdir \
  --json
```

- `tensorboard_dir` must be a path readable from the machine running
  `expnote web` (not a remote/relative path) — a local mount or synced copy
  if the logs were written on a different host.
- Accepts either a flat directory (event files directly inside it) or a
  directory with per-phase subdirectories (e.g. `offline/`, `online/`),
  matching however the training job's `SummaryWriter`(s) were laid out.
- If `tensorboard_dir` points at a batch root and it contains a child
  directory named exactly like the expnote run id, the run page reads that
  child directory instead of expanding every run in the batch.
- Opening the run in `expnote web` shows a "TensorBoard Charts" panel (only
  when `tensorboard_dir` is set) with a "Fetch TensorBoard charts" button;
  it re-reads all scalar points from disk on each fetch (no caching, unlike
  the wandb panel). For very large logs, the Web API also accepts an explicit
  scalar cap, e.g. `/api/runs/<run_id>/tensorboard?samples=50000`; `samples=0`
  means no scalar sampling limit.
- Run chart panels show split metric charts only. `hparam/*` TensorBoard
  scalars and single-point scalars are not rendered as curves.
- This mirrors the existing `metadata.wandb_url` chart panel; a run can have
  both set and both panels render independently.
- Requires the `tensorboard` Python package in the environment running
  `expnote web` (optional dependency, same pattern as `wandb`); if it's
  missing, fetching reports `tensorboard_not_installed` instead of failing
  silently.
- Remove the field with `expnote run update a7zf90k7 --workspace example
  --unset-meta tensorboard_dir` if the logdir moves or is deleted.

## Good Run Record Template

The minimal workflow leaves `a7zf90k7` in progress. Once the run finishes,
record the headline metric in Result and the interpretation in Analysis.

```bash
expnote run update a7zf90k7 \
  --workspace example \
  --status finished \
  --result "78% success at 1M steps" \
  --append-analysis "Success rate plateaued at 78% after roughly 700k steps; critic loss stabilized after 300k steps with no divergence, so this baseline is stable enough to compare against. [[k2m9p3qw]] (higher actor LR) converges faster but reaches a similar final success rate, so LR is not the current bottleneck. Next: rerun at 2M steps with image observations to confirm 78% is a real plateau and not an early stop." \
  --meta-json total_steps=1000000 \
  --json
```

What makes each field good here:

- Purpose (`Train SAC on StackCube seed=1`) states the specific
  configuration under test, not just "training run".
- Relation (`Baseline for [[k2m9p3qw]] (higher actor-LR ablation on the
  same env)`) links related runs with `[[run_id]]` so this run's place in
  a comparison chain is discoverable.
- Result is one line: the terminal headline metric only, no comparisons
  or causes.
- Analysis carries interpretation, diagnosis, comparisons via
  `[[run_id]]`, and next steps; that content never belongs in Result.
- Metadata captures every hyperparameter needed to reproduce the run as
  typed key/values, not embedded in prose fields.
- status is set explicitly to `finished` or `failed` when the run
  concludes; `running` is not a resting state for reported results.

Agents that cannot read repository files can get this same template from
`expnote guide agent --json` under the `run_record_template` key.

## Query Records

Do not grep generated Markdown for facts. Query SQLite through expnote:

```bash
expnote run show a7zf90k7 \
  --workspace example \
  --field purpose

expnote run show a7zf90k7 \
  --workspace example \
  --json

expnote run query \
  --workspace example \
  --status running \
  --where "metadata.seed = 1" \
  --order-by updated_at DESC \
  --json

expnote run status running \
  --workspace example \
  --json

expnote run update a7zf90k7 \
  --workspace example \
  --status finished \
  --json

expnote run update a7zf90k7 \
  --workspace example \
  --unset-meta seed \
  --json

expnote run update a7zf90k7 \
  --workspace example \
  --append-analysis "Reward plateaued after 300k steps." \
  --json

expnote run update a7zf90k7 \
  --workspace example \
  --metadata-json '{"algo":"sac","seed":1}' \
  --json
```

Use `--append-analysis` when adding observations to existing Analysis. It inserts
one blank line between old and new text. Use `--analysis` only when replacing the
whole Analysis field.

Use `--analysis-file <path>` / `--append-analysis-file <path>` to read long
analysis text from a file instead of an inline string, avoiding shell
substitution like `--analysis "$(cat notes.md)"`. Pass `-` to read from stdin,
e.g. `expnote run update <run_id> --append-analysis-file - < notes.md`.

When one run refers to another run, prefer `[[run_id]]` in SQL text fields. The
web UI also links bare active run ids when safe, but `[[run_id]]` is the least
ambiguous form for agents and remains clickable in the Web UI.

Use `run status <status> --json` for direct status lookup before handoff.
Use `run query --status <status> --where ... --json` when combining status with
metadata or topic filters.
`status` is manual. If external checks show a run has completed, update SQLite
with `run update <id> --status finished`.

Use `--metadata-json '{...}'` to merge a whole metadata object. Use
`--meta-json key=json` only for one typed key at a time.

## Cross-run Documents

Use `doc` commands when one analysis document compares or summarizes multiple
runs. SQLite stores the body and run links. Browse the document in the Web UI.

```bash
expnote doc add \
  --workspace example \
  --doc-id stackcube-seed-summary \
  --moc-id stackcube \
  --title "StackCube seed summary" \
  --run-id a7zf90k7 \
  --body "Initial cross-run comparison." \
  --json

expnote doc link stackcube-seed-summary other_wandb_id \
  --workspace example \
  --role ablation \
  --json

expnote doc update stackcube-seed-summary \
  --workspace example \
  --append-body "Seed 1 reached success earlier than the ablation." \
  --json

expnote doc show stackcube-seed-summary \
  --workspace example \
  --json
```

Use `--body-file <path>` / `--append-body-file <path>` to read a long document
body from a file instead of an inline string, or `-` for stdin, e.g.
`expnote doc update stackcube-seed-summary --append-body-file - < notes.md`.

Doc bodies can include Web-only chart placeholders:

```markdown
The main run plateaus after 300k steps.

{{ chart:eval_return }}

The ablation converges faster but reaches the same final success rate.
```

For agent-authored charts, place data and `charts.json` under
`<workspace-dir>/doc-assets/<doc_id>/` (usually `.expnote/doc-assets/<doc_id>/`).
Prefer declarative CSV/NPZ series charts. If the workspace already has a CSV
like:

```text
step,eval/return,eval/success_rate
1,10,0.1
2,20,0.2
3,30,0.3
```

then put it at:

```text
.expnote/doc-assets/stackcube-seed-summary/metrics.csv
```

and write:

```json
[
  {
    "id": "eval_return",
    "title": "Eval Return",
    "type": "series",
    "source": "metrics.csv",
    "x": "step",
    "y": ["eval/return"],
    "max_points": 2000
  }
]
```

Then add the chart placeholder to the doc body:

```bash
expnote doc update stackcube-seed-summary \
  --workspace example \
  --append-body "{{ chart:eval_return }}" \
  --json
```

The chart renders in `expnote web`.

Use `type: "python"` only for advanced charts. The script runs from the doc
asset directory in the Web UI and must write both `png` and `plotly` outputs.

## Benchmarks

Use a benchmark when you need a task-by-algorithm matrix. Record the runs in
SQLite first, then link a representative finished or failed run per cell.
Follow the [training and benchmarks tutorial](tutorials/experiments.rst) for
the command sequence.

## Handoff Checklist

Run these before handing off long-lived notes:

```bash
expnote validate \
  --workspace example \
  --json
```

See the [training record example](templates/training-record-example.md) for the
fields to capture.
