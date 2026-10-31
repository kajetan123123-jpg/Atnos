"""ATNOS Local Brain v1 — experimental operator brain (non-production chat path).

Does not change production Chat / Ollama defaults or cloud policy.
"""

from __future__ import annotations

from atnos.local_brain.readiness import local_brain_readiness_report
from atnos.local_brain.role_contracts import (
    BrainModelRole,
    ModelRoleContract,
    ROLE_CONTRACTS,
    get_role_contract,
)

__all__ = [
    "BrainModelRole",
    "ModelRoleContract",
    "ROLE_CONTRACTS",
    "get_role_contract",
    "local_brain_readiness_report",
]
