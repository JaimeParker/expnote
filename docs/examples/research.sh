# start project
demo_dir=$(mktemp -d)
export EXPNOTE_CONFIG_HOME="$demo_dir/config"
export EXPNOTE_DATA_HOME="$demo_dir/data"

vault="$demo_dir/research-vault"
project_dir="$vault/Research/warmup"
images="$vault/Attachments/warmup"
mkdir -p "$vault/.obsidian" "$project_dir" "$images"

expnote init --workspace warmup-runs
expnote project init --id warmup --name "Warmup study" \
  --project-dir "$project_dir" --images-dir "$images" \
  --profile-note "$project_dir/expnote-profile.md" --workspace warmup-runs --apply
expnote check
# end project

# start plan
# Exit status 4 indicates a pending write.
expnote new plan --id PLAN-001 --title "Warmup study" || test "$?" -eq 4
expnote new plan --id PLAN-001 --title "Warmup study" --apply

# Keep the generated frontmatter and replace the empty section headings.
plan="$project_dir/plans/PLAN-001--Warmup study.md"
awk '1; /^---$/ && ++delimiters == 2 { exit }' "$plan" > "$demo_dir/frontmatter"
{
  cat "$demo_dir/frontmatter"
  cat <<'NOTE'
# PLAN-001: Warmup study

## 1. Question

Does linear warmup reduce early loss spikes?

## 2. Scope and non-goals

One demonstration with two synthetic loss traces.

## 3. What to test

Compare an arm with no warmup and an arm with linear warmup.

## 4. How to read the results

Compare peak loss over the first five steps. The arm with the lower peak
wins this demonstration. Equal peaks count as a tie.
NOTE
} > "$plan"

expnote decision new --title "Approve the warmup study" --layer research \
  --decided "Compare peak loss across the two arms." \
  --reason "The question has a defined comparison and a fixed readout." \
  --options "A schedule sweep would answer a broader question." \
  --plans PLAN-001 --apply
expnote next --owner user
# end plan

# start execution
expnote plan advance PLAN-001 --to approved --decision WARMUP-DEC-0001 --apply
expnote new exec --plan PLAN-001 --stage S1 --title "Compare traces" --apply

execution="$project_dir/ops/exec--PLAN-001 Compare traces.md"
awk '1; /^---$/ && ++delimiters == 2 { exit }' "$execution" > "$demo_dir/frontmatter"
{
  cat "$demo_dir/frontmatter"
  cat <<'NOTE'
# Execution plan: Compare traces

## 1. Stages

Compare two five-step synthetic loss traces.

## 2. Materials and commands

Generate the fixed traces with Python and plot both on the same axes.

## Rules summary (needs confirmation)

Use the peak over steps 1–5 for each arm. The arm with the lower peak wins.
Equal peaks count as a tie. This restates the approved plan.

## 3. Risks and resources

Synthetic loss values demonstrate the workflow. A real study would
use a training protocol, recorded seeds, and measured loss values.
NOTE
} > "$execution"

expnote exec freeze PLAN-001-S1 --self-approve --no-new-rules --no-quiet --apply
expnote plan advance PLAN-001 --to running --no-quiet --apply
expnote exec advance PLAN-001-S1 --to running --no-quiet --apply
# end execution

# start runs
expnote moc add --workspace warmup-runs --moc-id warmup --title "Warmup study"
expnote topic add "Peak loss" --workspace warmup-runs --moc-id warmup

expnote run add --workspace warmup-runs --moc-id warmup --topic "Peak loss" \
  --run-id warm0001 --purpose "Demonstrate the no-warmup arm" --status finished \
  --result "Peak synthetic loss 1.20 over steps 1–5" --meta arm=none
expnote run add --workspace warmup-runs --moc-id warmup --topic "Peak loss" \
  --run-id warm0002 --purpose "Demonstrate the linear-warmup arm" --status finished \
  --result "Peak synthetic loss 0.67 over steps 1–5" --meta arm=linear

MPLBACKEND=Agg python - "$images/warmup-loss.png" <<'PY'
import sys

