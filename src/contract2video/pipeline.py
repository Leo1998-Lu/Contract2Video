"""Budgeted, evidence-driven orchestration for contract-constrained video."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .interfaces import RenderedShot
from .repair import affected_shots
from .schema import normalize_contract
from .verification import aggregate_evidence, decisions_as_list, evidence_dict, validate_evidence


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path): return str(value)
    if is_dataclass(value): return _jsonable(asdict(value))
    if isinstance(value, dict): return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    return value


def _redact(value: Any) -> Any:
    secret_words = ("secret", "token", "password", "api_key", "apikey", "credential", "authorization")
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if any(word in str(key).lower() for word in secret_words): out[key] = "<redacted>"
            else: out[key] = _redact(val)
        return out
    if isinstance(value, list): return [_redact(v) for v in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(value), sort_keys=True) + "\n")


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return result[:80] or "shot"


def _relative_path(path: Path, root: Path) -> str:
    """Serialize run-local paths without leaking caller workspace locations."""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError as exc:
        raise ValueError("renderer output must remain inside the run output directory") from exc


def _rendered(value: Any, shot: dict[str, Any], target: Path, *, seed: int, attempt: int, cost: float) -> RenderedShot:
    if isinstance(value, RenderedShot): item = value
    elif isinstance(value, dict):
        item = RenderedShot(
            shot_id=str(value.get("shot_id", shot["id"])), path=Path(value.get("path", target)),
            duration=float(value.get("duration", shot["duration"])), cost=float(value.get("cost", 0.0)),
            seed=int(value.get("seed", seed)), attempt=int(value.get("attempt", attempt)), metadata=dict(value.get("metadata", {})),
        )
    else:
        item = RenderedShot(shot_id=shot["id"], path=target, duration=float(shot["duration"]), seed=seed, attempt=attempt)
    if item.shot_id != shot["id"]: raise ValueError("renderer returned a different shot_id")
    if not isinstance(item.path, Path): item.path = Path(item.path)
    if not item.path.is_file() and target.is_file(): item.path = target
    if not item.path.is_file(): raise FileNotFoundError(f"renderer did not produce a readable asset for {shot['id']}")
    if not math.isfinite(float(item.duration)) or float(item.duration) <= 0:
        raise ValueError("renderer returned a non-positive or non-finite duration")
    if not math.isfinite(float(item.cost)) or float(item.cost) < 0:
        raise ValueError("renderer returned an invalid cost")
    return item


def _estimate(renderer: Any, contract: dict[str, Any], shot: dict[str, Any], policy: dict[str, Any]) -> float:
    estimate = None
    method = getattr(renderer, "estimate_cost", None)
    if callable(method):
        try: estimate = method(contract, shot)
        except Exception: estimate = None
    if estimate is None: estimate = shot.get("estimated_cost", policy.get("estimated_cost", policy.get("default_render_cost", 0.0)))
    try: estimate = float(estimate)
    except (TypeError, ValueError): estimate = 0.0
    return max(0.0, estimate) if math.isfinite(estimate) else float("inf")


def _load_calibration(path: Any) -> dict[str, Any] | None:
    if not path: return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValueError("unsupported calibration schema")
    out = dict(data)
    nested = data.get("thresholds")
    if isinstance(nested, dict):
        for key in ("support", "contradict"):
            if key in nested and key + "_threshold" not in out: out[key + "_threshold"] = nested[key]
    for key in ("support_threshold", "contradict_threshold"):
        if key in out and (not isinstance(out[key], (int, float)) or not 0 <= float(out[key]) <= 1):
            raise ValueError(f"invalid {key} in calibration")
    return out


def compile_contract(prompt: str, planner: Any) -> dict[str, Any]:
    """Compile a prompt through a planner and validate its returned contract."""
    if not isinstance(prompt, str) or not prompt.strip(): raise ValueError("prompt must be non-empty")
    result = planner.compile(prompt)
    return normalize_contract(result)


def run_pipeline(
    contract: dict[str, Any],
    config: dict[str, Any],
    output_dir: str | Path,
    *,
    renderer: Any = None,
    observer: Any = None,
    planner: Any = None,
) -> dict[str, Any]:
    """Render, observe, and locally repair a contract under explicit budgets."""
    started = time.monotonic()
    contract = normalize_contract(contract)
    if not isinstance(config, dict): raise ValueError("config must be an object")
    out = Path(output_dir)
    if out.exists() and not out.is_dir(): raise FileExistsError(f"output path is not a directory: {out}")
    if out.exists() and any(out.iterdir()): raise FileExistsError(f"output directory is non-empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    policy = dict(config.get("policy", {}))
    strategy = policy.get("strategy", "local")
    if strategy not in {"local", "full", "single"}: raise ValueError("policy.strategy must be local, full, or single")
    max_rounds = max(1, int(policy.get("max_rounds", 2)))
    max_calls = max(0, int(policy.get("max_render_calls", len(contract["shots"]) * max_rounds)))
    max_cost = float(policy.get("max_cost", float("inf")))
    if max_cost < 0 or math.isnan(max_cost): raise ValueError("max_cost must be non-negative or infinity")
    require_certified = bool(policy.get("require_certified", policy.get("require_certified_thresholds", False)))
    calibration = _load_calibration(policy.get("calibration_file"))
    support_threshold = float(policy.get("support_threshold", 0.5))
    contradict_threshold = float(policy.get("contradict_threshold", 0.5))
    if not 0 <= support_threshold <= 1 or not 0 <= contradict_threshold <= 1: raise ValueError("evidence thresholds must be in [0,1]")

    if renderer is None or observer is None:
        from . import adapters  # imported lazily to keep import side effects-free
        if renderer is None: renderer = adapters.make_renderer(dict(config.get("renderer", {})))
        if observer is None: observer = adapters.make_observer(dict(config.get("observer", {})))
    shots = [s for s in contract["shots"] if isinstance(s, dict)]
    shot_index = {s["id"]: i for i, s in enumerate(shots)}
    assets: dict[str, RenderedShot] = {}
    attempts: dict[str, int] = {s["id"]: 0 for s in shots}
    errors: list[dict[str, Any]] = []
    trace_path, evidence_path = out / "trace.jsonl", out / "evidence.jsonl"
    render_calls = 0; total_cost = 0.0; rounds = 0; budget_hit = False
    aggregate: dict[str, dict[str, Any]] = {}
    evidence_rows: list[dict[str, Any]] = []

    def trace(event: dict[str, Any]) -> None:
        event = {"time": time.time(), **event}; _append_jsonl(trace_path, event)

    def render_wave(target_ids: list[str], wave: int) -> bool:
        nonlocal render_calls, total_cost, budget_hit
        all_ok = True
        for sid in target_ids:
            shot = next(s for s in shots if s["id"] == sid)
            if render_calls >= max_calls:
                budget_hit = True; all_ok = False; trace({"event": "budget", "reason": "max_render_calls", "shot_id": sid}); break
            estimate = _estimate(renderer, contract, shot, policy)
            if math.isfinite(max_cost) and total_cost + estimate > max_cost + 1e-12:
                budget_hit = True; all_ok = False; trace({"event": "budget", "reason": "max_cost", "shot_id": sid, "estimated_cost": estimate}); break
            attempts[sid] += 1; attempt = attempts[sid]; render_calls += 1
            target = out / "shots" / f"{shot_index[sid]:03d}_{_slug(sid)}" / f"attempt-{attempt:03d}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            refs = [assets[d].path for d in shot.get("dependencies", []) if d in assets]
            feedback = [e for e in evidence_rows if sid in e.get("shot_ids", []) and e.get("verdict") != "support"]
            trace({"event": "render_start", "shot_id": sid, "attempt": attempt, "round": wave, "estimated_cost": estimate})
            try:
                value = renderer.render(contract, shot, target, seed=int(policy.get("seed", 0)) + attempts[sid] - 1, attempt=attempt, feedback=feedback, references=refs)
                item = _rendered(value, shot, target, seed=int(policy.get("seed", 0)) + attempts[sid] - 1, attempt=attempt, cost=estimate)
                actual = float(getattr(item, "cost", 0.0) or 0.0)
                total_cost += max(estimate, actual, 0.0)
                assets[sid] = item
                trace({"event": "render_end", "shot_id": sid, "attempt": attempt, "path": _relative_path(item.path, out), "cost": actual})
            except Exception as exc:
                total_cost += estimate
                all_ok = False
                errors.append({"stage": "render", "shot_id": sid, "attempt": attempt, "error": f"{type(exc).__name__}: {exc}", "path": _relative_path(target, out)})
                trace({"event": "render_error", "shot_id": sid, "attempt": attempt, "error": str(exc), "path": _relative_path(target, out)})
        return all_ok

    def observe_wave(wave: int) -> bool:
        nonlocal aggregate
        try:
            raw = observer.observe(contract, assets)
            if raw is None: raw = []
            raw = list(raw)
            normalized_raw = []
            normalization_issues = []
            for item in raw:
                row, validation_errors = validate_evidence(item, contract, assets)
                if validation_errors:
                    normalization_issues.append({"evidence": row, "errors": validation_errors})
                row["round"] = wave
                evidence_rows.append(row); _append_jsonl(evidence_path, row)
                normalized_raw.append(row)
            aggregate, invalid = aggregate_evidence(contract, normalized_raw, assets, support_threshold=support_threshold, contradict_threshold=contradict_threshold, calibration=calibration, require_certified=require_certified)
            invalid = normalization_issues + invalid
            for issue in invalid: errors.append({"stage": "evidence", **issue})
            trace({"event": "observe", "round": wave, "evidence_count": len(raw), "invalid_count": len(invalid), "decisions": decisions_as_list(aggregate)})
            return True
        except Exception as exc:
            errors.append({"stage": "observe", "error": f"{type(exc).__name__}: {exc}"})
            trace({"event": "observe_error", "round": wave, "error": str(exc)})
            return False

    # Initial wave and at most max_rounds-1 repair waves.
    target_ids = [s["id"] for s in shots]
    observer_ok = True
    for wave in range(1, max_rounds + 1):
        rounds = wave
        render_wave(target_ids, wave)
        observer_ok = observe_wave(wave)
        hard_unresolved = [cid for cid, d in aggregate.items() if d.get("hardness") == "hard" and d.get("status") != "supported"]
        if observer_ok and not hard_unresolved and len(assets) == len(shots): break
        if wave >= max_rounds or not observer_ok: break
        if strategy == "full": target_ids = [s["id"] for s in shots]
        else:
            target_ids = affected_shots(contract, hard_unresolved)
            if strategy == "single":
                target_ids = sorted(target_ids, key=lambda sid: (attempts.get(sid, 0), shot_index.get(sid, 10**9)))[:1]
        if not target_ids: break
        # A zero-cost or exactly budget-consuming render is still allowed to
        # finish its verification wave.  The render loop marks ``budget_hit``
        # only when a pre-call budget check actually blocks a call.
        if render_calls >= max_calls: budget_hit = True; break

    hard = [d for d in aggregate.values() if d.get("hardness") == "hard"]
    accepted = bool(observer_ok and len(assets) == len(shots) and all(d.get("status") == "supported" for d in hard))
    if accepted: status = "accepted"
    elif not observer_ok or any(error.get("stage") == "render" for error in errors): status = "failed"
    elif budget_hit: status = "budget_exhausted"
    else: status = "unresolved"

    final_path = out / "final.mp4"
    if len(assets) == len(shots):
        try:
            from .media import compose_videos
            compose_videos([assets[s["id"]].path for s in shots], final_path)
            trace({"event": "compose", "path": _relative_path(final_path, out)})
        except Exception as exc:
            errors.append({"stage": "compose", "error": f"{type(exc).__name__}: {exc}"})
            trace({"event": "compose_error", "error": str(exc)})
            if status == "accepted": status = "failed"

    _write_json(out / "contract.json", contract)
    _write_json(out / "config.json", _redact(config))
    assets_out = []
    for sid in sorted(assets, key=lambda x: shot_index.get(x, 10**9)):
        item = assets[sid]
        assets_out.append({"shot_id": sid, "path": _relative_path(item.path, out), "duration": item.duration, "cost": item.cost, "seed": item.seed, "attempt": item.attempt, "metadata": item.metadata})
    hard_total = len(hard); hard_supported = sum(d.get("status") == "supported" for d in hard)
    unknown_total = sum(d.get("status") == "unknown" for d in aggregate.values())
    manifest = {
        "schema_version": "1.0", "contract_id": contract["contract_id"], "status": status, "strategy": strategy,
        "render_calls": render_calls, "total_cost": total_cost, "rounds": rounds,
        "hard_pass_rate": hard_supported / hard_total if hard_total else 1.0,
        "hard_total": hard_total, "unknown_rate": unknown_total / len(aggregate) if aggregate else 0.0,
        "decisions": decisions_as_list(aggregate), "assets": assets_out, "errors": errors,
        "wall_seconds": time.monotonic() - started,
        "evidence_mode": (lambda mode: mode if mode in {"scripted", "vlm"} else ("vlm" if observer.__class__.__name__.lower().startswith("vlm") else "scripted"))(str(getattr(observer, "mode", config.get("observer", {}).get("type", "scripted")))),
        "final_path": _relative_path(final_path, out) if final_path.is_file() else None,
    }
    _write_json(out / "manifest.json", manifest)
    return manifest
