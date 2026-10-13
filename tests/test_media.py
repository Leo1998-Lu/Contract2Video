import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from contract2video.adapters import ChatPlanner
from contract2video.media import compose_videos, probe_video, sample_frames, sha256
from contract2video.procedural import ProceduralRenderer


class MediaTests(unittest.TestCase):
    def test_round_trip_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = ChatPlanner().compile("a geometric scene")
            renderer = ProceduralRenderer({"width": 128, "height": 72, "fps": 4})
            first = renderer.render(contract, contract["shots"][0], root / "a.mp4", seed=3, attempt=0, feedback=[], references=[])
            self.assertEqual(probe_video(first.path)["width"], 128)
            self.assertTrue(sha256(first.path))
            frames = sample_frames(first.path, 2, root / "frames")
            self.assertEqual(len(frames), 2)
            compose_videos([first.path], root / "joined.mp4")
            self.assertGreater(probe_video(root / "joined.mp4")["duration"], 0)

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = ChatPlanner().compile("a scene")
            renderer = ProceduralRenderer({"width": 64, "height": 64, "fps": 2})
            target = root / "x.mp4"
            renderer.render(contract, contract["shots"][0], target, seed=1, attempt=0, feedback=[], references=[])
            with self.assertRaises(FileExistsError):
                renderer.render(contract, contract["shots"][0], target, seed=1, attempt=1, feedback=[], references=[])


if __name__ == "__main__":
    unittest.main()

