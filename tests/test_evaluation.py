import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contract2video.evaluation import (
    aggregate_cases,
    aggregate_case_manifests,
    export_external_manifest,
    export_human_pairs,
    paired_bootstrap_ci,
    paired_randomization_test,
    paired_statistics,
    validate_human_labels,
)


def manifest(case, method, statuses, cost=1.0):
    return {
        "contract_id": case,
        "method": method,
        "status": "accepted" if all(x == "supported" for x in statuses) else "unresolved",
        "total_cost": cost,
        "decisions": [{"constraint_id": str(i), "hardness": "hard", "status": s} for i, s in enumerate(statuses)],
    }


class EvaluationTests(unittest.TestCase):
    def test_aggregates_seeds_per_case(self):
        rows = [manifest("case-1", "a", ["supported", "unknown"], 2), manifest("case-1", "a", ["supported", "supported"], 4)]
        summary = aggregate_case_manifests(rows)
        self.assertEqual(summary["n_cases"], 1)
        self.assertEqual(summary["cases"][0]["n_runs"], 2)
        self.assertAlmostEqual(summary["hard_pass_rate"], 0.75)
        self.assertAlmostEqual(summary["unknown_rate"], 0.25)
        self.assertEqual(summary["cost_total"], 6.0)

    def test_metrics_do_not_treat_unknown_as_pass(self):
        row = aggregate_case_manifests([manifest("x", "a", ["unknown", "violated"])])["cases"][0]
        self.assertEqual(row["hard_pass_rate"], 0.0)
        self.assertEqual(row["unknown_rate"], 0.5)
        self.assertEqual(row["violation_rate"], 0.5)

    def test_paired_statistics_are_reproducible(self):
        left = aggregate_cases([manifest(str(i), "a", ["supported"]) for i in range(5)])
        right = aggregate_cases([manifest(str(i), "b", ["unknown"]) for i in range(5)])
        first = paired_statistics(left, right, n_resamples=200, seed=7)
        second = paired_statistics(left, right, n_resamples=200, seed=7)
        self.assertEqual(first, second)
        self.assertGreater(first["estimate"], 0)

    def test_pair_functions_reject_mismatched_inputs(self):
        with self.assertRaises(ValueError):
            paired_bootstrap_ci([1], [1, 2])
        with self.assertRaises(ValueError):
            paired_randomization_test([], [])

    def test_human_pairs_keep_key_separate_and_validate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "left.mp4").write_bytes(b"left")
            (root / "right.mp4").write_bytes(b"right")
            result = export_human_pairs(
                [{"case_id": "case-1", "left_path": root / "left.mp4", "right_path": root / "right.mp4", "left_method": "method-a", "right_method": "method-b"}],
                root / "pairs",
                seed=5,
            )
            public = json.loads((root / "pairs" / "pairs.jsonl").read_text())
            self.assertNotIn("method-a", json.dumps(public))
            key = json.loads(Path(result["key"]).read_text())
            self.assertEqual(key["pairs"][0]["case_id"], "case-1")
            labels = [{"pair_id": public["pair_id"], "annotator_id": "r1", "label": "A"}]
            self.assertTrue(validate_human_labels(labels, root / "pairs" / "pairs.jsonl")["valid"])
            self.assertFalse(validate_human_labels([{**labels[0], "label": "maybe"}], root / "pairs" / "pairs.jsonl")["valid"])

    def test_external_export_contains_no_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "vbench.jsonl"
            result = export_external_manifest([{"contract_id": "x", "assets": {"s": {"path": "clips/x.mp4"}}}], output, benchmark="vbench")
            row = json.loads(output.read_text())
            self.assertEqual(result["n_cases"], 1)
            self.assertIsNone(row["scores"])
            self.assertEqual(row["evaluation_status"], "pending_external_evaluation")


if __name__ == "__main__":
    unittest.main()
