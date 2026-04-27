"""
Read-only endpoints for inspecting the policy engine's research traceability.
"""
from fastapi import APIRouter

from src.policy import evidence

router = APIRouter(prefix="/policy", tags=["Policy"])


@router.get("/evidence")
def get_evidence_registry() -> dict:
    """Return the canonical citations + per-component bindings.

    Used at thesis-time to demonstrate that every deterministic component is
    grounded in a research source. No auth required — public, read-only.
    """
    return evidence.to_dict()
