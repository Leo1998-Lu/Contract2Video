import hashlib
import json
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

from contract2video.interfaces import Evidence, RenderedShot
from contract2video.pipeline import run_pipeline
from contract2video.repair import affected_shots
from contract2video.schema import validate_contract
from contract2video.verification import aggregate_evidence, validate_evidence


def contract():
    return {
        "schema_version": "1.0", "contract_id": "case-1", "prompt": "two shots",
        "entities": [{"id": "hero", "description": "a person"}],
        "shots": [
            {"id": "s1", "prompt": "opening", "duration": 1, "dependencies": [], "entity_ids": ["hero"], "state_in": {}, "state_out": {"ready": True}},
            {"id": "s2", "prompt": "follow-up", "duration": 1, "dependencies": ["s1"], "entity_ids": ["hero"], "state_in": {"ready": True}, "state_out": {}},
        ],
        "constraints": [{"id": "c1", "kind": "identity", "description": "hero persists", "shot_ids": ["s1", "s2"], "hardness": "hard", "weight": 1}],
    }


class Renderer:
    def __init__(self): self.calls = []
    def render(self, contract, shot, target, *, seed, attempt, feedback, references):
        self.calls.append((shot["id"], attempt, tuple(references)))
        target.write_bytes((shot["id"] + str(attempt)).encode())
        return RenderedShot(shot["id"], target, shot["duration"], cost=0.1, seed=seed, attempt=attempt)


class Observer:
    mode = "scripted"
    def __init__(self, verdict="support"): self.verdict = verdict
    def observe(self, contract, assets):
        out = []
        for c in contract["constraints"]:
            hashes = {sid: hashlib.sha256(assets[sid].path.read_bytes()).hexdigest() for sid in c["shot_ids"] if sid in assets}
            spans = [{"shot_id": sid, "start": 0, "end": assets[sid].duration} for sid in c["shot_ids"] if sid in assets]
            out.append(Evidence(c["id"], self.verdict, 0.99, list(hashes), spans, "checked", "fixture", hashes))
        return out


class CoreTests(unittest.TestCase):
    def test_validation_and_dependent_repair(self):
        c = contract()
        self.assertEqual(validate_contract(c), [])
        self.assertEqual(affected_shots(c, ["c1"]), ["s1", "s2"])

    def test_invalid_evidence_becomes_unknown(self):
        c = contract()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.mp4"; p.write_bytes(b"x")
            assets = {"s1": RenderedShot("s1", p, 1), "s2": RenderedShot("s2", p, 1)}
            bad = Evidence("c1", "support", .9, ["s1", "s2"], [{"shot_id": "s1", "start": 0, "end": 2}], "", "fixture", {"s1": "bad", "s2": "bad"})
            row, errors = validate_evidence(bad, c, assets)
            self.assertEqual(row["verdict"], "unknown")
            self.assertTrue(errors)
            decision, _ = aggregate_evidence(c, [bad], assets, support_threshold=.5)
            self.assertEqual(decision["c1"]["status"], "unknown")

    def test_pipeline_accepts_with_evidence_and_writes_manifest(self):
        renderer, observer = Renderer(), Observer()
        media = types.ModuleType("contract2video.media")
        media.compose_videos = lambda paths, target: target.write_bytes(b"joined")
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(sys.modules, {"contract2video.media": media}):
            manifest = run_pipeline(contract(), {"policy": {"max_rounds": 1, "max_render_calls": 4}}, td, renderer=renderer, observer=observer)
            self.assertEqual(manifest["status"], "accepted")
            self.assertTrue((Path(td) / "manifest.json").is_file())
            self.assertEqual(manifest["render_calls"], 2)

    def test_failed_render_is_recorded_and_never_accepted(self):
        class BrokenRenderer(Renderer):
            def render(self, *args, **kwargs):
                raise RuntimeError("generation unavailable")
        with tempfile.TemporaryDirectory() as td:
            manifest = run_pipeline(contract(), {"policy": {"max_rounds": 1, "max_render_calls": 2}}, td, renderer=BrokenRenderer(), observer=Observer())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["render_calls"], 2)
            self.assertTrue(manifest["errors"])

    def test_uncertified_thresholds_abstain_when_requested(self):
        renderer, observer = Renderer(), Observer()
        media = types.ModuleType("contract2video.media")
        media.compose_videos = lambda paths, target: target.write_bytes(b"joined")
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(sys.modules, {"contract2video.media": media}):
            manifest = run_pipeline(contract(), {"policy": {"max_rounds": 1, "require_certified": True}}, td, renderer=renderer, observer=observer)
            self.assertEqual(manifest["status"], "unresolved")
            self.assertTrue(all(d["status"] == "unknown" for d in manifest["decisions"]))

    def test_budget_is_enforced_before_generation(self):
        renderer, observer = Renderer(), Observer("unknown")
        with tempfile.TemporaryDirectory() as td:
            manifest = run_pipeline(contract(), {"policy": {"max_rounds": 3, "max_render_calls": 1, "estimated_cost": 1}}, td, renderer=renderer, observer=observer)
            self.assertEqual(manifest["status"], "budget_exhausted")
            self.assertEqual(manifest["render_calls"], 1)
            self.assertEqual(len(renderer.calls), 1)


if __name__ == "__main__": unittest.main()
