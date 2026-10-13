"""Finite-sample confidence calibration for verifier scores.

Selection and certification records are intentionally separate.  Thresholds are
chosen only from the selection split; the certification split is used once to
report a conservative Hoeffding upper bound on the negative acceptance rate.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CALIBRATION_SCHEMA = "1.0"


def _rows(source: str | Path | Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(source, (str, Path)):
        result = []
        with Path(source).open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line: continue
                try: row = json.loads(line)
                except json.JSONDecodeError as exc: raise ValueError(f"invalid JSONL at line {line_no}: {exc}") from exc
                result.append(row)
        return result
    return list(source)


def _validate_row(row: Any, i: int) -> tuple[str, float, int]:
    if not isinstance(row, dict): raise ValueError(f"calibration row {i} must be an object")
    ident = row.get("id", row.get("case_id"))
    if not isinstance(ident, str) or not ident: raise ValueError(f"calibration row {i}: missing id")
    kind = row.get("kind")
    if not isinstance(kind, str) or not kind: raise ValueError(f"calibration row {i}: missing kind")
    score = row.get("score", row.get("confidence"))
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not 0 <= float(score) <= 1: raise ValueError(f"calibration row {i}: score must be in [0,1]")
    label = row.get("label")
    if isinstance(label, bool): raise ValueError(f"calibration row {i}: label must be numeric 0 or 1")
    elif isinstance(label, int) and label in (0, 1): label_i = label
    elif isinstance(label, float) and label in (0.0, 1.0): label_i = int(label)
    else: raise ValueError(f"calibration row {i}: label must be 0 or 1")
    return kind, float(score), label_i


def _select_threshold(rows: list[tuple[str, float, int]], target_fpr: float) -> tuple[float, dict[str, float]]:
    if not rows: return 1.0, {"selection_tpr": 0.0, "selection_fpr": 0.0}
    candidates = sorted({score for _, score, _ in rows}, reverse=True)
    # Include a value that accepts no score while staying in the documented range.
    if 1.0 not in candidates: candidates.append(1.0)
    best = None
    for threshold in candidates:
        positives = sum(label for _, score, label in rows)
        tp = sum(1 for _, score, label in rows if label == 1 and score >= threshold)
        fp = sum(1 for _, score, label in rows if label == 0 and score >= threshold)
        npos, nneg = positives, len(rows) - positives
        tpr = tp / npos if npos else 0.0
        fpr = fp / nneg if nneg else 0.0
        feasible = fpr <= target_fpr + 1e-12
        # Primary criterion: maximize recall under the desired negative rate.
        # Secondary criterion: accept more examples, then choose lower threshold.
        key = (1 if feasible else 0, tpr if feasible else -fpr, -threshold)
        if best is None or key > best[0]: best = (key, threshold, tpr, fpr)
    assert best is not None
    _, threshold, tpr, fpr = best
    return threshold, {"selection_tpr": tpr, "selection_fpr": fpr}


def _certify(rows: list[tuple[str, float, int]], threshold: float, alpha: float, multiplicity: int) -> dict[str, Any]:
    negatives = [score for _, score, label in rows if label == 0]
    n = len(negatives)
    k = sum(score >= threshold for score in negatives)
    empirical = k / n if n else None
    alpha_local = alpha / max(1, multiplicity)
    radius = math.sqrt(math.log(1.0 / alpha_local) / (2.0 * n)) if n else None
    upper = min(1.0, empirical + radius) if empirical is not None and radius is not None else None
    return {"negative_count": n, "negative_accepted": k, "empirical_fpr": empirical, "hoeffding_upper_fpr": upper, "alpha_local": alpha_local}


def calibrate_thresholds(
    selection: str | Path | Iterable[dict[str, Any]],
    certification: str | Path | Iterable[dict[str, Any]],
    output: str | Path | None = None,
    *,
    alpha: float = 0.05,
    target_fpr: float = 0.05,
) -> dict[str, Any]:
    """Select and certify per-kind thresholds from disjoint JSONL records.

    A returned ``certified`` flag is false when any direction has no
    certification negatives or its multiplicity-adjusted bound exceeds the
    requested target.  This is an FPR statement for the supplied certification
    split, not a claim about false discovery risk or adaptive reuse.
    """
    if not 0 < alpha < 1: raise ValueError("alpha must be in (0,1)")
    if not 0 <= target_fpr <= 1: raise ValueError("target_fpr must be in [0,1]")
    sel_raw, cert_raw = _rows(selection), _rows(certification)
    # Case-level disjointness is required for the certification statement.
    def _case_id(row: Any) -> str | None:
        if not isinstance(row, dict): return None
        value = row.get("case_id", row.get("id"))
        return str(value) if value is not None else None
    sel_cases = {_case_id(row) for row in sel_raw if _case_id(row) is not None}
    cert_cases = {_case_id(row) for row in cert_raw if _case_id(row) is not None}
    overlap = sorted(sel_cases & cert_cases)
    if overlap: raise ValueError("selection and certification sets overlap in case ids")
    sel: list[tuple[str, float, int]] = []
    cert: list[tuple[str, float, int]] = []
    for i, row in enumerate(sel_raw):
        kind, score, label = _validate_row(row, i); sel.append((kind, score, label))
    for i, row in enumerate(cert_raw):
        kind, score, label = _validate_row(row, i); cert.append((kind, score, label))
    kinds = sorted(set(kind for kind, _, _ in sel) | set(kind for kind, _, _ in cert))
    if not kinds: raise ValueError("calibration input is empty")
    thresholds: dict[str, float] = {}
    per_kind: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        srows = [r for r in sel if r[0] == kind]
        crows = [r for r in cert if r[0] == kind]
        threshold, sel_stats = _select_threshold(srows, target_fpr)
        cert_stats = _certify(crows, threshold, alpha, len(kinds))
        thresholds[kind] = threshold
        per_kind[kind] = {"threshold": threshold, **sel_stats, **cert_stats,
                          "certified": cert_stats["hoeffding_upper_fpr"] is not None and cert_stats["hoeffding_upper_fpr"] <= target_fpr}
    certified = all(stats["certified"] for stats in per_kind.values())
    serialized = json.dumps({"schema_version": CALIBRATION_SCHEMA, "selection": sel_raw, "certification": cert_raw, "alpha": alpha, "target_fpr": target_fpr}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    calibration_id = "sha256:" + hashlib.sha256(serialized).hexdigest()
    result: dict[str, Any] = {
        "schema_version": CALIBRATION_SCHEMA,
        "calibration_id": calibration_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "label_semantics": "verdict_correct (1 means the candidate evidence verdict is correct; 0 means incorrect)",
        "alpha": alpha,
        "target_fpr": target_fpr,
        "multiplicity": len(kinds),
        "thresholds": thresholds,
        "per_kind": per_kind,
        "selection_count": len(sel),
        "certification_count": len(cert),
        "certified": certified,
        "note": "Certification is a multiplicity-adjusted Hoeffding upper bound on negative acceptance for the fixed certification split; it is not a false-discovery guarantee.",
    }
    if "support" in thresholds: result["support_threshold"] = thresholds["support"]
    if "contradict" in thresholds: result["contradict_threshold"] = thresholds["contradict"]
    if output is not None:
        path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


calibrate = calibrate_thresholds
