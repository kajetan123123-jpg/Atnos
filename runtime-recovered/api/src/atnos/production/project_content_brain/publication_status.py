"""Status publikacji oddzielony od statusu produkcji."""
from __future__ import annotations

from typing import Any

from atnos.production.project_content_brain.types import PublicationStatus


PRODUCTION_TO_PUBLICATION: dict[str, PublicationStatus] = {
    "queued": "IN_PRODUCTION",
    "running": "IN_PRODUCTION",
    "blocked": "BLOCKED",
    "failed": "FAILED",
    "cancelled": "BLOCKED",
    "script_ready": "NEEDS_REVIEW",
    "ready": "NEEDS_REVIEW",  # gotowa produkcja ≠ zgoda na publikację
}


PUBLICATION_QUEUE_TO_STATUS: dict[str, PublicationStatus] = {
    "queued": "READY_TO_PUBLISH",
    "uploading": "READY_TO_PUBLISH",
    "uploaded_private": "APPROVED",
    "processing": "APPROVED",
    "scheduling": "READY_TO_PUBLISH",
    "scheduled": "READY_TO_PUBLISH",
    "published": "PUBLISHED",
    "retry_wait": "READY_TO_PUBLISH",
    "failed": "FAILED",
    "blocked": "BLOCKED",
    "paused": "BLOCKED",
}


def resolve_publication_status(
    *,
    production_status: str | None,
    delivery_mode: str | None = None,
    publication_queue_status: str | None = None,
    operator_approved: bool = False,
    content_plan_stage: str | None = None,
) -> dict[str, Any]:
    """Mapuj stany; READY produkcji nigdy nie oznacza automatycznie READY_TO_PUBLISH."""
    if delivery_mode == "local_only":
        return {
            "publication_status": "BLOCKED",
            "production_status": production_status,
            "ready_production_implies_publish": False,
            "local_only": True,
            "reason": "local_only pozostaje lokalne — brak zgody na publikację zewnętrzną.",
            "confidence": 1.0,
            "source": "delivery_mode",
        }

    if publication_queue_status:
        mapped = PUBLICATION_QUEUE_TO_STATUS.get(str(publication_queue_status), "NEEDS_REVIEW")
        return {
            "publication_status": mapped,
            "production_status": production_status,
            "ready_production_implies_publish": False,
            "local_only": False,
            "reason": f"Status z kolejki publikacji: {publication_queue_status}.",
            "confidence": 0.95,
            "source": "production_publication_queue",
        }

    if content_plan_stage in {"idea", "planned"}:
        stage_map = {"idea": "IDEA", "planned": "PLANNED"}
        return {
            "publication_status": stage_map[content_plan_stage],
            "production_status": production_status,
            "ready_production_implies_publish": False,
            "local_only": False,
            "reason": f"Etap planu treści: {content_plan_stage}.",
            "confidence": 0.9,
            "source": "content_plan",
        }

    prod = str(production_status or "")
    base = PRODUCTION_TO_PUBLICATION.get(prod, "IDEA")
    if prod == "ready" and operator_approved:
        return {
            "publication_status": "APPROVED",
            "production_status": production_status,
            "ready_production_implies_publish": False,
            "local_only": False,
            "reason": "Produkcja ready oraz jawna akceptacja operatora — nadal bez automatycznej publikacji.",
            "confidence": 0.9,
            "source": "production_status+operator_approved",
        }
    return {
        "publication_status": base,
        "production_status": production_status,
        "ready_production_implies_publish": False,
        "local_only": False,
        "reason": (
            "Status produkcji zmapowany na status publikacji; ready ≠ READY_TO_PUBLISH."
            if prod == "ready"
            else f"Mapowanie production_status={prod or 'unknown'}."
        ),
        "confidence": 0.85 if prod else 0.4,
        "source": "production_status",
    }