import matplotlib.pyplot as plt

steps = range(1, 6)
plt.plot(steps, [0.8, 1.2, 0.6, 0.4, 0.25], marker="o", label="No warmup")
plt.plot(steps, [0.67, 0.58, 0.49, 0.36, 0.25], marker="o", label="Linear warmup")
plt.title("Synthetic training traces")
plt.xticks(steps)
plt.xlabel("Step")
plt.ylabel("Loss")
plt.legend()
plt.tight_layout()
plt.savefig(sys.argv[1])
PY
# end runs

# start evidence
expnote new ev --plan PLAN-001 --exec PLAN-001-S1 --title "Peak loss" --apply
evidence="$project_dir/evidence/EV--PLAN-001 Peak loss.md"
appendix="$project_dir/ops/appendix--EV--PLAN-001 Peak loss.md"
today=$(date +%F)

cat > "$evidence" <<NOTE
---
schema: 3
type: evidence
created: "$today"
plan: "[[PLAN-001--Warmup study]]"
exec_plan: "[[exec--PLAN-001 Compare traces]]"
question: "Does linear warmup reduce early loss spikes?"
status: rendered
figures: ["warmup-loss.png"]
runs: ["warm0001", "warm0002"]
appendix: "[[appendix--EV--PLAN-001 Peak loss]]"
---
# Peak loss

Run identity and provenance: [[appendix--EV--PLAN-001 Peak loss]].

## Figures

![[warmup-loss.png]]

## Endpoint table

| Arm | Peak synthetic loss over steps 1–5 |
|---|---:|
| No warmup | 1.20 |
| Linear warmup | 0.67 |

## Figure labels

| Arm | Value |
|---|---|
| No warmup | |
| Linear warmup | |

## Observation

Both traces end at 0.25. The pre-registered comparison uses their peaks.
NOTE

cat > "$appendix" <<NOTE
---
schema: 3
type: evidence-appendix
created: "$today"
parent: "[[EV--PLAN-001 Peak loss]]"
plan: "[[PLAN-001--Warmup study]]"
runs: ["warm0001", "warm0002"]
---
# Peak loss appendix

Evidence page: [[EV--PLAN-001 Peak loss]].

## Run identity

warm0001 is the no-warmup arm. warm0002 is the linear-warmup arm.

## Tables

| Arm | Peak synthetic loss over steps 1–5 |
|---|---:|
| No warmup | 1.20 |
| Linear warmup | 0.67 |

| Arm | Synthetic losses at steps 1–5 |
|---|---|
| No warmup | 0.80, 1.20, 0.60, 0.40, 0.25 |
| Linear warmup | 0.67, 0.58, 0.49, 0.36, 0.25 |

## Checks

Both traces have five steps. The table and figure use the same values.
NOTE

expnote next --owner user
# end evidence

# start result
expnote new res --plan PLAN-001 --title "Warmup readout" --apply
result="$project_dir/results/RES--PLAN-001 Warmup readout.md"

cat > "$result" <<NOTE
---
schema: 3
type: result
id: "RES-PLAN-001"
plan: "[[PLAN-001--Warmup study]]"
status: draft
evidence: ["[[EV--PLAN-001 Peak loss]]"]
decisions: []
reviewed_by: ""
created: "$today"
---
# Warmup readout

## 1. Question and summary

Does linear warmup reduce early loss spikes?

## 2. Pre-registered verdicts

The linear-warmup arm has the lower peak in [[EV--PLAN-001 Peak loss]]
and wins by the rule in [[exec--PLAN-001 Compare traces]].

## 3. Exploratory analysis

Both traces end at the same value.

## 4. Scope and limits

The verdict covers these two synthetic traces.

## 5. Deviations

The demonstration followed the frozen comparison.
NOTE

expnote res submit RES-PLAN-001 --deviation-impact none --no-quiet --apply
expnote next --owner user
# end result

# start close
expnote plan advance PLAN-001 --to confirmed --apply
expnote check
# end close
