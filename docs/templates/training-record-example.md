# Training record example

This example records a StackCube training run, updates it after evaluation,
and links it to an analysis document. The metrics are illustrative.

## Record a run

```bash
expnote init --workspace robot-learning
expnote moc add --workspace robot-learning \
  --moc-id robot-learning --title "Robot Learning Training"
expnote topic add "StackCube SAC" --workspace robot-learning --moc-id robot-learning

expnote run add --workspace robot-learning \
  --moc-id robot-learning --topic "StackCube SAC" --run-id stack000 \
  --purpose "Train SAC on StackCube with seed 1" \
  --relation "Baseline for later ablations" \
  --status running --meta algo=sac --meta env_id=StackCube-v1 --meta-json seed=1

expnote run update stack000 --workspace robot-learning --status finished \
  --result "82% success over 100 evaluation episodes" \
  --append-analysis "Success rate plateaued around 300k steps."
```

The run ID is stable across updates. Purpose records the question or objective,
Relation connects the run to other work, and Metadata stores filterable
parameters. Result carries a headline metric with its evaluation scope.
Use Analysis for observations as the run progresses.

## Link a cross-run analysis

```bash
expnote doc add --workspace robot-learning --moc-id robot-learning \
  --doc-id stackcube-seed-summary --title "StackCube seed summary" \
  --run-id stack000 --body "Baseline observation from [[stack000]]."
expnote run show stack000 --workspace robot-learning --json
expnote web --workspace robot-learning --no-open
```

Open the localhost address printed by the CLI. The **Robot Learning Training**
MOC shows the topic and the analysis document. Add more runs to the document
with `expnote doc link` as their results arrive.

See the [training and benchmarks tutorial](../tutorials/experiments.rst) for
a complete matrix example.
