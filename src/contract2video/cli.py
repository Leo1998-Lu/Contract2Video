"""Command-line entry points for the anonymous reference implementation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .calibration import calibrate_thresholds
from .schema import validate_contract


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {source}")
    return value


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cmd_validate(args: argparse.Namespace) -> int:
    contract = _read_json(args.contract)
    errors = validate_contract(contract, raise_on_error=False)
    if errors:
        for error in errors:
            print(error)
        return 2
    print("valid")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    from .adapters import make_planner

    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    config = _read_json(args.config)
    planner = make_planner(config.get("planner", config))
    contract = planner.compile(prompt)
    validate_contract(contract)
    _write_json(args.output, contract)
    print(args.output)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run_pipeline

    contract = _read_json(args.contract)
    config = _read_json(args.config)
    validate_contract(contract)
    result = run_pipeline(contract, config, Path(args.output))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"accepted", "unresolved", "budget_exhausted"} else 1


def _cmd_calibrate(args: argparse.Namespace) -> int:
    result = calibrate_thresholds(
        args.selection, args.certification, args.output,
        alpha=float(args.alpha), target_fpr=float(args.target_fpr),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("certified") else 2


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluation import aggregate_case_manifests

    result = aggregate_case_manifests(args.manifests, method=args.method)
    if args.output:
        _write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contract2video")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a production contract")
    p.add_argument("--contract", required=True)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("compile", help="compile a prompt into a production contract")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_compile)

    p = sub.add_parser("run", help="run the bounded render-observe-repair loop")
    p.add_argument("--contract", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("calibrate", help="select and certify verifier thresholds")
    p.add_argument("--selection", required=True)
    p.add_argument("--certification", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--alpha", default=0.05, type=float)
    p.add_argument("--target-fpr", default=0.05, type=float)
    p.set_defaults(func=_cmd_calibrate)

    p = sub.add_parser("evaluate", help="aggregate case-level manifests")
    p.add_argument("--manifests", nargs="+", required=True)
    p.add_argument("--method")
    p.add_argument("--output")
    p.set_defaults(func=_cmd_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
