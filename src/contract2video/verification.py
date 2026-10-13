"""Conservative evidence validation and constraint aggregation."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from .interfaces import Evidence, RenderedShot


def _asset_path(asset: Any) -> Path | None:
    path = asset.get("path") if isinstance(asset, dict) else getattr(asset, "path", None)
    return Path(path) if path is not None else None


def _asset_duration(asset: Any) -> float:
    value = asset.get("duration", 0.0) if isinstance(asset, dict) else getattr(asset, "duration", 0.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_dict(item: Evidence | dict[str, Any]) -> dict[str, Any]:
    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, dict):
        return dict(item)
    return {
        "constraint_id": getattr(item, "constraint_id", ""),
        "verdict": getattr(item, "verdict", "unknown"),
        "confidence": getattr(item, "confidence", 0.0),
        "shot_ids": list(getattr(item, "shot_ids", []) or []),
        "spans": list(getattr(item, "spans", []) or []),
        "rationale": getattr(item, "rationale", ""),
        "source": getattr(item, "source", ""),
        "clip_hashes": dict(getattr(item, "clip_hashes", {}) or {}),
    }


def validate_evidence(
    evidence: Evidence | dict[str, Any],
    contract: dict[str, Any],
    assets: dict[str, RenderedShot | dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Validate one evidence record; invalid or incomplete records become unknown.

    The returned record always has the original rationale/source, but its verdict
    is set to ``unknown`` when ids, timestamps, or content hashes cannot be
    established.  Confidence is reliability of the observation, not truth
    probability.
    """
    item = evidence_dict(evidence)
    problems: list[str] = []
    cid = item.get("constraint_id")
    constraints = {c.get("id"): c for c in contract.get("constraints", []) if isinstance(c, dict)}
    shots = {s.get("id"): s for s in contract.get("shots", []) if isinstance(s, dict)}
    con = constraints.get(cid)
    if con is None:
        problems.append("unknown constraint id")
    verdict = item.get("verdict")
    if verdict not in {"support", "contradict", "unknown"}:
        problems.append("invalid verdict")
        verdict = "unknown"
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        problems.append("confidence must be in [0,1]")
        confidence = 0.0
    confidence = float(confidence)
    listed = item.get("shot_ids")
    if not isinstance(listed, list) or any(not isinstance(x, str) for x in listed):
        problems.append("shot_ids must be a list of strings")
        listed = []
    listed_set = set(listed)
    if len(listed_set) != len(listed):
        problems.append("shot_ids contains duplicates")
    if con is not None:
        scope = set(con.get("shot_ids", []))
        if not listed_set.issubset(scope):
            problems.append("evidence references shots outside constraint scope")
    for sid in listed:
        if sid not in shots:
            problems.append(f"unknown shot id {sid!r}")
        if sid not in assets:
            problems.append(f"missing rendered asset {sid!r}")
    spans = item.get("spans")
    if not isinstance(spans, list):
        problems.append("spans must be a list")
        spans = []
    normalized_spans: list[dict[str, Any]] = []
    span_shots: set[str] = set()
    for span in spans:
        if not isinstance(span, dict):
            problems.append("span must be an object"); continue
        sid = span.get("shot_id")
        start, end = span.get("start"), span.get("end")
        if sid not in listed_set:
            problems.append("span shot is absent from shot_ids"); continue
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            problems.append("span times must be numbers"); continue
        start, end = float(start), float(end)
        duration = _asset_duration(assets.get(sid))
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration + 1e-6:
            problems.append(f"invalid span bounds for {sid!r}"); continue
        normalized_spans.append({"shot_id": sid, "start": start, "end": end})
        span_shots.add(sid)
    hashes = item.get("clip_hashes")
    if not isinstance(hashes, dict):
        problems.append("clip_hashes must be an object")
        hashes = {}
    normalized_hashes: dict[str, str] = {}
    for sid, supplied in hashes.items():
        if sid not in listed_set or not isinstance(supplied, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", supplied):
            problems.append(f"invalid hash entry for {sid!r}"); continue
        path = _asset_path(assets.get(sid))
        if path is None or not path.is_file():
            problems.append(f"asset path unavailable for {sid!r}"); continue
        try:
            actual = _sha256(path)
        except OSError:
            problems.append(f"cannot hash asset for {sid!r}"); continue
        if supplied.lower() != actual.lower():
            problems.append(f"hash mismatch for {sid!r}"); continue
        normalized_hashes[sid] = supplied.lower()

    # Every non-unknown assessment must identify a valid span and hash for each
    # referenced clip.  Unknown observations are allowed to carry no evidence.
    if verdict != "unknown":
        if not str(item.get("source", "")).strip():
            problems.append("non-unknown evidence needs a non-empty source")
        if not listed_set:
            problems.append("non-unknown evidence needs referenced shots")
        if span_shots != listed_set:
            problems.append("non-unknown evidence needs timestamp coverage for every referenced shot")
        if set(normalized_hashes) != listed_set:
            problems.append("non-unknown evidence needs a valid hash for every referenced shot")
    if problems:
        verdict = "unknown"
        confidence = 0.0
    item.update({
        "constraint_id": cid,
        "verdict": verdict,
        "confidence": confidence,
        "shot_ids": listed,
        "spans": normalized_spans,
        "clip_hashes": normalized_hashes,
        "rationale": str(item.get("rationale", "")),
        "source": str(item.get("source", "")),
    })
    return item, problems


def _threshold_for(verdict: str, thresholds: dict[str, Any] | None, default: float, *, kind: str | None = None, require_certified: bool = False) -> float | None:
    if thresholds is None:
        return None if require_certified else default
    key = "support_threshold" if verdict == "support" else "contradict_threshold"
    val: Any = thresholds.get(key, None)
    # Calibration files may provide global direction thresholds or a threshold
    # per verifier/constraint kind.  Global values take precedence.
    nested = thresholds.get("thresholds")
    if val is None and isinstance(nested, dict):
        val = nested.get(verdict)
        if val is None and kind is not None:
            val = nested.get(f"{kind}:{verdict}", nested.get(kind))
    if val is None: val = default
    certified = thresholds.get("certified", True)
    if require_certified and not certified:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool) and 0 <= float(val) <= 1:
        return float(val)
    return None


