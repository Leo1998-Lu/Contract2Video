import unittest
from contract2video.calibration import calibrate_thresholds


class CalibrationTests(unittest.TestCase):
    def test_certification_is_finite_sample_and_multiplicity_adjusted(self):
        selection = [
            {"id": "s1", "kind": "support", "score": .95, "label": 1},
            {"id": "s2", "kind": "support", "score": .20, "label": 0},
            {"id": "c1", "kind": "contradict", "score": .9, "label": 1},
            {"id": "c2", "kind": "contradict", "score": .1, "label": 0},
        ]
        certification = [
            {"id": "t1", "kind": "support", "score": .96, "label": 1},
            {"id": "t2", "kind": "support", "score": .1, "label": 0},
            {"id": "t3", "kind": "contradict", "score": .91, "label": 1},
            {"id": "t4", "kind": "contradict", "score": .2, "label": 0},
        ]
        result = calibrate_thresholds(selection, certification, alpha=.05, target_fpr=.2)
        self.assertEqual(result["multiplicity"], 2)
        self.assertIn("hoeffding_upper_fpr", result["per_kind"]["support"])
        self.assertTrue(result["calibration_id"].startswith("sha256:"))

    def test_overlapping_case_ids_rejected(self):
        rows = [{"id": "same", "kind": "support", "score": .5, "label": 1}]
        with self.assertRaises(ValueError): calibrate_thresholds(rows, rows)


if __name__ == "__main__": unittest.main()
