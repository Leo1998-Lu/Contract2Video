"""Validation and normalization of the public contract schema.

The validator is deliberately strict at boundaries so later agents can rely on
resolved references and deterministic shot order.  It has no external
runtime dependencies.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "1.0"
CONSTRAINT_KINDS = {"entity", "attribute", "action", "spatial", "temporal", "identity", "audio", "text"}
HARDNESSES = {"hard", "soft"}


def _is_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _err(path: str, message: str) -> str:
    return f"{path}: {message}"


def validate_contract(contract: dict[str, Any], *, raise_on_error: bool = True) -> list[str]:
    """Return validation errors (or raise ``ValueError`` when requested)."""
    errors: list[str] = []
    if not isinstance(contract, dict):
        errors.append("contract: expected object")
        return _finish(errors, raise_on_error)
    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append(_err("schema_version", f"must be {SCHEMA_VERSION!r}"))
    for key in ("contract_id", "prompt"):
        if not _is_string(contract.get(key)):
            errors.append(_err(key, "must be a non-empty string"))
    entities = contract.get("entities")
    shots = contract.get("shots")
    constraints = contract.get("constraints")
    if not isinstance(entities, list):
        errors.append(_err("entities", "must be a list")); entities = []
    if not isinstance(shots, list):
        errors.append(_err("shots", "must be a list")); shots = []
    if not isinstance(constraints, list):
        errors.append(_err("constraints", "must be a list")); constraints = []

    entity_ids: set[str] = set()
    shot_ids: set[str] = set()
    constraint_ids: set[str] = set()
    all_ids: dict[str, str] = {}

    for i, entity in enumerate(entities):
        p = f"entities[{i}]"
        if not isinstance(entity, dict):
            errors.append(_err(p, "must be an object")); continue
        ident = entity.get("id")
        if not _is_string(ident):
            errors.append(_err(f"{p}.id", "must be a non-empty string")); continue
        if ident in entity_ids:
            errors.append(_err(f"{p}.id", "duplicate entity id"))
        entity_ids.add(ident)
        if ident in all_ids:
            errors.append(_err(f"{p}.id", f"id already used by {all_ids[ident]}"))
        else:
            all_ids[ident] = f"entity {ident}"
        if not _is_string(entity.get("description")):
            errors.append(_err(f"{p}.description", "must be a non-empty string"))

    for i, shot in enumerate(shots):
        p = f"shots[{i}]"
        if not isinstance(shot, dict):
            errors.append(_err(p, "must be an object")); continue
        ident = shot.get("id")
        if not _is_string(ident):
            errors.append(_err(f"{p}.id", "must be a non-empty string")); continue
        if ident in shot_ids:
            errors.append(_err(f"{p}.id", "duplicate shot id"))
        shot_ids.add(ident)
        if ident in all_ids:
            errors.append(_err(f"{p}.id", f"id already used by {all_ids[ident]}"))
        else:
            all_ids[ident] = f"shot {ident}"
        if not _is_string(shot.get("prompt")):
            errors.append(_err(f"{p}.prompt", "must be a non-empty string"))
        duration = shot.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            errors.append(_err(f"{p}.duration", "must be a finite number > 0"))
        elif duration != duration or duration in (float("inf"), float("-inf")):
            errors.append(_err(f"{p}.duration", "must be finite"))
        deps = shot.get("dependencies")
        if not isinstance(deps, list) or any(not _is_string(x) for x in deps):
            errors.append(_err(f"{p}.dependencies", "must be a list of string ids")); deps = []
        if len(set(deps)) != len(deps):
            errors.append(_err(f"{p}.dependencies", "contains duplicate ids"))
        for dep in deps:
            if dep not in shot_ids:
                errors.append(_err(f"{p}.dependencies", f"dependency {dep!r} must precede this shot"))
        entity_refs = shot.get("entity_ids")
        if not isinstance(entity_refs, list) or any(not _is_string(x) for x in entity_refs):
            errors.append(_err(f"{p}.entity_ids", "must be a list of string ids")); entity_refs = []
        for ent in entity_refs:
            if ent not in entity_ids:
                errors.append(_err(f"{p}.entity_ids", f"unknown entity {ent!r}"))
        for state_key in ("state_in", "state_out"):
            if not isinstance(shot.get(state_key), dict):
                errors.append(_err(f"{p}.{state_key}", "must be an object"))
        if "scene" in shot and not isinstance(shot["scene"], dict):
            errors.append(_err(f"{p}.scene", "must be an object when supplied"))

    # A second dependency pass catches forward references without accidentally
    # accepting a duplicate id from an earlier malformed item.
    order = {s.get("id"): i for i, s in enumerate(shots) if isinstance(s, dict) and _is_string(s.get("id"))}
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict) or not _is_string(shot.get("id")): continue
        for dep in shot.get("dependencies", []) if isinstance(shot.get("dependencies"), list) else []:
            if dep == shot.get("id"):
                errors.append(_err(f"shots[{i}].dependencies", "cannot depend on itself"))
            elif dep not in order:
                errors.append(_err(f"shots[{i}].dependencies", f"unknown dependency {dep!r}"))
            elif order[dep] >= i:
                errors.append(_err(f"shots[{i}].dependencies", f"dependency {dep!r} must precede this shot"))

    for i, con in enumerate(constraints):
        p = f"constraints[{i}]"
        if not isinstance(con, dict):
            errors.append(_err(p, "must be an object")); continue
        ident = con.get("id")
        if not _is_string(ident):
            errors.append(_err(f"{p}.id", "must be a non-empty string")); continue
        if ident in constraint_ids:
            errors.append(_err(f"{p}.id", "duplicate constraint id"))
        constraint_ids.add(ident)
        if ident in all_ids:
            errors.append(_err(f"{p}.id", f"id already used by {all_ids[ident]}"))
        else:
            all_ids[ident] = f"constraint {ident}"
        kind = con.get("kind")
        if kind not in CONSTRAINT_KINDS:
            errors.append(_err(f"{p}.kind", f"must be one of {sorted(CONSTRAINT_KINDS)}"))
        if not _is_string(con.get("description")):
            errors.append(_err(f"{p}.description", "must be a non-empty string"))
        scope = con.get("shot_ids")
        if not isinstance(scope, list) or not scope or any(not _is_string(x) for x in scope):
            errors.append(_err(f"{p}.shot_ids", "must be a non-empty list of string ids")); scope = []
        if len(set(scope)) != len(scope):
            errors.append(_err(f"{p}.shot_ids", "contains duplicate ids"))
        for sid in scope:
            if sid not in shot_ids:
                errors.append(_err(f"{p}.shot_ids", f"unknown shot {sid!r}"))
        weight = con.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            errors.append(_err(f"{p}.weight", "must be a finite number > 0"))
        elif weight != weight or weight in (float("inf"), float("-inf")):
            errors.append(_err(f"{p}.weight", "must be finite"))
        if con.get("hardness") not in HARDNESSES:
            errors.append(_err(f"{p}.hardness", "must be 'hard' or 'soft'"))

    return _finish(errors, raise_on_error)


def _finish(errors: list[str], raise_on_error: bool) -> list[str]:
    if errors and raise_on_error:
        raise ValueError("Invalid contract: " + "; ".join(errors))
    return errors


def normalize_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate then deep-copy a contract for deterministic serialization."""
    validate_contract(contract)
    return deepcopy(contract)


def contract_errors(contract: dict[str, Any]) -> list[str]:
    """Convenience non-raising validation API used by CLI and tests."""
    return validate_contract(contract, raise_on_error=False)
