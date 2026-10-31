from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["remote-nodes"])


def _load_remote_node_service_dependencies():
    from atnos.services.remote_node_service import RemoteBootstrapRequest, RemoteNodeError, RemoteNodeService

    return RemoteBootstrapRequest, RemoteNodeError, RemoteNodeService


def _load_remote_node_hub_error():
    from atnos.services.remote_node_hub import RemoteNodeHubError

    return RemoteNodeHubError


def _coerce_remote_execution_origin(value: str):
    from atnos.db.models.enums import RemoteExecutionOrigin

    return RemoteExecutionOrigin(value)


class RemoteBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoteNodeBootstrapRequestModel(RemoteBaseModel):
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="atnos", min_length=1, max_length=120)
    private_key_path: str = Field(min_length=1, max_length=1024)
    control_plane_base_url: str = Field(min_length=1, max_length=255)
    label: str | None = Field(default=None, max_length=120)


class RemoteNodeEnrollRequestModel(RemoteBaseModel):
    node_id: int = Field(gt=0)
    enrollment_token: str = Field(min_length=1, max_length=512)
    hostname: str | None = Field(default=None, max_length=255)
    public_capabilities_json: dict[str, object] | None = None
    cert_fingerprint: str | None = Field(default=None, max_length=255)


class RemoteNodeRotateCredentialsRequestModel(RemoteBaseModel):
    node_id: int = Field(gt=0)
    credential_token: str = Field(min_length=1, max_length=512)


class RemoteExecutionDispatchRequestModel(RemoteBaseModel):
    task_kind: str = Field(min_length=1, max_length=120)
    payload: dict[str, object] = Field(default_factory=dict)
    preferred_node_id: int | None = Field(default=None, gt=0)
    origin: str = Field(default="manual", pattern="^(manual|scheduler|trusted_auto|system)$")


@router.post("/remote-nodes/bootstrap")
def bootstrap_remote_node(
    request: Request,
    payload: RemoteNodeBootstrapRequestModel,
) -> dict[str, object]:
    RemoteBootstrapRequest, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).bootstrap_node(
                RemoteBootstrapRequest(
                    host=payload.host.strip(),
                    ssh_port=payload.ssh_port,
                    user=payload.user.strip(),
                    private_key_path=Path(payload.private_key_path).expanduser(),
                    control_plane_base_url=payload.control_plane_base_url.strip().rstrip("/"),
                    label=payload.label.strip() if payload.label is not None else None,
                )
            )
    except (RemoteNodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/remote-nodes/enroll")
def enroll_remote_node(
    request: Request,
    payload: RemoteNodeEnrollRequestModel,
) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).enroll(
                node_id=payload.node_id,
                enrollment_token=payload.enrollment_token,
                hostname=payload.hostname,
                public_capabilities_json=payload.public_capabilities_json,
                cert_fingerprint=payload.cert_fingerprint,
            )
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/remote-nodes/rotate-credentials")
def rotate_remote_node_credentials(
    request: Request,
    payload: RemoteNodeRotateCredentialsRequestModel,
) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).rotate_credentials(
                node_id=payload.node_id,
                credential_token=payload.credential_token,
            )
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/remote-nodes")
def list_remote_nodes(request: Request) -> dict[str, object]:
    _, _, RemoteNodeService = _load_remote_node_service_dependencies()
    with request.app.state.database.session_scope() as session:
        return RemoteNodeService(session, request.app.state.settings).list_nodes_payload()


