# Clustering Eval — YYYY-MM-DD

After the first `/buildstories` over real pending data, sample and rate.

## Method

1. Open the *Stories* tab.
2. Pick 20 stories at random (use a simple `rand()` sort or eyeball it).
3. For each, fill the table below.

## Rubric

| Story ID | Type correct? (Y/N/borderline) | Summary accurate? (full/partial/wrong) | Highlights grounded? (all real / 1 hallucinated / multi hallucinated) | Notes |
|---|---|---|---|---|
| 1 |  |  |  |  |
| 2 |  |  |  |  |
| ... |  |  |  |  |
| 20 |  |  |  |  |

## Decision rule

- ≥ 3/20 stories with hallucinated highlights → revise the `cluster_into_story` prompt before running again.
- ≥ 4/20 mistyped → revise seeded type list or prompt examples.
- Otherwise → ship. Re-run eval after any prompt change.

## Iterations

| Date | Sample size | Hallucinated highlights | Mistyped | Action taken |
|---|---|---|---|---|
|  |  |  |  |  |
