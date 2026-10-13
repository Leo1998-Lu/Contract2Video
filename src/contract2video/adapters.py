"""Renderer, observer, and planner adapters.

External generation and language-model clients are imported or contacted only
when the corresponding adapter is used.  The default path is fully local and
deterministic, which makes the repository useful without credentials.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .interfaces import Evidence, RenderedShot
from .media import probe_video, sha256
from .procedural import ProceduralRenderer, encode_images_to_video


def _new_target(target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {target}")
    return target


def _json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CommandRenderer:
    """Run a configured executable using an argv list and fixed placeholders."""

    ALLOWED = {
        "target", "shot_id", "prompt", "seed", "attempt", "duration",
        "feedback_json", "references_json", "contract_id",
    }

    def __init__(self, config: dict):
        command = config.get("command")
        args = config.get("args", [])
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("command renderer requires a non-empty argv list")
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError("command renderer args must be a list of strings")
        self.command = list(command) + list(args)
        self.timeout = float(config.get("timeout", 600.0))
        self.cost = float(config.get("cost", config.get("estimated_cost", 0.0)))
        self.estimated_cost = float(config.get("estimated_cost", self.cost))
        if not math.isfinite(self.timeout) or not math.isfinite(self.cost) or not math.isfinite(self.estimated_cost) or self.timeout <= 0 or self.cost < 0 or self.estimated_cost < 0:
            raise ValueError("command timeout must be positive and cost non-negative")
        self.cwd = config.get("cwd")
        if self.cwd is not None:
            self.cwd = str(Path(self.cwd))
        self.env = {str(k): str(v) for k, v in (config.get("env") or {}).items()}
        unknown = set(config.get("placeholders", [])) - self.ALLOWED
        if unknown:
            raise ValueError(f"unsupported command placeholders: {sorted(unknown)}")

    def estimate_cost(self, contract: dict, shot: dict) -> float:
        return self.estimated_cost

    @staticmethod
    def _substitute(template: str, values: dict[str, str]) -> str:
        # Exact token substitution avoids format-string evaluation and shell syntax.
        result = str(template)
        for key, value in values.items():
            result = result.replace("{" + key + "}", value)
        leftovers = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", result)
        if leftovers:
            raise ValueError(f"unsupported placeholder(s) in command: {sorted(set(leftovers))}")
        return result

    def render(self, contract: dict, shot: dict, target: Path, *, seed: int,
               attempt: int, feedback: list[dict], references: list[Path]) -> RenderedShot:
        destination = _new_target(Path(target))
        values = {
            "target": str(destination),
            "shot_id": str(shot.get("id", "")),
            "prompt": str(shot.get("prompt", "")),
            "seed": str(int(seed)),
            "attempt": str(int(attempt)),
            "duration": str(float(shot.get("duration", 0))),
            "feedback_json": _json_compact(feedback),
            "references_json": _json_compact([str(Path(p)) for p in references]),
            "contract_id": str(contract.get("contract_id", "")),
        }
        argv = [self._substitute(part, values) for part in self.command]
        environment = os.environ.copy()
        environment.update(self.env)
        try:
            result = subprocess.run(
                argv, check=False, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd, env=environment,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            self._write_failure_log(destination, argv, exc, "", "")
            raise RuntimeError(f"command renderer failed to execute: {exc}") from exc
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            self._write_failure_log(destination, argv, result, result.stdout, result.stderr)
            raise RuntimeError(
                f"command renderer exited with status {result.returncode}: {result.stderr.strip()}"
            )
        try:
            info = probe_video(destination)
        except Exception:
            self._write_failure_log(destination, argv, result, result.stdout, result.stderr)
            raise
        return RenderedShot(
            shot_id=str(shot.get("id", "")), path=destination,
            duration=float(info["duration"]), cost=self.cost,
            seed=int(seed), attempt=int(attempt),
            metadata={
                "renderer": "command", "sha256": sha256(destination),
                "executable": Path(argv[0]).name,
                "command_digest": hashlib.sha256(_json_compact(argv).encode("utf-8")).hexdigest(),
            },
        )

    @staticmethod
    def _write_failure_log(target: Path, argv: list[str], process: Any, stdout: str, stderr: str) -> None:
        log_path = target.parent / f"{target.name}.failure.json"
        payload = {
            "argv": argv,
            "stdout": stdout,
            "stderr": stderr,
            "error": repr(process),
        }
        try:
            log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


class WanRenderer:
    """Optional diffusers-based adapter; imports are deferred until render."""

    def __init__(self, config: dict):
        self.config = dict(config)
        self.model_id = self.config.get("model_id") or self.config.get("model")
        if not self.model_id:
            raise ValueError("wan renderer requires model_id")
        self.cost = float(self.config.get("cost", self.config.get("estimated_cost", 0.0)))
        self.estimated_cost = float(self.config.get("estimated_cost", self.cost))
        if not math.isfinite(self.cost) or not math.isfinite(self.estimated_cost) or self.cost < 0 or self.estimated_cost < 0:
            raise ValueError("renderer cost cannot be negative")
        self._pipeline = None
        self._device = None

    def estimate_cost(self, contract: dict, shot: dict) -> float:
        return self.estimated_cost

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            import torch  # type: ignore
            from diffusers import DiffusionPipeline  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "wan renderer requires optional torch and diffusers dependencies; install them explicitly"
            ) from exc
        kwargs = dict(self.config.get("pipeline_kwargs") or {})
        device = self.config.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        if str(device).startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("wan renderer requested CUDA but no CUDA device is available")
        try:
            pipeline = DiffusionPipeline.from_pretrained(self.model_id, **kwargs)
            pipeline = pipeline.to(device)
        except Exception as exc:
            raise RuntimeError(f"could not load configured Wan pipeline: {exc}") from exc
        self._pipeline = pipeline
        self._device = str(device)
        return pipeline

    def render(self, contract: dict, shot: dict, target: Path, *, seed: int,
               attempt: int, feedback: list[dict], references: list[Path]) -> RenderedShot:
        destination = _new_target(Path(target))
        pipeline = self._load()
        try:
            import torch  # type: ignore
        except ImportError as exc:
            raise RuntimeError("wan renderer requires torch at render time") from exc
        prompt = str(shot.get("prompt", ""))
        generator = torch.Generator(device=self._device or self.config.get("device", "cpu")).manual_seed(int(seed))
        kwargs = dict(self.config.get("generation_kwargs") or {})
        for key in ("num_frames", "num_inference_steps", "guidance_scale", "height", "width", "negative_prompt"):
            if key in self.config and key not in kwargs:
                kwargs[key] = self.config[key]
        kwargs.setdefault(
            "num_frames",
            max(1, int(round(float(shot.get("duration", 2.0)) * float(self.config.get("fps", 8))))),
        )
        kwargs["generator"] = generator
        try:
            result = pipeline(prompt=prompt, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"Wan generation failed: {exc}") from exc
        frames = getattr(result, "frames", None)
        if frames is None:
            frames = getattr(result, "images", None)
        if frames is None:
            raise RuntimeError("Wan pipeline returned no frames or images")
        # Pipelines differ in whether frames are nested per batch; use the first item.
        if isinstance(frames, (list, tuple)) and frames and isinstance(frames[0], (list, tuple)):
            frames = frames[0]
        if not isinstance(frames, (list, tuple)) or not frames:
            raise RuntimeError("Wan pipeline returned an unsupported frame container")
        fps = float(self.config.get("fps", 8))
        with tempfile.TemporaryDirectory(prefix="wan_frames_", dir=str(destination.parent)) as temp_dir:
            frame_paths: list[Path] = []
            for index, frame in enumerate(frames):
                path = Path(temp_dir) / f"frame_{index:06d}.png"
                if hasattr(frame, "save"):
                    frame.save(path, format="PNG")
                else:
                    try:
                        from PIL import Image
                        Image.fromarray(frame).save(path, format="PNG")
                    except Exception as exc:
                        raise RuntimeError("Wan frame cannot be converted to PNG") from exc
                frame_paths.append(path)
            encode_images_to_video(frame_paths, destination, fps)
        info = probe_video(destination)
        return RenderedShot(
            shot_id=str(shot.get("id", "")), path=destination,
            duration=float(info["duration"]), cost=self.cost, seed=int(seed), attempt=int(attempt),
            metadata={"renderer": "wan", "sha256": sha256(destination), "fps": info["fps"]},
        )


def _normalise_fixture_entry(entry: dict, contract: dict, assets: dict[str, RenderedShot]) -> Evidence:
    constraint_id = str(entry.get("constraint_id", ""))
    constraints = {str(item.get("id")): item for item in contract.get("constraints", [])}
    constraint = constraints.get(constraint_id)
    if not constraint:
        return Evidence(constraint_id, "unknown", 0.0, [], [], "fixture references an unknown constraint", "scripted")
    scope = [str(x) for x in constraint.get("shot_ids", [])]
    shot_ids = [str(x) for x in entry.get("shot_ids", scope)]
    verdict = str(entry.get("verdict", "unknown"))
    confidence = float(entry.get("confidence", 0.0))
    spans = entry.get("spans") or []
    hashes = {str(k): str(v) for k, v in (entry.get("clip_hashes") or {}).items()}
    rationale = str(entry.get("rationale", "fixture evidence"))
    invalid = False
    if verdict not in {"support", "contradict", "unknown"} or not 0 <= confidence <= 1:
        invalid = True
    if any(x not in scope for x in shot_ids) or set(shot_ids) != set(scope):
        invalid = True
    if verdict != "unknown":
        if not spans or set(hashes) != set(scope):
            invalid = True
        for shot_id in scope:
            asset = assets.get(shot_id)
            try:
                actual_hash = sha256(asset.path) if asset is not None else None
            except (OSError, ValueError):
                actual_hash = None
            if asset is None or hashes.get(shot_id) != actual_hash:
                invalid = True
        for span in spans:
            try:
                span_shot = str(span["shot_id"])
                start, end = float(span["start"]), float(span["end"])
                asset = assets.get(span_shot)
                if span_shot not in scope or asset is None or not (math.isfinite(start) and math.isfinite(end)) or not (0 <= start < end <= float(asset.duration) + 1e-6):
                    invalid = True
            except (KeyError, TypeError, ValueError):
                invalid = True
    if invalid:
        return Evidence(constraint_id, "unknown", 0.0, scope, [], "fixture evidence failed hash, scope, or timestamp validation", "scripted")
    return Evidence(constraint_id, verdict, confidence, scope, spans, rationale, "scripted", hashes)


class ScriptedObserver:
    """Fixture-only observer for deterministic tests and protocol examples."""

    mode = "scripted"

    def __init__(self, config: dict):
        self.config = dict(config)
        if "evidence" not in self.config and "fixture" not in self.config and not self.config.get("fixture_file"):
            raise ValueError("scripted observer requires an explicit evidence fixture")
        if self.config.get("fixture_file"):
            fixture_path = Path(self.config["fixture_file"])
            if not fixture_path.is_file():
                raise FileNotFoundError(f"observer fixture does not exist: {fixture_path}")
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        elif "fixture" in self.config:
            payload = self.config["fixture"]
            if isinstance(payload, (str, Path)):
                fixture_path = Path(payload)
                if not fixture_path.is_file():
                    raise FileNotFoundError(f"observer fixture does not exist: {fixture_path}")
                payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        else:
            payload = self.config.get("evidence")
        if isinstance(payload, dict):
            payload = payload.get("evidence", [])
        if not isinstance(payload, list):
            raise ValueError("scripted evidence fixture must contain a list")
        self.entries = [dict(item) for item in payload if isinstance(item, dict)]

    def observe(self, contract: dict, assets: dict[str, RenderedShot]) -> list[Evidence]:
        entries = {str(entry.get("constraint_id")): entry for entry in self.entries}
        evidence: list[Evidence] = []
        for constraint in contract.get("constraints", []):
            cid = str(constraint.get("id", ""))
            if cid in entries:
                evidence.append(_normalise_fixture_entry(entries[cid], contract, assets))
            else:
                evidence.append(Evidence(cid, "unknown", 0.0, [str(x) for x in constraint.get("shot_ids", [])], [], "no fixture entry", "scripted"))
        return evidence


class VLMObserver:
    """Explicit extension point for a provider-backed visual-language observer."""

    mode = "vlm"

    def __init__(self, config: dict):
        self.config = dict(config)
        self.provider = self.config.get("provider")

    def observe(self, contract: dict, assets: dict[str, RenderedShot]) -> list[Evidence]:
        raise RuntimeError("VLM observer requires an explicit provider integration")


class ChatPlanner:
    """Planner with a deterministic local compiler and optional JSON endpoint."""

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.endpoint = self.config.get("endpoint") or self.config.get("base_url")
        self.model = self.config.get("model", "")
        self.timeout = float(self.config.get("timeout", 60))
        self.api_key_env = str(self.config.get("api_key_env", "OPENAI_API_KEY"))
        if self.timeout <= 0:
            raise ValueError("planner timeout must be positive")

    def compile(self, prompt: str) -> dict:
        prompt = str(prompt).strip()
        if not prompt:
            raise ValueError("prompt must be non-empty")
        if self.endpoint:
            return self._compile_remote(prompt)
        return self._compile_local(prompt)

    def _compile_remote(self, prompt: str) -> dict:
        body = {
            "model": self.model,
            "temperature": float(self.config.get("temperature", 0.0)),
            "messages": [
                {"role": "system", "content": "Return only a valid Contract2Video JSON contract."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        endpoint = str(self.endpoint)
        if self.config.get("base_url") and endpoint.rstrip("/").endswith("/v1"):
            endpoint = endpoint.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(endpoint, data=_json_compact(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"planner endpoint request failed: {exc}") from exc
        content = payload
        if isinstance(payload, dict) and payload.get("choices"):
            content = payload["choices"][0].get("message", {}).get("content", "")
        if isinstance(content, str):
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S)
            try:
                content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError("planner endpoint did not return JSON") from exc
        if not isinstance(content, dict):
            raise RuntimeError("planner endpoint returned an invalid contract")
        return content

    def _compile_local(self, prompt: str) -> dict:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        contract_id = f"contract-{digest}"
        # Keep a single deterministic shot: downstream planning may split it.
        shot_id = "shot_001"
        entity_id = "entity_001"
        return {
            "schema_version": "1.0",
            "contract_id": contract_id,
            "prompt": prompt,
            "entities": [{"id": entity_id, "description": prompt[:240]}],
            "shots": [{
                "id": shot_id, "prompt": prompt, "duration": float(self.config.get("duration", 2.0)),
                "dependencies": [], "entity_ids": [entity_id], "state_in": {}, "state_out": {},
            }],
            "constraints": [{
                "id": "constraint_001", "kind": "text", "description": f"The rendered shot follows the request: {prompt}",
                "shot_ids": [shot_id], "hardness": "hard", "weight": 1.0,
            }],
        }


def make_renderer(config: dict):
    config = dict(config or {})
    kind = config.get("type")
    if kind == "procedural":
        return ProceduralRenderer(config)
    if kind == "command":
        return CommandRenderer(config)
    if kind == "wan":
        return WanRenderer(config)
    raise ValueError("renderer.type must be one of: procedural, command, wan")


def make_observer(config: dict):
    config = dict(config or {})
    kind = config.get("type")
    if kind == "scripted":
        return ScriptedObserver(config)
    if kind == "vlm":
        return VLMObserver(config)
    raise ValueError("observer.type must be one of: scripted, vlm")


def make_planner(config: dict):
    config = dict(config or {})
    if config.get("type") != "chat":
        raise ValueError("planner.type must be chat")
    return ChatPlanner(config)

