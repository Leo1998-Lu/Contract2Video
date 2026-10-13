"""Deterministic local renderer for examples and reproducible development runs."""
from __future__ import annotations

import hashlib
import math
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from .interfaces import RenderedShot
from .media import probe_video, sha256


def _new_target(target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {target}")
    return target


def _seed_colour(seed: int, shot_id: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(f"{seed}:{shot_id}".encode("utf-8")).digest()
    return (40 + digest[0] % 150, 40 + digest[1] % 150, 40 + digest[2] % 150)


def _font(size: int):
    # Pillow's built-in font is available on every supported installation.
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def encode_images_to_video(frames: list[Path], target: Path, fps: float) -> None:
    """Encode a sequence of PNG frames as an H.264 MP4 without overwriting."""
    if not frames:
        raise ValueError("at least one frame is required")
    target = _new_target(Path(target))
    fps = float(fps)
    if fps <= 0:
        raise ValueError("fps must be positive")
    with tempfile.NamedTemporaryFile(prefix="render_", suffix=".mp4", dir=str(target.parent), delete=False) as handle:
        temporary = Path(handle.name)
    # The image2 demuxer reads a fixed sequence and does not execute filenames.
    command = [
        "ffmpeg", "-v", "error", "-framerate", f"{fps:.8f}", "-i",
        str(frames[0].parent / "frame_%06d.png"), "-frames:v", str(len(frames)),
        "-c:v", "libx264", "-pix_fmt", "yuv444p", "-movflags", "+faststart",
        "-y", str(temporary),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg could not encode procedural video: {result.stderr.strip()}")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    probe_video(target)


class ProceduralRenderer:
    """Render a simple geometric clip entirely locally.

    This renderer is intentionally modest: its purpose is reproducible pipeline
    validation, not perceptual benchmarking.  It still emits a valid MP4 so all
    observers and evaluators exercise the same media interfaces as external
    generators.
    """

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.width = int(self.config.get("width", 512))
        self.height = int(self.config.get("height", 288))
        self.fps = float(self.config.get("fps", 12))
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("procedural width, height, and fps must be positive")
        self.cost = float(self.config.get("cost", 0.0))
        self.estimated_cost = float(self.config.get("estimated_cost", self.cost))
        if not math.isfinite(self.cost) or not math.isfinite(self.estimated_cost) or self.cost < 0 or self.estimated_cost < 0:
            raise ValueError("renderer cost cannot be negative")

    def estimate_cost(self, contract: dict, shot: dict) -> float:
        return self.estimated_cost

    def render(self, contract: dict, shot: dict, target: Path, *, seed: int,
               attempt: int, feedback: list[dict], references: list[Path]) -> RenderedShot:
        target = _new_target(Path(target))
        shot_id = str(shot.get("id", ""))
        if not shot_id:
            raise ValueError("shot must have a non-empty id")
        duration = float(shot.get("duration", self.config.get("duration", 2.0)))
        if duration <= 0:
            raise ValueError("shot duration must be positive")
        frame_count = max(1, int(round(duration * self.fps)))
        rng = random.Random((int(seed) * 1_000_003) ^ (int(attempt) * 97))
        base = _seed_colour(int(seed), shot_id)
        prompt = str(shot.get("prompt", ""))
        with tempfile.TemporaryDirectory(prefix="frames_", dir=str(target.parent)) as temp_dir:
            frame_paths: list[Path] = []
            font = _font(max(14, self.width // 32))
            small_font = _font(max(10, self.width // 48))
            for frame_index in range(frame_count):
                image = Image.new("RGB", (self.width, self.height), base)
                draw = ImageDraw.Draw(image)
                phase = frame_index / max(1, frame_count - 1)
                # Deterministic moving geometry makes temporal checks meaningful.
                x = int((self.width - 80) * phase + 20)
                y = int(self.height * 0.55 + 30 * (phase - 0.5))
                jitter = rng.randrange(-2, 3)
                draw.ellipse((x + jitter, y - 28, x + 56 + jitter, y + 28), fill=(235, 235, 245), outline=(20, 20, 35), width=2)
                title = f"{shot_id}  |  attempt {int(attempt)}"
                draw.text((18, 14), title, fill=(250, 250, 250), font=font)
                short_prompt = " ".join(prompt.split())[:100]
                draw.text((18, self.height - 34), short_prompt, fill=(230, 230, 240), font=small_font)
                if feedback:
                    draw.rectangle((self.width - 155, 14, self.width - 18, 38), fill=(20, 20, 35))
                    draw.text((self.width - 145, 19), "repair pass", fill=(250, 220, 120), font=small_font)
                frame = Path(temp_dir) / f"frame_{frame_index:06d}.png"
                image.save(frame, format="PNG", optimize=False)
                frame_paths.append(frame)
            # Encode before leaving TemporaryDirectory.
            encode_images_to_video(frame_paths, target, self.fps)
        info = probe_video(target)
        return RenderedShot(
            shot_id=shot_id, path=target, duration=float(info["duration"]),
            cost=self.cost, seed=int(seed), attempt=int(attempt),
            metadata={"renderer": "procedural", "sha256": sha256(target), "fps": info["fps"]},
        )

