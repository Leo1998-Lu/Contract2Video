# Evaluation protocol

Evaluation uses one prompt or contract as one independent case. Render seeds,
attempts, and frame samples are within-case replicates; they are averaged before
confidence intervals or paired tests are computed. This prevents a case with
many frames from receiving more statistical weight than another case.

## Aggregate pipeline manifests

The evaluator accepts a manifest JSON file, a JSONL file, a run directory, or a
list of those inputs:

```bash
python scripts/evaluate.py \
  --input results/method_a results/method_b \
  --output results/summary.json
```

Every manifest is grouped by `case_id`, then `prompt_id`, then `contract_id`,
and finally by an explicit prompt. The method label is read from `method`,
`model`, or `system`; pass `--method` when a collection contains no label.
For each independent case the evaluator reports:

- `hard_pass_rate`: fraction of hard constraints supported;
- `unknown_rate`: fraction of hard constraints without sufficient evidence;
- `violation_rate`: fraction of hard constraints contradicted;
- `cost`: mean cost per within-case run and `cost_total` across runs;
- `render_calls`, `accepted_rate`, and the original run statuses.

The top-level summary contains the mean of the case-level rates, total cost,
cost per case, and the complete case table. An unknown constraint never counts
as a pass. Contradictions are counted as violations when the pipeline has
already applied its conservative evidence aggregation.

## Paired statistics

Use `scripts/stats.py` for two method summaries or raw manifest collections:

```bash
python scripts/stats.py \
  --left results/method_a/summary.json \
  --right results/method_b/summary.json \
  --metric hard_pass_rate --resamples 10000 --seed 0 \
  --output results/paired_hard_pass.json
```

The implementation aligns shared case identifiers, computes the paired mean
difference (left minus right), and uses:

1. a percentile bootstrap interval over resampled cases; and
2. a paired sign-flip randomization test.

Both procedures use an explicit seed, which is recorded in the output. The
result is descriptive evidence for the supplied cases; do not call a fixture or
development-only collection statistically significant.

## Human pair export

Create randomized, anonymized A/B labels from two collections:

```bash
python scripts/make_pairs.py \
  --left results/method_a \
  --right results/method_b \
  --left-method system-a --right-method system-b \
  --output-dir results/human_pairs --seed 13
```

The output has a public `pairs.jsonl` and a separate `key.json`. The public
file contains only pair identifiers, randomized labels A/B, clip paths under
the generated `clips/` directory, and an optional prompt. Method names, source
paths, and the A/B mapping appear only in the key. Keep the key inaccessible to
raters until labels have been collected. The exporter refuses to overwrite a
non-empty output directory.

Human labels are JSON or JSONL objects with `pair_id`, `label` (`A`, `B`,
`tie`, or `invalid`), and an optional `annotator_id`. Validate them before
analysis:

```python
from contract2video.evaluation import validate_human_labels
report = validate_human_labels("labels.jsonl", "results/human_pairs/pairs.jsonl")
if not report["valid"]:
    raise ValueError(report["errors"])
```

Validation checks pair identifiers, allowed labels, duplicate labels per
annotator and pair, and missing labels when `require_all=True`.

## External benchmark handoff

The evaluator does not estimate perceptual benchmark scores. Export clip paths
and prompts for the installed external package with:

```bash
python scripts/evaluate.py \
  --input results/method_a \
  --output results/method_a/summary.json \
  --external-benchmark vbench \
  --external-output results/method_a/vbench_manifest.jsonl
```

`--external-benchmark` accepts `vbench`, `t2v-compbench`, or `videoscore`.
Each JSONL row is marked `pending_external_evaluation` and has `scores: null`.
Run the selected benchmark's documented evaluator separately, preserve its
version and configuration, then import measured values as a new artifact. Do
not fill the exported `scores` field with placeholders or values from another
metric.