@router.get("/remote-nodes/{node_id}")
def get_remote_node(node_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).get_node_payload(node_id)
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/remote-nodes/{node_id}/pause")
def pause_remote_node(node_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).pause_node(node_id)
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/remote-nodes/{node_id}/resume")
def resume_remote_node(node_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).resume_node(node_id)
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/remote-nodes/{node_id}/revoke")
def revoke_remote_node(node_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            return RemoteNodeService(session, request.app.state.settings).revoke_node(node_id)
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/remote-nodes/{node_id}/capabilities")
def get_remote_node_capabilities(node_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            payload = RemoteNodeService(session, request.app.state.settings).get_node_payload(node_id)
        worker = payload["remote_worker"]
        return {
            "node_id": node_id,
            "capability_snapshot": worker.get("capability_snapshot") if isinstance(worker, dict) else None,
            "public_capabilities": worker.get("public_capabilities") if isinstance(worker, dict) else None,
        }
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/remote-nodes/{node_id}/runs")
def get_remote_node_runs(
    node_id: int,
    request: Request,
    limit: int = Query(default=12, ge=1, le=100),
) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    try:
        with request.app.state.database.session_scope() as session:
            payload = RemoteNodeService(session, request.app.state.settings).get_node_payload(node_id)
        worker = payload["remote_worker"]
        return {
            "node_id": node_id,
            "runs": worker.get("recent_runs") if isinstance(worker, dict) else [],
        }
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/remote-execution/dispatch")
async def dispatch_remote_execution(
    request: Request,
    payload: RemoteExecutionDispatchRequestModel,
) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    RemoteNodeHubError = _load_remote_node_hub_error()
    try:
        with request.app.state.database.session_scope() as session:
            serialized_run, dispatch_frame = RemoteNodeService(
                session,
                request.app.state.settings,
            ).dispatch_run(
                task_kind=payload.task_kind,
                payload=payload.payload,
                preferred_node_id=payload.preferred_node_id,
                origin=_coerce_remote_execution_origin(payload.origin),
            )
        if dispatch_frame is not None:
            node_id = serialized_run.get("assigned_node_id")
            if not isinstance(node_id, int):
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Remote run has no node.")
            await request.app.state.remote_node_hub.send(node_id, dispatch_frame)
        return {"remote_execution_run": serialized_run}
    except RemoteNodeHubError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/remote-execution/{run_id}/cancel")
async def cancel_remote_execution(run_id: int, request: Request) -> dict[str, object]:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    RemoteNodeHubError = _load_remote_node_hub_error()
    try:
        with request.app.state.database.session_scope() as session:
            payload = RemoteNodeService(session, request.app.state.settings).cancel_run(run_id)
        run = payload["remote_execution_run"]
        if isinstance(run, dict) and isinstance(run.get("assigned_node_id"), int):
            try:
                await request.app.state.remote_node_hub.send(
                    int(run["assigned_node_id"]),
                    {"type": "run.cancel", "payload": {"run_id": run_id}},
                )
            except RemoteNodeHubError:
                pass
        return payload
    except RemoteNodeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.websocket("/remote-nodes/ws/{node_id}")
async def remote_node_websocket(websocket: WebSocket, node_id: int, token: str = Query(min_length=1)) -> None:
    _, RemoteNodeError, RemoteNodeService = _load_remote_node_service_dependencies()
    await websocket.accept()
    try:
        with websocket.app.state.database.session_scope() as session:
            service = RemoteNodeService(session, websocket.app.state.settings)
            service.authorize_connection(node_id=node_id, credential_token=token)
            hello = service.register_connection(
                node_id=node_id,
                remote_addr=websocket.client.host if websocket.client is not None else None,
            )
        await websocket.send_json({"type": "connection.accepted", "payload": hello})

        async def _handle_message(current_node_id: int, frame: dict[str, object]) -> None:
            with websocket.app.state.database.session_scope() as session:
                service = RemoteNodeService(session, websocket.app.state.settings)
                service.handle_worker_frame(current_node_id, frame)

        await websocket.app.state.remote_node_hub.receive_loop(
            node_id,
            websocket,
            handler=_handle_message,
        )
    except RemoteNodeError:
        await websocket.close(code=4403)
    except WebSocketDisconnect:
        return
    finally:
        with websocket.app.state.database.session_scope() as session:
            RemoteNodeService(session, websocket.app.state.settings).disconnect(node_id=node_id)
