#!/usr/bin/env python3
"""Aggregate Contract2Video manifests at case level."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract2video.evaluation import (  # noqa: E402
    aggregate_case_manifests,
    export_external_manifest,
    load_manifests,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", nargs="+", required=True, help="Manifest JSON/JSONL files or run directories")
    parser.add_argument("--output", "-o", required=True, help="Summary JSON path")
    parser.add_argument("--method", default=None, help="Override method label for all input manifests")
    parser.add_argument("--external-benchmark", choices=["vbench", "t2v-compbench", "videoscore"], default=None)
    parser.add_argument("--external-output", default=None, help="JSONL path for external benchmark handoff")
    args = parser.parse_args(argv)

    records = []
    for source in args.input:
        records.extend(load_manifests(source))
    summary = aggregate_case_manifests(records, method=args.method)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.external_benchmark:
        if not args.external_output:
            parser.error("--external-output is required with --external-benchmark")
        export_external_manifest(records, args.external_output, benchmark=args.external_benchmark)
    print(json.dumps({"output": str(output), "n_cases": summary["n_cases"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
