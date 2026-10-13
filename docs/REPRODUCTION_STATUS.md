# Reproduction status

This release separates what can be executed immediately from what requires external model and data access.

## Runnable in a clean Python environment

```bash
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v
PYTHONPATH=src python -m contract2video validate --contract examples/two_shot_demo.json
PYTHONPATH=src python -m contract2video compile --prompt-file examples/demo_prompt.txt --config configs/reference.json --output /tmp/contract.json
PYTHONPATH=src python -m contract2video run --contract examples/two_shot_demo.json --config configs/reference.json --output /tmp/contract2video-run
```

These commands exercise schema validation, deterministic contract compilation, the procedural renderer, scripted evidence handling, budgets, dependency-aware repair, manifests, and case-level aggregation. The fixture observer is a control-flow fixture and is not a perceptual video-quality evaluator.

## Requires external assets

The Wan and Hunyuan configurations are adapter boundaries. Their model weights, benchmark videos, learned observers, provider credentials, and licensed evaluation data are intentionally excluded. To reproduce the paper's empirical tables, add those assets under a documented external data root, pin model and sampler revisions, connect a real renderer and observer, and produce independent per-case manifests.

## What the paper numbers mean

The LaTeX source contains a retained numerical profile from the supplied draft. It is labeled as pending reconciliation because the current release does not contain the raw case manifests, original human labels, or provider invoices needed to verify those summaries. Fixture runs must not be copied into the paper tables.
