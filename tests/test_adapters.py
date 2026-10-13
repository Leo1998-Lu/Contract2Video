import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from contract2video.adapters import (
    ChatPlanner,
    ScriptedObserver,
    VLMObserver,
    make_observer,
    make_planner,
    make_renderer,
)
from contract2video.media import sha256


class AdapterTests(unittest.TestCase):
    def test_local_planner_is_deterministic(self):
        planner = make_planner({"type": "chat"})
        self.assertEqual(planner.compile("same"), planner.compile("same"))

    def test_scripted_fixture_validates_hash_and_span(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = ChatPlanner().compile("a scene")
            renderer = make_renderer({"type": "procedural", "width": 64, "height": 64, "fps": 2})
            shot = contract["shots"][0]
            asset = renderer.render(contract, shot, root / "clip.mp4", seed=1, attempt=0, feedback=[], references=[])
            fixture = [{
                "constraint_id": "constraint_001", "verdict": "support", "confidence": 0.9,
                "shot_ids": ["shot_001"], "spans": [{"shot_id": "shot_001", "start": 0.0, "end": 1.0}],
                "clip_hashes": {"shot_001": sha256(asset.path)}, "rationale": "fixture",
            }]
            observer = ScriptedObserver({"evidence": fixture})
            result = observer.observe(contract, {"shot_001": asset})
            self.assertEqual(result[0].verdict, "support")
            fixture[0]["clip_hashes"]["shot_001"] = "bad"
            result = ScriptedObserver({"evidence": fixture}).observe(contract, {"shot_001": asset})
            self.assertEqual(result[0].verdict, "unknown")

    def test_vlm_is_explicit_stub(self):
        with self.assertRaises(RuntimeError):
            VLMObserver({}).observe({}, {})

    def test_unknown_factory_type(self):
        with self.assertRaises(ValueError):
            make_observer({"type": "other"})


if __name__ == "__main__":
    unittest.main()

