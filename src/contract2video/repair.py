"""Dependency-aware local repair selection."""
from __future__ import annotations
from collections import defaultdict, deque
from typing import Any, Iterable


def affected_shots(contract: dict[str, Any], constraint_ids: Iterable[str]) -> list[str]:
    """Return violated/unknown scopes plus all transitive dependents in order."""
    shots = [s for s in contract.get("shots", []) if isinstance(s, dict) and isinstance(s.get("id"), str)]
    order = {s["id"]: i for i, s in enumerate(shots)}
    by_id = {s["id"]: s for s in shots}
    requested = {constraint_ids} if isinstance(constraint_ids, str) else set(constraint_ids)
    constraints = {c.get("id"): c for c in contract.get("constraints", []) if isinstance(c, dict)}
    roots: set[str] = set()
    for cid in requested:
        con = constraints.get(cid)
        if con:
            roots.update(x for x in con.get("shot_ids", []) if x in by_id)
    dependents: dict[str, list[str]] = defaultdict(list)
    for shot in shots:
        for dep in shot.get("dependencies", []) if isinstance(shot.get("dependencies"), list) else []:
            if dep in by_id:
                dependents[dep].append(shot["id"])
    seen = set(roots)
    queue = deque(roots)
    while queue:
        sid = queue.popleft()
        for child in dependents.get(sid, []):
            if child not in seen:
                seen.add(child); queue.append(child)
    return sorted(seen, key=lambda sid: order[sid])
