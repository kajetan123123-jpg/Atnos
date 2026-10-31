"""Explicit scene meaning contracts — narration → roles → success criterion.

Planners and renderers must honor direction/object roles; never reverse a transfer
because both account names appear in the sentence.
"""

from __future__ import annotations

from typing import Any


_FORBIDDEN_BY_KIND: dict[str, list[str]] = {
    "reserve_readiness": ["invented prices, runway months or allocation proportions",
                           "a completed transfer or guaranteed immediate access",
                           "investment return portrayed as guaranteed"],
    "transfer": [
        "destination shown as source",
        "arrow reversed against spoken from→to",
        "single unlabeled coin with no A→B path",
    ],
    "transfer_forbidden": [
        "money completing a transfer",
        "arrow arriving at destination",
    ],
    "raise_split": [
        "single linear box chain presented as the whole story",
        "raise absorbed entirely into spending with no keep branch",
        "one icon standing in for a fork",
    ],
    "notebook_log": [
        "generic transfer still without a notebook or ledger",
        "icon labeled 'habit' with no visible entry",
    ],
    "habit_repeat": [
        "single set-aside icon with no second cycle",
        "decorative repeat glyph without accumulation",
    ],
}


def build_scene_meaning_contract(
    *,
    scene_id: str,
    narration: str,
    relation: dict[str, Any] | None,
    concept: str = "",
) -> dict[str, Any]:
    """Build the minimum meaning contract every scene must carry into QA."""
    rel = relation if isinstance(relation, dict) else {}
    kind = str(rel.get("kind") or "")
    objects_raw = rel.get("objects") if isinstance(rel.get("objects"), list) else []
    objects: list[str] = []
    object_roles: dict[str, str] = {}
    for item in objects_raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        label = str(item.get("label") or role).strip()
        if not label:
            continue
        objects.append(label)
        if role:
            object_roles[role] = label
    if rel.get("source") and "source" not in object_roles:
        object_roles["source"] = str(rel["source"])
        if str(rel["source"]) not in objects:
            objects.append(str(rel["source"]))
    if rel.get("destination") and "destination" not in object_roles:
        object_roles["destination"] = str(rel["destination"])
        if str(rel["destination"]) not in objects:
            objects.append(str(rel["destination"]))

    direction = rel.get("direction")
    forbidden = list(_FORBIDDEN_BY_KIND.get(kind, []))
    if kind == "transfer" and direction:
        forbidden.append(f"any arrow that is not {direction}")
    if concept == "notebook_log" or kind == "notebook_log":
        forbidden.append("raise-split still staged as the notebook beat")

    return {
        "scene_id": scene_id,
        "narration": str(narration or "")[:2000],
        "viewer_should_understand": str(rel.get("viewer_should_understand") or "")[:240],
        "objects": objects[:8],
        "object_roles": object_roles,
        "start_state": str(rel.get("initial_state") or "")[:240],
        "action": str(rel.get("action") or "")[:80],
        "direction": str(direction) if direction else "",
        "condition": str(rel.get("condition") or "")[:120],
        "end_state": str(rel.get("final_state") or "")[:240],
        "success_criterion": str(rel.get("compliance_criterion") or "")[:240],
        "forbidden_interpretations": forbidden[:8],
        "relation_kind": kind or None,
        "unsupported_gap": rel.get("unsupported_gap"),
    }


def required_interaction_for_contract(contract: dict[str, Any] | None) -> str | None:
    """What the asset/renderer must be able to show for this beat."""
    if not isinstance(contract, dict):
        return None
    kind = str(contract.get("relation_kind") or "")
    return {
        "transfer": "source_to_destination_motion",
        "transfer_forbidden": "blocked_transfer_path",
        "raise_split": "branching_split",
        "notebook_log": "notebook_entry_write",
        "habit_repeat": "multi_cycle_repeat",
    }.get(kind)
