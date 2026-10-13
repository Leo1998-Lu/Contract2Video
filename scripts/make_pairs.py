#!/usr/bin/env python3
"""Create blinded human-evaluation pairs from two manifest collections."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract2video.evaluation import case_id, export_human_pairs, load_manifests, method_name  # noqa: E402


def _clip(record: dict) -> str | None:
    assets = record.get("assets", {})
    values = assets.values() if isinstance(assets, dict) else assets if isinstance(assets, list) else []
    for asset in values:
        path = asset.get("path") if isinstance(asset, dict) else asset
        if path:
            return str(path)
    for key in ("final_path", "final", "video"):
        if record.get(key):
            return str(record[key])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, help="First method manifest JSON/JSONL or run directory")
    parser.add_argument("--right", required=True, help="Second method manifest JSON/JSONL or run directory")
    parser.add_argument("--output-dir", "-o", required=True)
    parser.add_argument("--left-method", default="left")
    parser.add_argument("--right-method", default="right")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    left = {}
    for index, row in enumerate(load_manifests(args.left)):
        cid = case_id(row, fallback=f"case-{index:06d}")
        clip = _clip(row)
        if clip:
            left[cid] = (clip, row)
    right = {}
    for index, row in enumerate(load_manifests(args.right)):
        cid = case_id(row, fallback=f"case-{index:06d}")
        clip = _clip(row)
        if clip:
            right[cid] = (clip, row)
    shared = sorted(set(left).intersection(right))
    if not shared:
        parser.error("No shared cases with clips")
    pairs = []
    for cid in shared:
        pairs.append({
            "case_id": cid,
            "left_path": left[cid][0],
            "right_path": right[cid][0],
            "left_method": args.left_method,
            "right_method": args.right_method,
            "prompt": left[cid][1].get("prompt", left[cid][1].get("metadata", {}).get("prompt")),
        })
    result = export_human_pairs(pairs, args.output_dir, seed=args.seed)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