def aggregate_evidence(
    contract: dict[str, Any],
    evidences: Iterable[Evidence | dict[str, Any]],
    assets: dict[str, RenderedShot | dict[str, Any]],
    *,
    support_threshold: float = 0.5,
    contradict_threshold: float = 0.5,
    calibration: dict[str, Any] | None = None,
    require_certified: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate evidence by constraint using contradiction-first semantics."""
    by_id: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in contract.get("constraints", []) if isinstance(c, dict) and "id" in c}
    invalid: list[dict[str, Any]] = []
    for raw in evidences:
        item, problems = validate_evidence(raw, contract, assets)
        if problems:
            invalid.append({"evidence": item, "errors": problems})
        if item.get("constraint_id") in by_id:
            by_id[item["constraint_id"]].append(item)
    result: dict[str, dict[str, Any]] = {}
    for con in contract.get("constraints", []):
        if not isinstance(con, dict) or "id" not in con: continue
        cid = con["id"]
        scope = set(con.get("shot_ids", []))
        entries = by_id.get(cid, [])
        accepted_contradictions: list[dict[str, Any]] = []
        accepted_support: list[dict[str, Any]] = []
        for item in entries:
            verdict = item["verdict"]
            if verdict == "unknown": continue
            threshold = _threshold_for(verdict, calibration, contradict_threshold if verdict == "contradict" else support_threshold, kind=con.get("kind"), require_certified=require_certified)
            if threshold is None or item["confidence"] < threshold: continue
            if verdict == "contradict": accepted_contradictions.append(item)
            elif verdict == "support": accepted_support.append(item)
        if accepted_contradictions:
            status = "violated"
        elif scope and scope.issubset({sid for item in accepted_support for sid in item.get("shot_ids", [])}):
            status = "supported"
        else:
            status = "unknown"
        result[cid] = {
            "constraint_id": cid,
            "kind": con.get("kind"),
            "hardness": con.get("hardness"),
            "status": status,
            "supported_shots": sorted({sid for item in accepted_support for sid in item.get("shot_ids", [])}),
            "scope": sorted(scope),
            "evidence_count": len(entries),
            "accepted_support": len(accepted_support),
            "accepted_contradict": len(accepted_contradictions),
        }
    return result, invalid


def decisions_as_list(aggregated: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(v) for _, v in sorted(aggregated.items())]
