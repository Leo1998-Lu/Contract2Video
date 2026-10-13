"""Small, deterministic media helpers used by the adapters.

The module deliberately keeps media handling in subprocesses and never invokes a
shell.  This makes paths and user supplied prompts safe to pass through the
rendering workflow while keeping the dependency footprint small.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


def _require_file(path: Path) -> Path:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"media file does not exist: {path}")
    return path


def _assert_new_target(target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {target}")
    return target


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of *path* using bounded memory."""
    path = _require_file(Path(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ffprobe(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is required to inspect video files") from exc
    if result.returncode != 0:
        raise ValueError(f"ffprobe could not inspect {path}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned malformed metadata for {path}") from exc
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"no video stream found in {path}")

    def number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    duration = number(video.get("duration"), number((payload.get("format") or {}).get("duration")))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator or 1)
    except (AttributeError, ValueError, ZeroDivisionError):
        fps = 0.0
    if not math.isfinite(duration) or not math.isfinite(fps) or duration <= 0 or width <= 0 or height <= 0 or fps <= 0:
        raise ValueError(f"video metadata is incomplete or non-positive for {path}")
    return {"duration": duration, "width": width, "height": height, "fps": fps}


def probe_video(path: Path) -> dict:
    """Probe a regular video and return normalized duration, size, and FPS."""
    return _ffprobe(_require_file(Path(path)))


def sample_frames(path: Path, count: int, output_dir: Path) -> list[tuple[float, Path]]:
    """Extract evenly spaced PNG frames and return ``(seconds, path)`` pairs."""
    if int(count) <= 0:
        raise ValueError("count must be positive")
    source = _require_file(Path(path))
    info = probe_video(source)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # A midpoint avoids seeking past the end on very short clips.
    duration = float(info["duration"])
    timestamps = [duration * (i + 0.5) / int(count) for i in range(int(count))]
    outputs: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(timestamps):
        output = directory / f"frame_{index:05d}.png"
        if output.exists():
            raise FileExistsError(f"refusing to overwrite sampled frame: {output}")
        command = [
            "ffmpeg", "-v", "error", "-ss", f"{max(0.0, min(timestamp, duration - 1e-6)):.6f}",
            "-i", str(source), "-frames:v", "1", "-pix_fmt", "rgb24", str(output),
        ]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required to sample video frames") from exc
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(f"ffmpeg could not sample {source}: {result.stderr.strip()}")
        outputs.append((max(0.0, min(timestamp, duration)), output))
    return outputs


def compose_videos(paths: list[Path], target: Path) -> None:
    """Concatenate video files into *target* with a deterministic re-encode.

    The concat filter normalizes minor differences between source codecs while
    preserving source files.  The target is written through a sibling temporary
    file and atomically renamed only after ffmpeg succeeds.
    """
    if not paths:
        raise ValueError("at least one source video is required")
    sources = [_require_file(Path(path)) for path in paths]
    destination = _assert_new_target(Path(target))
    # Probe up front so malformed sources are rejected before creating output.
    infos = [probe_video(path) for path in sources]
    # H.264 4:2:0 output requires an even canvas; round only the composed
    # canvas so a source clip's own dimensions remain unchanged.
    width = int(infos[0]["width"]) + int(infos[0]["width"]) % 2
    height = int(infos[0]["height"]) + int(infos[0]["height"]) % 2
    fps = infos[0]["fps"]
    # The filter scales all clips to a common canvas and fixes frame rate. This
    # is intentionally explicit instead of relying on concat's implicit rules.
    filter_parts = []
    labels = []
    for index in range(len(sources)):
        label = f"v{index}"
        filter_parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps:.6f},format=yuv420p[{label}]"
        )
        labels.append(f"[{label}]")
    filter_parts.append("".join(labels) + f"concat=n={len(sources)}:v=1:a=0[outv]")
    with tempfile.NamedTemporaryFile(prefix="compose_", suffix=".mp4", dir=str(destination.parent), delete=False) as handle:
        temporary = Path(handle.name)
    command = ["ffmpeg", "-v", "error"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend([
        "-filter_complex", ";".join(filter_parts), "-map", "[outv]",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-y", str(temporary),
    ])
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg could not compose videos: {result.stderr.strip()}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    # Ensure a successful command actually produced a readable video.
    probe_video(destination)

