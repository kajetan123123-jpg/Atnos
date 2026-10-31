from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from atnos.api.routes.local_host import require_local_capability
from atnos.production.publication_coordinator import PublicationCoordinator, PublicationPolicy

router = APIRouter(prefix="/api/production/publication", tags=["production"])


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: PublicationPolicy
    approve_scheduled_publication: bool = False


def coordinator(request: Request):
    return PublicationCoordinator(request.app.state.settings, request.app.state.database)


@router.get("")
def publication_status(request: Request, job_id: str | None = None):
    from atnos.services.scheduler_runtime_service import get_scheduler_runtime_status
    runtime = get_scheduler_runtime_status()
    return {**coordinator(request).status(job_id), "runtime": {
        "running": runtime["production_publication_automatic"],
        "last_tick": runtime["production_publication_last_tick"],
        "error": runtime["production_publication_last_error"],
    }}


@router.put("/policy", dependencies=[Depends(require_local_capability)])
def set_publication_policy(request: Request, payload: PolicyRequest):
    if payload.policy.enabled and not payload.approve_scheduled_publication:
        raise HTTPException(409, "Wymagana zgoda na publiczna publikacje wedlug harmonogramu.")
    return coordinator(request).configure(payload.policy)


@router.post("/verify", dependencies=[Depends(require_local_capability)])
def verify_publication_channel(request: Request):
    return coordinator(request).verify_connection()


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(require_local_capability)])
def retry_publication(request: Request, job_id: str):
    # This only re-arms a known, already accepted upload. An unknown insert is not retried.
    from atnos.intelligence.storage import connect
    service = coordinator(request)
    with connect(service.settings) as db:
        row = db.execute("SELECT status FROM production_publication_queue WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Brak publikacji w kolejce.")
        if row["status"] not in {"blocked_user_action", "blocked_technical"}:
            raise HTTPException(409, "Ta publikacja nie wymaga bezpiecznego wznowienia.")
        db.execute("UPDATE production_publication_queue SET status='retry_wait',attempts=0,error=NULL,next_check_at=? WHERE job_id=? AND lease_token IS NULL", (service.clock().isoformat(), job_id))
        db.commit()
    return service.status(job_id)
