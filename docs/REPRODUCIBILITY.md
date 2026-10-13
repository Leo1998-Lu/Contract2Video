# Reproducibility

This repository provides the implementation boundary and reference fixtures. It does not include benchmark datasets, model weights, generated benchmark videos, external service credentials, or reported results. A result table must be produced from an independent evaluation package and attached separately to a submission or release.

## Environment

Use Python 3.10 or newer and install the pinned project dependencies from `pyproject.toml` in a clean virtual environment. Record the Python version, operating system, accelerator model, driver/runtime versions, package lock, and the exact Git revision for every run. The reference procedural renderer is intended to make interface-level checks deterministic; it is not a substitute for a learned video generator.

## Inputs

For each case, preserve:

- the original prompt and language;
- the compiled contract JSON;
- the complete adapter configuration with secrets redacted;
- renderer, planner, and observer identifiers;
- seeds, attempt numbers, frame rate, resolution, and duration;
- calibration file checksum when a calibrated observer is used.

Use relative paths inside the repository or a documented external data root. Do not put private workspace paths or credentials in contracts, logs, fixtures, or paper material.

## Connecting a learned renderer

The `Renderer` protocol accepts a contract, one shot, target path, seed, attempt, repair feedback, and reference paths. To connect a local or service-backed generator:

1. implement the protocol in an adapter module;
2. register a `type` in `make_renderer`;
3. estimate cost before the call and return the measured cost after it;
4. write failed responses and diagnostics to the run trace while preserving any partial artifact;
5. keep authentication in the environment or a secret manager;
6. pin model and sampler versions in the run manifest.

The Wan configuration is an example boundary with placeholder model identifiers. Replace them locally and keep the resulting checkpoints outside the anonymous repository.

## Connecting a real observer

Implement `Observer.observe(contract, assets)` and emit one evidence record per checked constraint. The observer must provide timestamp spans in seconds and SHA-256 hashes for all referenced clips. If a signal cannot check a required condition, emit `unknown`; do not infer support from the contract or renderer metadata. For video-language, audio, OCR, and tracking observers, record model versions and preprocessing settings.

## Evaluation

Run each prompt or case independently and aggregate by case, not by frame, seed, or candidate. Report paired bootstrap confidence intervals and fixed-seed randomization tests when comparing systems. Keep human-evaluation pairing keys separate from anonymized labels and preserve the annotation protocol, inclusion criteria, and agreement statistic. Export clip manifests for external video benchmarks; do not replace an external benchmark with fabricated scores.

No significance claim is valid for the included fixtures. Fixture outputs test interfaces and control flow only.

## Reproduction checklist

Before publishing an evaluation package, verify:

- all source and configuration files are present at the recorded revision;
- all inputs and preprocessing scripts have checksums;
- no credentials, private URLs, or identifying metadata are included;
- all failed render calls and unresolved decisions are retained;
- the evidence mode is recorded and fixture runs are excluded from research tables;
- a clean environment can validate contracts and replay the documented commands.
