"""Case-level evaluation utilities for Contract2Video.

The evaluator deliberately keeps the unit of analysis at the prompt/case level.
Several seeds, render attempts, or sampled frames belonging to one case are
collapsed before reporting uncertainty.  The module is stdlib-only so that it
can be used with the small reference implementation and with exported results
from larger video systems.

No perceptual scores are inferred here.  The external benchmark exporter emits
clip manifests for VBench, T2V-CompBench, and VideoScore; scores must be
produced by the corresponding evaluator and imported explicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
from collections import defaultdict
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence


EXTERNAL_BENCHMARKS = {
    "vbench": "VBench",
    "t2v-compbench": "T2V-CompBench",
    "t2v_compbench": "T2V-CompBench",
    "t2vcompbench": "T2V-CompBench",
    "videoscore": "VideoScore",
}


@dataclass(frozen=True)
class CaseMetric:
    """One independent case after collapsing any within-case replicates."""

    case_id: str
    method: str
    n_runs: int
    hard_pass_rate: float
    unknown_rate: float
    violation_rate: float
    cost: float
    cost_total: float
    render_calls: float
    accepted_rate: float
    statuses: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "method": self.method,
            "n_runs": self.n_runs,
            "hard_pass_rate": self.hard_pass_rate,
            "unknown_rate": self.unknown_rate,
            "violation_rate": self.violation_rate,
            "cost": self.cost,
            "cost_total": self.cost_total,
            "render_calls": self.render_calls,
            "accepted_rate": self.accepted_rate,
            "statuses": list(self.statuses),
        }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _read_records(value: Any) -> list[dict[str, Any]]:
    """Read JSON, JSONL, a directory of manifests, or already-loaded records."""
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(dict(item))
            else:
                result.extend(_read_records(item))
        return result
    if isinstance(value, IterableABC) and not isinstance(value, (str, bytes, bytearray, Path)):
        return _read_records(list(value))
    path = Path(value)
    if path.is_dir():
        files = sorted(path.rglob("manifest.json"))
        if not files:
            files = sorted(path.rglob("*.json"))
        result: list[dict[str, Any]] = []
        for item in files:
            result.extend(_read_records(item))
        return result
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        result = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}") from exc
            if not isinstance(item, Mapping):
                raise ValueError(f"JSONL line {line_number} of {path} is not an object")
            result.append(dict(item))
        return result
    if isinstance(parsed, Mapping):
        # A summary document may contain a case list.  Keep plain manifests as
        # one record and expand common aggregate wrappers when present.
        for wrapper in ("cases", "records", "labels", "pairs"):
            if isinstance(parsed.get(wrapper), list):
                return [dict(item) for item in parsed[wrapper] if isinstance(item, Mapping)]
        return [dict(parsed)]
    if isinstance(parsed, list):
        return [dict(item) for item in parsed if isinstance(item, Mapping)]
    raise ValueError(f"Expected an object or list in {path}")


def load_manifests(source: Any) -> list[dict[str, Any]]:
    """Load independent per-case manifests from paths or JSON-like values."""
    return _read_records(source)


def _nested(manifest: Mapping[str, Any], *names: str) -> Any:
    metadata = manifest.get("metadata")
    for name in names:
        if name in manifest and manifest[name] not in (None, ""):
            return manifest[name]
        if isinstance(metadata, Mapping) and name in metadata and metadata[name] not in (None, ""):
            return metadata[name]
    return None


def case_id(manifest: Mapping[str, Any], fallback: str = "case") -> str:
    """Return a stable case key, preferring an explicit prompt/case identifier."""
    value = _nested(manifest, "case_id", "prompt_id", "contract_id", "prompt")
    if value is None:
        # The caller supplies a deterministic index for nameless fixtures.  It
        # is intentionally not derived from a path, which can reveal a user
        # workspace in exported result files.
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def method_name(manifest: Mapping[str, Any], fallback: str = "method") -> str:
    value = _nested(manifest, "method", "model", "system", "method_name")
    if value is None:
        return fallback
    return str(value)


def _normalise_status(status: Any) -> str:
    status = str(status or "").strip().lower()
    aliases = {"pass": "supported", "passed": "supported", "fail": "violated", "failed": "violated"}
    return aliases.get(status, status)


def manifest_metrics(manifest: Mapping[str, Any]) -> dict[str, float | int | str]:
    """Derive hard pass, unknown, violation, and budget metrics for one run."""
    decisions = manifest.get("decisions")
    hard = []
    if isinstance(decisions, list):
        hard = [d for d in decisions if isinstance(d, Mapping) and str(d.get("hardness", "hard")).lower() == "hard"]
    status = _normalise_status(manifest.get("status"))
    if hard:
        statuses = [_normalise_status(d.get("status", d.get("verdict"))) for d in hard]
        total = len(statuses)
        supported = sum(s == "supported" for s in statuses)
        unknown = sum(s == "unknown" for s in statuses)
        violated = sum(s in {"violated", "contradict", "contradicted"} for s in statuses)
        pass_rate = supported / total
        unknown_rate = unknown / total
        violation_rate = violated / total
    else:
        total = int(_as_float(manifest.get("hard_total"), 0))
        has_rates = any(key in manifest for key in ("hard_pass_rate", "unknown_rate", "violation_rate"))
        pass_rate = _as_float(manifest.get("hard_pass_rate"), 1.0 if status == "accepted" else 0.0)
        unknown_rate = _as_float(manifest.get("unknown_rate"), 1.0 if status == "unresolved" else 0.0)
        violation_rate = _as_float(manifest.get("violation_rate"), 1.0 if status in {"failed", "violated"} else 0.0)
        if total <= 0 and not has_rates:
            # A case with no listed hard constraints is vacuously satisfied only
            # when the pipeline explicitly accepted it.  It is otherwise
            # represented as unknown rather than silently treated as a pass.
            pass_rate = 1.0 if status == "accepted" else 0.0
            unknown_rate = 0.0 if status == "accepted" else 1.0
            violation_rate = 0.0
    return {
        "status": status,
        "hard_total": total,
        "hard_pass_rate": max(0.0, min(1.0, pass_rate)),
        "unknown_rate": max(0.0, min(1.0, unknown_rate)),
        "violation_rate": max(0.0, min(1.0, violation_rate)),
        "cost": max(0.0, _as_float(manifest.get("total_cost", manifest.get("cost", 0.0)))),
        "render_calls": max(0, int(_as_float(manifest.get("render_calls", 0)))),
        "accepted": max(0.0, min(1.0, _as_float(manifest.get("accepted_rate"), 1.0 if status == "accepted" else 0.0))),
    }


def aggregate_cases(
    manifests: Iterable[Mapping[str, Any]] | Any,
    *,
    method: str | None = None,
) -> list[CaseMetric]:
    """Collapse runs by ``(method, case_id)`` and return independent cases.

    If several seeds or attempts share a case identifier, their rates are
    averaged within that case.  They therefore contribute one observation to
    confidence intervals and paired tests.
    """
    records = load_manifests(manifests)
    buckets: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for index, manifest in enumerate(records):
        m = method or method_name(manifest)
        cid = case_id(manifest, fallback=f"case-{index:06d}")
        buckets[(m, cid)].append((manifest, manifest_metrics(manifest)))
    output: list[CaseMetric] = []
    for (m, cid), rows in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1])):
        metrics = [row[1] for row in rows]
        costs = [_as_float(x["cost"]) for x in metrics]
        output.append(
            CaseMetric(
                case_id=cid,
                method=m,
                n_runs=len(rows),
                hard_pass_rate=mean(_as_float(x["hard_pass_rate"]) for x in metrics),
                unknown_rate=mean(_as_float(x["unknown_rate"]) for x in metrics),
                violation_rate=mean(_as_float(x["violation_rate"]) for x in metrics),
                cost=mean(costs) if costs else 0.0,
                cost_total=sum(costs),
                render_calls=mean(_as_float(x["render_calls"]) for x in metrics),
                accepted_rate=mean(_as_float(x["accepted"]) for x in metrics),
                statuses=tuple(sorted({str(x["status"]) for x in metrics})),
            )
        )
    return output


def summarize_cases(cases: Iterable[CaseMetric | Mapping[str, Any]]) -> dict[str, Any]:
    """Report means over independent cases and explicit cost totals."""
    rows = [x.as_dict() if isinstance(x, CaseMetric) else dict(x) for x in cases]
    if not rows:
        return {
            "n_cases": 0,
            "hard_pass_rate": None,
            "unknown_rate": None,
            "violation_rate": None,
            "accepted_rate": None,
            "cost_total": 0.0,
            "cost_per_case": None,
            "render_calls_total": 0.0,
            "cases": [],
        }
    return {
        "n_cases": len(rows),
        "hard_pass_rate": mean(_as_float(x.get("hard_pass_rate")) for x in rows),
        "unknown_rate": mean(_as_float(x.get("unknown_rate")) for x in rows),
        "violation_rate": mean(_as_float(x.get("violation_rate")) for x in rows),
        "accepted_rate": mean(_as_float(x.get("accepted_rate")) for x in rows),
        "cost_total": sum(_as_float(x.get("cost_total", x.get("cost"))) for x in rows),
        "cost_per_case": mean(_as_float(x.get("cost")) for x in rows),
        "render_calls_total": sum(_as_float(x.get("render_calls")) for x in rows),
        "cases": rows,
    }


def aggregate_case_manifests(manifests: Iterable[Mapping[str, Any]] | Any, *, method: str | None = None) -> dict[str, Any]:
    """Convenience wrapper returning both cases and a summary."""
    cases = aggregate_cases(manifests, method=method)
    summary = summarize_cases(cases)
    by_method: dict[str, dict[str, Any]] = {}
    for method_name_value in sorted({case.method for case in cases}):
        by_method[method_name_value] = summarize_cases(case for case in cases if case.method == method_name_value)
    summary["by_method"] = by_method
    return summary


def _metric_map(cases: Iterable[CaseMetric | Mapping[str, Any]], metric: str) -> dict[str, float]:
    result = {}
    for case in cases:
        row = case.as_dict() if isinstance(case, CaseMetric) else case
        if "case_id" not in row:
            raise ValueError("Each case row needs case_id for paired analysis")
        if metric not in row:
            raise ValueError(f"Unknown case metric: {metric}")
        result[str(row["case_id"])] = _as_float(row[metric])
    return result


def paired_values(
    left: Iterable[CaseMetric | Mapping[str, Any]],
    right: Iterable[CaseMetric | Mapping[str, Any]],
    *,
    metric: str = "hard_pass_rate",
) -> tuple[list[str], list[float], list[float]]:
    """Align two methods on shared case identifiers in deterministic order."""
    a, b = _metric_map(left, metric), _metric_map(right, metric)
    ids = sorted(set(a).intersection(b))
    if not ids:
        raise ValueError("No shared case identifiers for paired analysis")
    return ids, [a[i] for i in ids], [b[i] for i in ids]


def _paired_numeric(
    left: Sequence[Any],
    right: Sequence[Any],
    metric: str | None,
) -> tuple[list[float], list[float]]:
    """Accept either numeric vectors or case rows for the statistical helpers."""
    if len(left) != len(right):
        raise ValueError("Paired analysis requires equal-length inputs")
    if left and isinstance(left[0], (Mapping, CaseMetric)):
        _, a, b = paired_values(left, right, metric=metric or "hard_pass_rate")
        return a, b
    try:
        return [float(x) for x in left], [float(x) for x in right]
    except (TypeError, ValueError) as exc:
        raise ValueError("Paired inputs must be numeric vectors or case rows") from exc


def paired_bootstrap_ci(
    left: Sequence[Any],
    right: Sequence[Any],
    *,
    n_resamples: int = 10000,
    seed: int = 0,
    confidence: float = 0.95,
    metric: str | None = None,
) -> dict[str, Any]:
    """Paired percentile bootstrap CI for ``mean(left - right)``."""
    if not left:
        raise ValueError("Paired bootstrap requires equal non-empty vectors")
    if n_resamples < 1 or not (0.0 < confidence < 1.0):
        raise ValueError("n_resamples must be positive and confidence in (0, 1)")
    left_values, right_values = _paired_numeric(left, right, metric)
    differences = [a - b for a, b in zip(left_values, right_values)]
    rng = random.Random(seed)
    n = len(differences)
    samples = []
    for _ in range(n_resamples):
        samples.append(mean(differences[rng.randrange(n)] for _ in range(n)))
    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    low = samples[min(len(samples) - 1, max(0, int(math.floor(alpha * len(samples)))))]
    high_index = min(len(samples) - 1, max(0, int(math.ceil((1.0 - alpha) * len(samples)) - 1)))
    return {
        "estimate": mean(differences),
        "lower": float(low),
        "upper": float(samples[high_index]),
        "confidence": confidence,
        "n_cases": n,
        "n_resamples": n_resamples,
        "seed": seed,
    }


def paired_randomization_test(
    left: Sequence[Any],
    right: Sequence[Any],
    *,
    n_resamples: int = 10000,
    seed: int = 0,
    alternative: str = "two-sided",
    metric: str | None = None,
) -> dict[str, Any]:
    """Paired sign-flip randomization test with a reproducible RNG."""
    if not left:
        raise ValueError("Paired randomization requires equal non-empty vectors")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    if alternative not in {"two-sided", "greater", "less"}:
        raise ValueError("alternative must be two-sided, greater, or less")
    left_values, right_values = _paired_numeric(left, right, metric)
    differences = [a - b for a, b in zip(left_values, right_values)]
    observed = mean(differences)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_resamples):
        value = mean(d * (1.0 if rng.getrandbits(1) else -1.0) for d in differences)
        if alternative == "two-sided":
            hit = abs(value) >= abs(observed)
        elif alternative == "greater":
            hit = value >= observed
        else:
            hit = value <= observed
        extreme += int(hit)
    return {
        "estimate": observed,
        "p_value": (extreme + 1) / (n_resamples + 1),
        "alternative": alternative,
        "n_cases": len(differences),
        "n_resamples": n_resamples,
        "seed": seed,
    }


def paired_statistics(
    left: Iterable[CaseMetric | Mapping[str, Any]],
    right: Iterable[CaseMetric | Mapping[str, Any]],
    *,
    metric: str = "hard_pass_rate",
    n_resamples: int = 10000,
    seed: int = 0,
) -> dict[str, Any]:
    ids, a, b = paired_values(left, right, metric=metric)
    result = paired_bootstrap_ci(a, b, n_resamples=n_resamples, seed=seed)
    result.update(paired_randomization_test(a, b, n_resamples=n_resamples, seed=seed))
    result["metric"] = metric
    result["case_ids"] = ids
    return result


def _safe_ext(path: Path) -> str:
    ext = path.suffix.lower()
    return ext if ext in {".mp4", ".webm", ".mov", ".mkv", ".avi"} else ".mp4"


def _pair_id(case_id_value: str, index: int) -> str:
    digest = hashlib.sha256(case_id_value.encode("utf-8")).hexdigest()[:12]
    return f"pair-{index:05d}-{digest}"


def export_human_pairs(
    pairs: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    *,
    seed: int = 0,
    copy_clips: bool = True,
) -> dict[str, str | int]:
    """Export blinded A/B clips and a separate mapping key.

    Each input row needs ``case_id``, ``left_path`` and ``right_path``.  Method
    labels and source paths are written only to ``key.json``.  The public pair
    file contains randomized labels A/B and the prompt when supplied.
    """
    out = Path(output_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    clip_dir = out / "clips"
    clip_dir.mkdir()
    rng = random.Random(seed)
    public_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    for index, item in enumerate(pairs, start=1):
        cid = str(item.get("case_id", f"case-{index:06d}"))
        left = Path(str(item.get("left_path", item.get("a_path", item.get("clip_a", "")))))
        right = Path(str(item.get("right_path", item.get("b_path", item.get("clip_b", "")))))
        if not left.is_file() or not right.is_file():
            raise FileNotFoundError(f"Pair {cid} needs two existing clip files")
        pair_id = _pair_id(cid, index)
        swapped = bool(rng.getrandbits(1))
        labelled = [("A", left), ("B", right)] if not swapped else [("A", right), ("B", left)]
        public_clips: dict[str, str] = {}
        source_by_label: dict[str, str] = {}
        for label, source in labelled:
            destination = clip_dir / f"{pair_id}_{label}{_safe_ext(source)}"
            if copy_clips:
                shutil.copy2(source, destination)
            else:
                destination = source
            public_clips[label] = str(destination.relative_to(out)) if destination.is_relative_to(out) else str(destination)
            source_by_label[label] = str(source)
        public = {"pair_id": pair_id, "clips": public_clips}
        if "prompt" in item:
            public["prompt"] = str(item["prompt"])
        public_rows.append(public)
        key_rows.append(
            {
                "pair_id": pair_id,
                "case_id": cid,
                "labels": {"A": str(item.get("left_method", item.get("method_a", "left"))) if not swapped else str(item.get("right_method", item.get("method_b", "right"))),
                           "B": str(item.get("right_method", item.get("method_b", "right"))) if not swapped else str(item.get("left_method", item.get("method_a", "left")))},
                "source_paths": source_by_label,
            }
        )
    pair_file = out / "pairs.jsonl"
    with pair_file.open("w", encoding="utf-8") as handle:
        for row in public_rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    key_file = out / "key.json"
    key_file.write_text(json.dumps({"schema_version": "1.0", "pairs": key_rows}, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return {"pairs": str(pair_file), "key": str(key_file), "n_pairs": len(public_rows)}


def _read_json_records(source: str | Path | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _read_records(source)


def validate_human_labels(
    labels: str | Path | Iterable[Mapping[str, Any]],
    pairs: str | Path | Iterable[Mapping[str, Any]],
    *,
    allowed: Sequence[str] = ("A", "B", "tie", "invalid"),
    require_all: bool = True,
) -> dict[str, Any]:
    """Validate blinded labels without consulting the private key."""
    pair_rows = _read_json_records(pairs)
    pair_ids = {str(row.get("pair_id")) for row in pair_rows if row.get("pair_id")}
    label_rows = _read_json_records(labels)
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    allowed_set = {str(x) for x in allowed}
    for index, row in enumerate(label_rows, start=1):
        pid = str(row.get("pair_id", ""))
        annotator = str(row.get("annotator_id", row.get("rater_id", "default")))
        value = str(row.get("label", row.get("winner", "")))
        if pid not in pair_ids:
            errors.append(f"row {index}: unknown pair_id {pid!r}")
        if value not in allowed_set:
            errors.append(f"row {index}: label must be one of {sorted(allowed_set)}")
        marker = (annotator, pid)
        if marker in seen:
            errors.append(f"row {index}: duplicate label for annotator {annotator!r} and pair {pid!r}")
        seen.add(marker)
    if require_all:
        annotators = {str(row.get("annotator_id", row.get("rater_id", "default"))) for row in label_rows}
        for annotator in annotators or {"default"}:
            missing = pair_ids - {str(row.get("pair_id")) for row in label_rows if str(row.get("annotator_id", row.get("rater_id", "default"))) == annotator}
            errors.extend(f"missing pair {pid!r} for annotator {annotator!r}" for pid in sorted(missing))
    return {"valid": not errors, "errors": errors, "n_pairs": len(pair_ids), "n_labels": len(label_rows), "allowed": sorted(allowed_set)}


def export_external_manifest(
    records: Iterable[Mapping[str, Any]] | Any,
    output: str | Path,
    *,
    benchmark: str,
) -> dict[str, Any]:
    """Export clip paths and contract metadata for an external evaluator.

    This function never inserts perceptual scores.  The resulting JSONL is an
    input handoff for the selected benchmark and can be consumed by a project-
    specific adapter after that benchmark has been installed and configured.
    """
    key = benchmark.strip().lower()
    if key not in EXTERNAL_BENCHMARKS:
        raise ValueError(f"Unsupported external benchmark: {benchmark}")
    canonical = EXTERNAL_BENCHMARKS[key]
    rows = load_manifests(records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for index, record in enumerate(rows):
            assets = record.get("assets", {})
            if isinstance(assets, Mapping):
                asset_values = assets.values()
            elif isinstance(assets, list):
                asset_values = assets
            else:
                asset_values = []
            clips = []
            for asset in asset_values:
                if isinstance(asset, Mapping):
                    clip = asset.get("path") or asset.get("clip")
                else:
                    clip = asset
                if clip:
                    clips.append(str(clip))
            if not clips and record.get("final_path"):
                clips.append(str(record["final_path"]))
            row = {
                "schema_version": "1.0",
                "benchmark": canonical,
                "case_id": case_id(record, fallback=f"case-{index:06d}"),
                "prompt": _nested(record, "prompt"),
                "clips": clips,
                "scores": None,
                "evaluation_status": "pending_external_evaluation",
            }
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
            count += 1
    return {"output": str(path), "benchmark": canonical, "n_cases": count, "scores": None}


# Descriptive aliases used by downstream scripts.  They keep the main API
# discoverable without duplicating implementation or producing any measured
# values for an external benchmark.
export_blinded_pairs = export_human_pairs
validate_human_evaluation_labels = validate_human_labels
export_external_clips = export_external_manifest
paired_bootstrap = paired_bootstrap_ci
paired_randomization = paired_randomization_test


__all__ = [
    "CaseMetric",
    "aggregate_cases",
    "aggregate_case_manifests",
    "summarize_cases",
    "load_manifests",
    "manifest_metrics",
    "paired_values",
    "paired_bootstrap_ci",
    "paired_bootstrap",
    "paired_randomization_test",
    "paired_randomization",
    "paired_statistics",
    "export_human_pairs",
    "export_blinded_pairs",
    "validate_human_labels",
    "validate_human_evaluation_labels",
    "export_external_manifest",
    "export_external_clips",
]
