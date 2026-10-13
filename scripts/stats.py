#!/usr/bin/env python3
"""Run paired confidence intervals and randomization tests on case summaries."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract2video.evaluation import aggregate_case_manifests, load_manifests, paired_statistics  # noqa: E402


def _cases(source: str):
    loaded = load_manifests(source)
    # Summary JSONs already contain one row per case.  Raw manifests are
    # collapsed here so seeds and render attempts do not become observations.
    return aggregate_case_manifests(loaded)["cases"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--metric", default="hard_pass_rate", choices=["hard_pass_rate", "unknown_rate", "violation_rate", "accepted_rate", "cost", "render_calls"])
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", "-o", required=True)
    args = parser.parse_args(argv)
    result = paired_statistics(_cases(args.left), _cases(args.right), metric=args.metric, n_resamples=args.resamples, seed=args.seed)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "n_cases": result["n_cases"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
