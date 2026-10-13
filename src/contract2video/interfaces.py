"""Typed boundaries between planning, generation, observation, and control."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

@dataclass
class RenderedShot:
    shot_id: str
    path: Path
    duration: float
    cost: float = 0.0
    seed: int = 0
    attempt: int = 0
    metadata: dict = field(default_factory=dict)

@dataclass
class Evidence:
    constraint_id: str
    verdict: str
    confidence: float
    shot_ids: list[str]
    spans: list[dict]
    rationale: str
    source: str
    clip_hashes: dict[str, str] = field(default_factory=dict)

class Renderer(Protocol):
    def render(self, contract: dict, shot: dict, target: Path, *, seed: int,
               attempt: int, feedback: list[dict], references: list[Path]) -> RenderedShot: ...

class Observer(Protocol):
    def observe(self, contract: dict, assets: dict[str, RenderedShot]) -> list[Evidence]: ...

class Planner(Protocol):
    def compile(self, prompt: str) -> dict: ...
