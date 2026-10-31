from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from atnos.production.content_loop import AutonomousContentLoopService
from atnos.production.cost_guard import CostGuardBlockedError
from atnos.production.distribution_planner import (
    DistributionPlanError,
    build_distribution_plan,
    build_distribution_release_package,
)
from atnos.production.faceless_playbook import build_faceless_playbook_for_project
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.job_recovery import ProductionJobRecoveryService
from atnos.production.money_machine import (
    MoneyMachineActionService,
    MoneyMachineStatusService,
    build_money_machine_operator_summary,
)
from atnos.production.money_machine_strategy import MoneyMachineStrategyService
from atnos.production.money_machine_remediation import MoneyMachineRemediationService
from atnos.production.private_upload import (
    ProductionUploadError,
    run_analytics_followup,
    run_due_analytics_followups,
    upload_private_from_package,
)
from atnos.production.operating_standards import build_operating_standards
from atnos.production.public_quality_gate import build_public_quality_gate_status
from atnos.production.publication import (
    PUBLIC_PROMOTION_READY_STATUSES,
    ProductionPublicationError,
    promote_private_job_to_public,
    public_promotion_blockers,
)
from atnos.production.publication_quality import public_quality_evidence
from atnos.production.quality_remediation import ProductionQualityRemediationService
from atnos.production.quality_scheduler import ProductionQualityScheduler, QUALITY_REVIEW_BLOCKED
from atnos.production.provider_doctor import (
    ProviderDoctorActionError,
    build_provider_doctor_status,
    provider_doctor_action_by_id,
)
from atnos.production.regeneration import prepare_regeneration_job
from atnos.production.readiness import ShortProductionReadinessService
from atnos.production.visual_backend_doctor import run_visual_backend_smoke_render, write_visual_backend_doctor_report
from atnos.production.visual_backend_status import build_visual_backend_status
from atnos.production.voice_provider_doctor import write_voice_provider_doctor_report
from atnos.production.voice_provider_status import build_voice_provider_status
from atnos.intelligence.storage import connect, json_loads
from atnos.memory.obsidian_style_guide import ensure_channel_style_guide
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier
from atnos.services.shorts_onboarding_service import ShortsOnboardingService
from atnos.services.production_preflight_service import ProductionPreflightService
from atnos.services.production_delegation_service import ProductionDelegationService
from atnos.services.production_runtime_preparation_service import ProductionRuntimePreparationService
from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.learning.reference_learning_service import ReferenceLearningService
from atnos.production.order_dispatch import ProductionOrderDispatcher, order_status
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import ProductionOrder
from atnos.production.order_feedback import ProductionFeedback, ProductionFeedbackStore
from atnos.production.asset_brain.approval import character_requires_contact_sheet_review
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
from atnos.production.comfy_channels.profiles import generated_root
from atnos.production.studio_script_recovery import StudioScriptRepair, StudioCandidateAssessment
from atnos.production.studio_renderer_recovery import StudioRendererRepair

router = APIRouter(prefix="/api/production", tags=["production"])


@router.get("/workspace")
def get_production_workspace(request: Request) -> dict[str, Any]:
    from atnos.production.workspace_catalog import build_workspace_catalog
    return build_workspace_catalog(request.app.state.settings)


@router.get("/image-options")
def get_production_image_options(request: Request) -> dict[str, Any]:
    from atnos.production.order_image_setup import image_options
    return image_options(request.app.state.settings)


@router.get('/voice-candidates/{candidate_id}/sample')
def get_production_voice_sample(candidate_id: Literal['Ryan-B', 'Ryan-neutral']):
    from atnos.production.local_voice_lane import intake_voice_sample, LocalVoiceLaneError
    try:
        sample = intake_voice_sample() if candidate_id == 'Ryan-B' else intake_voice_sample(candidate_id)
        return FileResponse(sample['sample_path'], media_type='audio/wav',
            filename=candidate_id+'.wav', content_disposition_type='inline', headers={'Cache-Control': 'no-store'})
    except (OSError, LocalVoiceLaneError, ValueError) as exc:
        raise HTTPException(status_code=409, detail='Próbka wybranego głosu jest niedostępna lub zmieniona.') from exc


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
def create_production_order(request: Request, payload: ProductionOrder) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        payload.validate_automatic_submission()
        from atnos.production.local_voice_lane import validate_intake_voice_selection, register_intake_voice_lane, LocalVoiceLaneError
        try:
            voice_selection = validate_intake_voice_selection(settings, payload)
        except LocalVoiceLaneError as exc:
            raise ValueError(str(exc)) from exc
        from atnos.production.order_image_setup import register_order_image_lane, validate_image_selection

        # Resolve and validate the closed server-side reference before creating
        # any durable order. The lane must be bound before dispatch can start.
        reference_path = validate_image_selection(settings, payload)
        order = ProductionOrderStore(settings).create(payload, request_key=request.headers.get("Idempotency-Key"))
        if voice_selection is not None:
            try:
                register_intake_voice_lane(settings, order['id'], voice_selection)
            except LocalVoiceLaneError as exc:
                raise ValueError(str(exc)) from exc
        if reference_path is not None:
            register_order_image_lane(settings, order["id"], reference_path)
        return ProductionOrderDispatcher(settings).dispatch(order["id"])
    except (OrderConflict, ProductionBlocked) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/orders")
def list_production_orders(request: Request, limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    return {"orders": ProductionOrderDispatcher(request.app.state.settings).list_orders(limit=limit)}


@router.get("/orders/{job_id}")
def get_production_order(request: Request, job_id: str) -> dict[str, Any]:
    try:
        from atnos.production.order_recovery import recover_interrupted_orders

        recover_interrupted_orders(request.app.state.settings, job_ids=[job_id])
        from atnos.production.local_image_dispatch_budget import local_image_action_summary
        return {**order_status(ProductionOrderStore(request.app.state.settings).get(job_id)),
                'local_image_action': local_image_action_summary(request.app.state.settings, job_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc


@router.get('/orders/{job_id}/recovery')
def get_production_order_recovery(request: Request, job_id: str, response: Response) -> dict[str, Any]:
    from atnos.production.studio_script_recovery import recovery_snapshot
    response.headers['Cache-Control'] = 'no-store'
    try:
        return recovery_snapshot(ProductionOrderStore(request.app.state.settings), job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc


@router.get('/orders/{job_id}/presentation-repair')
def get_studio_presentation_repair(request:Request,job_id:str,response:Response):
    from atnos.production.studio_renderer_recovery import presentation_snapshot
    response.headers['Cache-Control']='no-store'
    try:return presentation_snapshot(ProductionOrderStore(request.app.state.settings),job_id)
    except KeyError as exc:raise HTTPException(status_code=404,detail='Nie znaleziono produkcji.') from exc


@router.post('/orders/{job_id}/presentation-repair',status_code=202)
async def apply_studio_presentation_repair(request:Request,job_id:str,payload:StudioRendererRepair):
    from atnos.production.studio_renderer_recovery import register_studio_presentation
    from atnos.production.remotion_quality_repair import _latest_refresh
    store=ProductionOrderStore(request.app.state.settings)
    try:
        registration=await register_studio_presentation(store,job_id,payload)
        state=store.get(job_id)
        latest=_latest_refresh(state)
        pending=(state['status']=='blocked' and latest and latest['output']['digest']==registration['request_digest']
                 and 'renderer_quality_refresh_requested' in str(state.get('error')))
        result=(ProductionOrderDispatcher(request.app.state.settings).dispatch(job_id)
            if not registration.get('already_registered') or pending else {**order_status(state),'worker_requested':False})
        return {**result,'presentation_request_key':payload.request_key}
    except KeyError as exc:raise HTTPException(status_code=404,detail='Nie znaleziono produkcji.') from exc
    except (OrderConflict,ProductionBlocked,ValueError,TimeoutError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/orders/{job_id}/reconcile-local-operation')
def reconcile_production_local_operation(request: Request, job_id: str) -> dict[str, Any]:
    from atnos.production.wb_local_flux_trial import reconcile_interrupted_flux_trial
    try:
        reconcile_interrupted_flux_trial(request.app.state.settings, job_id)
        return {**order_status(ProductionOrderStore(request.app.state.settings).get(job_id)), 'worker_requested':False}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc
    except (OrderConflict, ProductionBlocked, ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/orders/{job_id}/script-repair', status_code=status.HTTP_202_ACCEPTED)
def repair_production_order_script(request: Request, job_id: str, payload: StudioScriptRepair) -> dict[str, Any]:
    from atnos.production.studio_script_recovery import recovery_snapshot, register_script_repair
    store = ProductionOrderStore(request.app.state.settings)
    try:
        registration = register_script_repair(store, job_id, payload)
        if registration['already_registered'] and not registration.get('pending_dispatch'):
            result = {**order_status(store.get(job_id)), 'worker_requested': False}
        else:
            result = ProductionOrderDispatcher(request.app.state.settings).dispatch(job_id)
        return {**result, 'recovery': recovery_snapshot(store, job_id),
                'repair_request_key': registration['request']['request_key']}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc
    except (OrderConflict, ProductionBlocked, TimeoutError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/orders/{job_id}/script-candidate')
def get_script_candidate(request: Request, job_id: str, response: Response):
    from atnos.production.studio_script_recovery import load_script_candidate, load_candidate_assessment
    response.headers['Cache-Control'] = 'no-store'
    store = ProductionOrderStore(request.app.state.settings)
    try:
        candidate = load_script_candidate(store, job_id)
        return {'candidate': candidate, 'assessment': load_candidate_assessment(store, job_id, candidate) if candidate else None}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc
    except (OrderConflict, ProductionBlocked, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/orders/{job_id}/script-candidate/assessment')
def assess_script_candidate(request: Request, job_id: str, payload: StudioCandidateAssessment):
    from atnos.production.studio_script_recovery import register_candidate_assessment
    try:
        # Register evidence only. The existing resume/dispatcher owns execution.
        return register_candidate_assessment(ProductionOrderStore(request.app.state.settings), job_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc
    except (OrderConflict, ProductionBlocked, TimeoutError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _order_asset_review_rows(request: Request, job_id: str) -> list[dict[str, Any]]:
    """Read only the candidate IDs recorded for this production order."""
    settings = request.app.state.settings
    store = ProductionOrderStore(settings)
    try:
        state = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc

    order_root = Path(str(state["artifact_dir"])).resolve()
    production_root = (Path(settings.data_dir) / "production").resolve()
    if (os.name == "nt" and production_root.drive.casefold() != "e:") or not order_root.is_relative_to(production_root):
        raise HTTPException(status_code=409, detail="Katalog tej produkcji jest poza dozwoloną lokalizacją.")
    # Image-only review is a normal earlier production stage. Prefer its latest
    # manifest over an older scene assignment so the Studio shows the actual
    # candidate, including rejected history, instead of an empty review panel.
    manifests = [(order_root / name).resolve() for name in
                 ('scene_production_assets.json', 'image_api_review.json')]
    existing = [path for path in manifests if path.is_file()]
    manifest = max(existing, key=lambda path: path.stat().st_mtime_ns) if existing else manifests[0]
    if not manifest.is_relative_to(order_root):
        raise HTTPException(status_code=409, detail="Manifest zasobów jest poza katalogiem produkcji.")
    if manifest.exists() and (not manifest.is_file() or manifest.stat().st_size > 8 * 1024 * 1024):
        raise HTTPException(status_code=409, detail="Manifest zasobów jest nieprawidłowy.")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {'waiting': []}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Nie można odczytać manifestu zasobów.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("waiting"), list):
        raise HTTPException(status_code=409, detail="Manifest zasobów ma nieprawidłowy format.")

    rows: list[dict[str, Any]] = []
    for waiting in payload["waiting"]:
        if not isinstance(waiting, dict):
            raise HTTPException(status_code=409, detail="Manifest zasobów ma nieprawidłowy format.")
        acquisition = waiting.get("acquisition")
        if not isinstance(acquisition, dict) or not isinstance(acquisition.get("asset_id"), str):
            continue
        asset_id = acquisition["asset_id"].strip()
        if asset_id:
            rows.append({
                "scene_id": str(waiting.get("scene_id") or ""),
                "asset_id": asset_id,
                "acquisition_status": str(acquisition.get("status") or ""),
                "output_path": acquisition.get("output_path"),
            })
    # A retry guard can stop before a final waiting manifest exists. The real
    # completed generation checkpoints still identify this order's candidates.
    # Reading them exposes rejected history without approving or resuming it.
    known = {row['asset_id'] for row in rows}
    for checkpoint in reversed(state['checkpoints']):
        parts = str(checkpoint.get('stage') or '').split('/')
        if (len(parts) != 4 or parts[:2] not in (['assets','image_local'], ['assets','image_api'])
                or checkpoint.get('status') != 'done'
                or not store._valid_files(order_root, checkpoint['files_json'])):
            continue
        output = checkpoint.get('output')
        if not isinstance(output, dict):
            continue
        from atnos.production.order_store import file_hash
        source = Path(str(output.get('output_path') or '')).resolve()
        image_root = generated_root(settings).resolve()
        if (not source.is_file() or source.stat().st_size > 8*1024*1024
                or not (source.is_relative_to(image_root) or source.is_relative_to(order_root/'assets'))
                or not output.get('image_sha256') or file_hash(source) != output['image_sha256']):
            continue
        asset_id = output.get('asset_id')
        if isinstance(asset_id, str) and asset_id.strip() and asset_id not in known:
            rows.append({'scene_id': parts[2], 'asset_id': asset_id,
                         'acquisition_status': str(output.get('quality_status') or '').casefold(),
                         'output_path': output.get('output_path')})
            known.add(asset_id)
    return rows


def _candidate_image_path(settings: Any, brain: WealthBlueprintAssetBrain, asset: Any, output_path: Any,
                          order_root: Path | None = None) -> Path | None:
    """The catalog file must match this order's candidate and stay in generated E: storage."""
    root = generated_root(settings).resolve()
    if os.name == "nt" and root.drive.casefold() != "e:":
        return None
    path = brain.resolve_file(asset).resolve()
    media_types = {".png", ".jpg", ".jpeg", ".webp"}
    allowed = path.is_relative_to(root)
    if order_root is not None:
        production_root = (Path(settings.data_dir) / 'production').resolve()
        order_root = order_root.resolve()
        if order_root.is_relative_to(production_root) and (os.name != 'nt' or order_root.drive.casefold() == 'e:'):
            allowed |= any(path.is_relative_to((order_root / 'assets' / lane).resolve())
                           for lane in ('image_api', 'image_local'))
    if not allowed or path.suffix.casefold() not in media_types or not path.is_file():
        return None
    if not isinstance(output_path, str) or not output_path.strip():
        return None
    source = Path(output_path)
    if not source.is_absolute() or source.resolve() != path:
        return None
    return path


@router.get("/orders/{job_id}/asset-candidates")
def get_production_asset_candidates(request: Request, job_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    rows = _order_asset_review_rows(request, job_id)
    if not rows:
        return {"job_id": job_id, "candidates": []}
    settings = request.app.state.settings
    brain = WealthBlueprintAssetBrain(settings=settings)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        asset = brain.get(row["asset_id"])
        gate = asset.pre_gate if asset is not None and isinstance(asset.pre_gate, dict) else {}
        failed = [str(item) for item in gate.get("failed", []) if isinstance(item, str)] if isinstance(gate.get("failed"), list) else []
        qc = {
            "passed": gate.get("passed") if isinstance(gate.get("passed"), bool) else None,
            "verdict": str(gate["verdict"]) if gate.get("verdict") else None,
            "failed": failed,
        }
        order_root = Path(ProductionOrderStore(settings).get(job_id)['artifact_dir'])
        image = _candidate_image_path(settings, brain, asset, row["output_path"], order_root) if asset is not None else None
        reviewable = asset is not None and asset.quality_status == "AWAITING_REVIEW"
        approval_blocker: str | None = None
        if reviewable:
            if image is None:
                approval_blocker = "Obraz kandydata nie jest dostępny w dozwolonej lokalizacji."
            elif qc["passed"] is not True:
                approval_blocker = "Automatyczna kontrola jakości nie została zaliczona."
            elif character_requires_contact_sheet_review(asset) and not (
                asset.contact_sheet_path and Path(asset.contact_sheet_path).is_file()
            ):
                approval_blocker = "Postać wymaga ręcznej kontroli arkusza porównawczego."
        can_reject = bool(reviewable and image is not None)
        can_approve = bool(can_reject and approval_blocker is None)
        image_url = (
            f"/api/production/orders/{quote(job_id, safe='')}/asset-candidates/"
            f"{quote(row['asset_id'], safe='')}/image"
            if image is not None else None
        )
        candidates.append({
            "scene_id": row["scene_id"],
            "asset_id": row["asset_id"],
            "acquisition_status": row["acquisition_status"],
            "quality_status": asset.quality_status if asset is not None else "MISSING",
            "qc_verdict": qc["verdict"],
            "qc_failed": failed,
            "reject_reason": asset.reject_reason if asset is not None else None,
            "reject_notes": asset.reject_notes if asset is not None else "",
            "reviewed_by": asset.reviewed_by if asset is not None else None,
            "pre_gate": qc,
            "image_url": image_url,
            "can_approve": can_approve,
            "can_reject": can_reject,
            "approval_blocker": approval_blocker,
        })
    return {"job_id": job_id, "candidates": candidates}


@router.get("/orders/{job_id}/asset-candidates/{asset_id}/image")
def get_production_asset_candidate_image(request: Request, job_id: str, asset_id: str) -> FileResponse:
    row = next((item for item in _order_asset_review_rows(request, job_id) if item["asset_id"] == asset_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Kandydat nie należy do tej produkcji.")
    settings = request.app.state.settings
    brain = WealthBlueprintAssetBrain(settings=settings)
    asset = brain.get(asset_id)
    order_root = Path(ProductionOrderStore(settings).get(job_id)['artifact_dir'])
    path = _candidate_image_path(settings, brain, asset, row["output_path"], order_root) if asset is not None else None
    if path is None:
        raise HTTPException(status_code=404, detail="Obraz kandydata nie jest dostępny.")
    media_type = "image/jpeg" if path.suffix.casefold() in {".jpg", ".jpeg"} else f"image/{path.suffix[1:].casefold()}"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/orders/{job_id}/feedback")
def list_production_feedback(request: Request, job_id: str) -> dict[str, Any]:
    try:
        return {"feedback": ProductionFeedbackStore(request.app.state.settings).list(job_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc


class ProductionCallLimitApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_calls: int = Field(strict=True, ge=1, le=100)


@router.post('/orders/{job_id}/call-limit')
def approve_production_call_limit(request: Request, job_id: str, payload: ProductionCallLimitApproval):
    try:
        state = ProductionOrderStore(request.app.state.settings).authorize_call_limit(job_id, payload.max_calls,
            reason=f'User explicitly approved {payload.max_calls} total calls in Atnos Studio.')
        return order_status(state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Nie znaleziono produkcji.') from exc
    except OrderConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{job_id}/feedback", status_code=201)
def add_production_feedback(request: Request, job_id: str, payload: ProductionFeedback) -> dict[str, Any]:
    try:
        return ProductionFeedbackStore(request.app.state.settings).add(job_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/orders/{job_id}/feedback/{feedback_id}")
def disable_production_feedback(request: Request, job_id: str, feedback_id: str):
    try:
        ProductionFeedbackStore(request.app.state.settings).disable(job_id, feedback_id)
        return {"disabled": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono uwagi dla tej produkcji.") from exc


def _order_review_package(request: Request, job_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        state = ProductionOrderStore(request.app.state.settings).get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc
    package = next((row["output"] for row in state["checkpoints"] if row["stage"] == "package" and row["status"] == "done"), None)
    return state, package if isinstance(package, dict) else None


def _order_deliverable(request: Request, job_id: str, key: str) -> Path:
    state, package = _order_review_package(request, job_id)
    if not package:
        raise HTTPException(status_code=404, detail="Pakiet produkcji nie jest jeszcze gotowy.")
    root = Path(str(state["artifact_dir"])).resolve()
    deliverables = package.get("deliverables") if isinstance(package.get("deliverables"), dict) else {}
    files = package.get("files") if isinstance(package.get("files"), list) else []
    wanted = str(deliverables.get(key) or "")
    path = Path(wanted).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Artefakt produkcji nie jest dostępny.")
    known = {str(item.get("path")): str(item.get("sha256")) for item in files if isinstance(item, dict)}
    relative = path.relative_to(root).as_posix()
    expected_hash = known.get(relative)
    if not expected_hash:
        raise HTTPException(status_code=409, detail="Artefakt nie ma zweryfikowanego wpisu w manifeście.")
    if file_hash(path) != expected_hash:
        raise HTTPException(status_code=409, detail="Artefakt nie zgadza się z zatwierdzonym manifestem QA.")
    return path


@router.get("/orders/{job_id}/review-package")
def get_production_order_review_package(request: Request, job_id: str) -> dict[str, Any]:
    state, package = _order_review_package(request, job_id)
    deliverables = package.get("deliverables") if isinstance(package, dict) and isinstance(package.get("deliverables"), dict) else {}
    metadata_path = deliverables.get("metadata") if isinstance(deliverables, dict) else None
    metadata: dict[str, Any] = {}
    if metadata_path:
        try:
            metadata = json.loads(_order_deliverable(request, job_id, "metadata").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    script = next((row["output"] for row in state["checkpoints"] if row["stage"] == "script" and row["status"] == "done"), {})
    if script and state['request'].get('production_scope') == 'script_preview':
        row = next(row for row in state['checkpoints'] if row['stage'] == 'script')
        if not ProductionOrderStore(request.app.state.settings)._valid_files(Path(state['artifact_dir']), row['files_json']):
            raise HTTPException(status_code=409, detail='Plik scenariusza zmienił się od kontroli jakości. Sprawdź zlecenie.')
    if package:
        for key in ("video", "landscape_thumbnail"):
            _order_deliverable(request, job_id, key)
    error = state.get("error")
    failures: list[str] = []
    if isinstance(error, str) and error.startswith("{"):
        try:
            parsed = json.loads(error)
            failures = [str(item) for item in parsed.get("failures", []) if str(item).strip()]
            if not failures and parsed.get("message"):
                failures = [str(parsed["message"])]
        except json.JSONDecodeError:
            failures = [error]
    elif error:
        failures = [str(error)]
    if script and state['request'].get('production_scope') == 'script_preview':
        from atnos.production.fiction_script import StoryScript, script_issues
        from atnos.production.fiction_messages import story_issue_message
        current_script = StoryScript.model_validate({key: script[key] for key in StoryScript.model_fields})
        failures.extend(story_issue_message(item) for item in script_issues(current_script, state['profile']['production'], state['request']))
    return {
        "job_id": job_id,
        "status": str(state.get("status") or "unknown"),
        "ready_for_review": state.get("status") == "ready" and bool(package),
        "title": str(metadata.get("title") or script.get("title") or state["request"].get("topic") or ""),
        "description": str(metadata.get("description") or script.get("description") or ""),
        "tags": [str(item) for item in metadata.get("tags", []) if str(item).strip()],
        "video_ready": bool(deliverables.get("video")),
        "thumbnail_ready": bool(deliverables.get("landscape_thumbnail")),
        "script_preview": script if state['request'].get('production_scope') == 'script_preview' and script else None,
        "video_url": f"/api/production/orders/{job_id}/review-artifacts/video" if deliverables.get("video") else None,
        "thumbnail_url": f"/api/production/orders/{job_id}/review-artifacts/thumbnail" if deliverables.get("landscape_thumbnail") else None,
        "blocking_failures": failures,
        "quality_review_status": "passed" if state.get("status") == "ready" else state.get("current_step"),
        "updated_at": state.get("updated_at"),
    }


@router.get("/orders/{job_id}/review-artifacts/{kind}")
def get_production_order_review_artifact(job_id: str, kind: Literal["video", "thumbnail"], request: Request) -> FileResponse:
    key = "video" if kind == "video" else "landscape_thumbnail"
    path = _order_deliverable(request, job_id, key)
    return FileResponse(str(path), media_type="video/mp4" if kind == "video" else "image/png")


@router.post("/orders/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_production_order(request: Request, job_id: str) -> dict[str, Any]:
    try:
        return ProductionOrderDispatcher(request.app.state.settings).dispatch(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono produkcji.") from exc
    except ProductionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _effective_settings_for_session(request: Request, session: Any) -> Any:
    """Return runtime-overridden settings for production decisions."""
    return RuntimeSettingsService(session, request.app.state.settings).build_effective_settings()


class ProductionStartRequest(BaseModel):
    account_id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)


class HookSelectionRequest(BaseModel):
    hook_id: str = Field(min_length=1)


class AnalyticsFollowupRequest(BaseModel):
    followup_id: int = Field(gt=0)


class PublicPromotionRequest(BaseModel):
    approved: bool = False
    publish_at: str | None = None


class ProductionRegenerateRequest(BaseModel):
    reason: str = Field(default="manual_recovery", max_length=240)
    run_in_background: bool = True


class DistributionPackageRequest(BaseModel):
    platforms: list[str] | None = None


class RuntimePrepareRequest(BaseModel):
    start_comfyui: bool = True
    comfyui_wait_timeout_s: int = Field(default=180, ge=5, le=180)
    ensure_models: bool = False
    ensure_ollama_models: bool = False
    model_pipeline: Literal["shorts_sdxl", "shorts_ltx", "shorts_wan", "shorts_full"] = "shorts_sdxl"


class ProductionAutopilotRunRequest(BaseModel):
    account_id: str | None = None
    max_jobs: int = Field(default=1, ge=1, le=3)


class ProductionQualityReviewRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)


class ProductionQualityRemediationRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=3)


class ReferenceLearningStartRequest(BaseModel):
    profile_key: Literal["wealth_blueprint"] = "wealth_blueprint"
    target_count: Literal[40] = 40


class ProductionDelegationReviewPlanRequest(BaseModel):
    account_id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    allow_browser_ai: bool = False
    max_steps: int = Field(default=6, ge=3, le=12)


class ProductionDelegationReviewRunNextRequest(BaseModel):
    account_id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)


class ProductionDelegationReviewRunAllRequest(ProductionDelegationReviewRunNextRequest):
    limit: int = Field(default=6, ge=1, le=12)


class MoneyMachineRunNextRequest(BaseModel):
    account_id: str | None = None
    max_uploads: int = Field(default=1, ge=1, le=10)
    per_account_limit: int = Field(default=1, ge=1, le=10)
    execute: bool = False
    approved_private_upload: bool = False
    execute_recovery: bool = False


class MoneyMachineRunPublicationRequest(BaseModel):
    account_id: str | None = None
    max_publications: int = Field(default=1, ge=1, le=10)
    per_account_limit: int = Field(default=1, ge=1, le=10)
    execute: bool = False
    approved_public_promotion: bool = False
    publish_at: str | None = None


class StaleJobRecoveryRequest(BaseModel):
    account_id: str | None = None
    stale_after_minutes: int | None = Field(default=None, ge=1, le=1440)


class MoneyMachineStrategyReportRequest(BaseModel):
    account_id: str | None = None
    include_live_checks: bool = True
    send_gmail: bool = False


class MoneyMachineRemediationRequest(BaseModel):
    account_id: str | None = None
    include_live_checks: bool = True
    run_voice_doctor: bool = True


class StyleGuideEnsureRequest(BaseModel):
    account_id: str = Field(min_length=1)
    channel_name: str | None = None
    niche: str = Field(default="faceless YouTube Shorts")
    overwrite: bool = False


class VisualBackendSmokeRenderRequest(BaseModel):
    mode: Literal["auto", "ltx", "wan"] = "ltx"
    prompt: str = Field(
        default="A cinematic finance dashboard comes alive with moving cashflow lines, smooth motion, no text.",
        min_length=10,
        max_length=800,
    )


class F5TTSRuntimeStartRequest(BaseModel):
    wait_timeout_s: int = Field(default=120, ge=1, le=180)


@router.get("/readiness")
async def production_readiness(
    request: Request,
    live: bool | None = Query(default=None),
    include_live_checks: bool | None = Query(default=None),
) -> dict[str, Any]:
    run_live_checks = include_live_checks if include_live_checks is not None else (True if live is None else live)
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return await ShortProductionReadinessService(
            effective_settings,
            session=session,
        ).check(include_live_checks=run_live_checks)


@router.get("/media-observability")
def media_observability(
    request: Request,
    history_hours: int = Query(default=24, ge=1, le=168),
    max_jobs: int = Query(default=40, ge=5, le=200),
    simulate: str | None = Query(
        default=None,
        description="Comma-separated test overrides: comfy_down,gpu_locked,cache_miss,asset_rejected,quality_fail,model_fallback",
    ),
) -> dict[str, Any]:
    """One place for Comfy / GPU / cache / Asset Brain / QC / models / orders / errors."""
    from atnos.production.media_observability import ObservabilitySimulate, build_media_observability

    sim = None
    if simulate and str(getattr(request.app.state.settings, "app_env", "") or "").casefold() in {
        "test",
        "development",
        "dev",
        "local",
    }:
        flags = {part.strip().casefold() for part in simulate.split(",") if part.strip()}
        sim = ObservabilitySimulate(
            comfy_down="comfy_down" in flags,
            gpu_locked="gpu_locked" in flags,
            cache_miss_only="cache_miss" in flags,
            asset_rejected="asset_rejected" in flags,
            quality_gate_failure="quality_fail" in flags,
            model_fallback="model_fallback" in flags,
        )
    elif simulate:
        # Production: ignore simulate flags (fail closed for fake states)
        sim = None
    return build_media_observability(
        request.app.state.settings,
        history_hours=history_hours,
        max_jobs=max_jobs,
        simulate=sim,
        live=True,
    )


@router.get("/wealth-blueprint/video-engine/health")
def wealth_blueprint_video_engine_health(
    request: Request,
    require_comfy: bool | None = Query(default=None),
    require_whisper: bool | None = Query(default=None),
) -> dict[str, Any]:
    """Dependency health for Remotion, FFmpeg, ComfyUI, TTS, Whisper — no silent substitutes."""
    from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health

    return run_wealth_blueprint_video_engine_health(
        request.app.state.settings,
        require_comfy=require_comfy,
        require_whisper=require_whisper,
    )


class WealthBlueprintProductionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None, max_length=200)
    script: str | None = Field(default=None, max_length=8000)
    job_id: str | None = Field(default=None, max_length=80)
    force: bool = False
    health_only: bool = False


@router.post("/wealth-blueprint/video-engine/production-test")
def wealth_blueprint_video_engine_production_test(
    request: Request,
    payload: WealthBlueprintProductionTestRequest = Body(default_factory=WealthBlueprintProductionTestRequest),
) -> dict[str, Any]:
    """One-shot production test: health matrix, then resumable wealth_blueprint_video_job (no upload)."""
    from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health
    from atnos.production.channels.wealth_blueprint.video_job import (
        BlockedDependencyError,
        DEFAULT_SCRIPT,
        DEFAULT_TOPIC,
        WealthBlueprintVideoJob,
        WealthBlueprintVideoJobError,
    )

    settings = request.app.state.settings
    health = run_wealth_blueprint_video_engine_health(settings)
    if payload.health_only or health["status"] == "BLOCKED_DEPENDENCY":
        return {
            "status": health["status"] if health["status"] == "BLOCKED_DEPENDENCY" else "HEALTH_ONLY",
            "health": health,
            "job": None,
            "upload": False,
        }

    job = WealthBlueprintVideoJob(settings, job_id=payload.job_id)
    try:
        state = job.run(
            topic=payload.topic or DEFAULT_TOPIC,
            script=payload.script or DEFAULT_SCRIPT,
            force=payload.force,
        )
    except BlockedDependencyError as exc:
        return {
            "status": "BLOCKED_DEPENDENCY",
            "health": health,
            "dependency": exc.dependency,
            "detail": exc.detail,
            "job_id": job.job_id,
            "artifact_dir": str(job.artifact_dir),
            "upload": False,
        }
    except WealthBlueprintVideoJobError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": state.status,
        "health": health,
        "job_id": state.job_id,
        "final_mp4": state.final_mp4,
        "report_path": state.report_path,
        "artifact_dir": state.artifact_dir,
        "blocked_dependencies": state.blocked_dependencies,
        "upload": False,
    }


@router.get("/reference-learning/status")
def production_reference_learning_status(request: Request) -> dict[str, Any]:
    return ReferenceLearningService(request.app.state.settings).status()


@router.post("/reference-learning/start", status_code=status.HTTP_202_ACCEPTED)
def production_reference_learning_start(
    request: Request,
    payload: ReferenceLearningStartRequest,
) -> dict[str, Any]:
    try:
        return ReferenceLearningService(request.app.state.settings).start(
            profile_key=payload.profile_key,
            target_count=payload.target_count,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/onboarding")
async def production_onboarding_checklist(request: Request) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        return (
            await ShortsOnboardingService(
                session,
                request.app.state.settings,
            ).build_checklist()
        ).to_dict()


@router.get("/money-machine/status")
async def production_money_machine_status(
    request: Request,
    account_id: str | None = None,
    include_oauth: bool = True,
) -> dict[str, Any]:
    """Return the operational state of the Shorts machine: buffers, QA-ready jobs and blockers."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return MoneyMachineStatusService(
            effective_settings,
            session=session,
        ).build(account_id=account_id, include_oauth=include_oauth)


@router.get("/money-machine/summary")
async def production_money_machine_summary(
    request: Request,
    account_id: str | None = None,
    include_oauth: bool = True,
    max_uploads: int = Query(default=5, ge=1, le=10),
    per_account_limit: int = Query(default=2, ge=1, le=10),
) -> dict[str, Any]:
    """Return compact operator summary: upload-ready jobs, recovery jobs and blockers."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        status_payload = MoneyMachineStatusService(
            effective_settings,
            session=session,
        ).build(account_id=account_id, include_oauth=include_oauth)
        run_next_payload = MoneyMachineActionService(
            effective_settings,
            session=session,
        ).run_next(
            account_id=account_id,
            max_uploads=max_uploads,
            per_account_limit=per_account_limit,
            execute=False,
            approved_private_upload=False,
        )
    return build_money_machine_operator_summary(status_payload, run_next_payload)


@router.get("/money-machine/remediation-plan")
async def production_money_machine_remediation_plan(
    request: Request,
    account_id: str | None = None,
    include_live_checks: bool = True,
) -> dict[str, Any]:
    """Return concrete remediation actions for current money-machine blockers."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return await MoneyMachineRemediationService(
            effective_settings,
            session=session,
        ).build_plan(account_id=account_id, include_live_checks=include_live_checks)


@router.post("/money-machine/remediate-safe")
async def production_money_machine_remediate_safe(
    request: Request,
    payload: MoneyMachineRemediationRequest = Body(default_factory=MoneyMachineRemediationRequest),
) -> dict[str, Any]:
    """Run safe remediations only. Does not publish, does not spend, does not start OAuth consent."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return await MoneyMachineRemediationService(
            effective_settings,
            session=session,
        ).execute_safe_repairs(
            account_id=payload.account_id,
            run_voice_doctor=payload.run_voice_doctor,
            include_live_checks=payload.include_live_checks,
        )


@router.post("/money-machine/strategy-report")
async def production_money_machine_strategy_report(
    request: Request,
    payload: MoneyMachineStrategyReportRequest = Body(default_factory=MoneyMachineStrategyReportRequest),
) -> dict[str, Any]:
    """Write the money-machine operating plan and optionally email it to the owner."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return await MoneyMachineStrategyService(
            effective_settings,
            session=session,
        ).build(
            account_id=payload.account_id,
            include_live_checks=payload.include_live_checks,
            send_gmail=payload.send_gmail,
        )


@router.get("/voice-provider/status")
async def production_voice_provider_status(request: Request) -> dict[str, Any]:
    """Return last known premium voice readiness without spending provider quota."""
    return build_voice_provider_status(request.app.state.settings)


@router.post("/voice-provider/check")
async def production_voice_provider_check(request: Request) -> dict[str, Any]:
    """Run a tiny live TTS probe, persist report and return whether public-quality voice is ready."""
    report = await write_voice_provider_doctor_report(request.app.state.settings)
    _push_production_ws_event(
        request,
        {
            "type": "voice_provider_check",
            "status": "ready" if report.get("public_voice_ready") else "blocked",
            "public_voice_ready": bool(report.get("public_voice_ready")),
            "next_action": report.get("next_action"),
            "report_path": report.get("report_path"),
        },
        notify_slack=False,
    )
    return report


@router.get("/voice-provider/f5-runtime/status")
async def production_f5_tts_runtime_status(request: Request) -> dict[str, Any]:
    """Return local F5-TTS runtime diagnostics without starting any process."""
    return F5TTSRuntimeService(request.app.state.settings).status().to_dict()


@router.post("/voice-provider/f5-runtime/start")
async def production_f5_tts_runtime_start(
    request: Request,
    payload: F5TTSRuntimeStartRequest = Body(default_factory=F5TTSRuntimeStartRequest),
) -> dict[str, Any]:
    """Start F5-TTS only through the configured local launch command."""
    result = F5TTSRuntimeService(request.app.state.settings).start(wait_timeout_s=payload.wait_timeout_s).to_dict()
    _push_production_ws_event(
        request,
        {
            "type": "f5_tts_runtime",
            "status": "ready" if result.get("endpoint_ready") else "blocked",
            "endpoint_ready": bool(result.get("endpoint_ready")),
            "started": bool(result.get("started")),
            "next_action": result.get("next_action"),
        },
        notify_slack=False,
    )
    return result


@router.post("/voice-provider/f5-runtime/stop")
async def production_f5_tts_runtime_stop(request: Request) -> dict[str, Any]:
    """Stop only the configured local F5-TTS runtime process tree."""
    result = F5TTSRuntimeService(request.app.state.settings).stop().to_dict()
    _push_production_ws_event(
        request,
        {
            "type": "f5_tts_runtime",
            "status": "stopped" if result.get("stopped") else "blocked",
            "endpoint_ready": bool(result.get("endpoint_ready")),
            "stopped": bool(result.get("stopped")),
            "next_action": result.get("next_action"),
        },
        notify_slack=False,
    )
    return result


@router.get("/visual-backend/status")
async def production_visual_backend_status(request: Request) -> dict[str, Any]:
    """Return last known visual backend readiness without launching a render."""
    return build_visual_backend_status(request.app.state.settings)


@router.get("/public-quality/status")
async def production_public_quality_status(
    request: Request,
    account_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return combined public-quality readiness for voice, visuals and style guide."""
    return build_public_quality_gate_status(request.app.state.settings, account_id=account_id)


@router.get("/quality-review/status")
async def production_quality_review_status(request: Request) -> dict[str, Any]:
    """Return automatic review-quality gate status for finished Shorts."""
    return ProductionQualityScheduler(request.app.state.settings).status()


@router.post("/quality-review/run-once")
async def production_quality_review_run_once(
    request: Request,
    payload: ProductionQualityReviewRunRequest = Body(default_factory=ProductionQualityReviewRunRequest),
) -> dict[str, Any]:
    """Run the review-quality gate once. Never uploads or publishes."""
    result = ProductionQualityScheduler(request.app.state.settings).audit_due_jobs(limit=payload.limit)
    _push_production_ws_event(
        request,
        {
            "type": "production_quality_review",
            "status": result.get("status"),
            "checked_count": result.get("checked_count"),
            "blocked_count": result.get("blocked_count"),
            "passed_count": result.get("passed_count"),
        },
        notify_slack=False,
    )
    return result


@router.get("/quality-remediation/status")
async def production_quality_remediation_status(request: Request) -> dict[str, Any]:
    """Return automatic quality remediation status for blocked Shorts."""
    return ProductionQualityRemediationService(request.app.state.settings).status()


@router.post("/quality-remediation/run-once")
async def production_quality_remediation_run_once(
    request: Request,
    payload: ProductionQualityRemediationRunRequest = Body(default_factory=ProductionQualityRemediationRunRequest),
) -> dict[str, Any]:
    """Queue/run safe regeneration for quality-blocked jobs. Never uploads or publishes."""
    result = await ProductionQualityRemediationService(
        request.app.state.settings,
        event_sink=lambda event: _push_production_ws_event(request, event, notify_slack=False),
    ).run_due(limit=payload.limit)
    _push_production_ws_event(
        request,
        {
            "type": "production_quality_remediation",
            "status": result.get("status"),
            "planned_count": result.get("planned_count"),
            "started_count": result.get("started_count"),
        },
        notify_slack=False,
    )
    return result


@router.post("/style-guide/ensure")
async def production_style_guide_ensure(
    request: Request,
    payload: StyleGuideEnsureRequest,
) -> dict[str, Any]:
    """Create a safe default channel style guide for public-quality production."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        result = ensure_channel_style_guide(
            effective_settings,
            payload.account_id,
            channel_name=payload.channel_name,
            niche=payload.niche,
            overwrite=payload.overwrite,
        )
        public_quality = build_public_quality_gate_status(effective_settings, account_id=payload.account_id)

    response = {
        "status": result.status,
        "created": result.created,
        "path": str(result.path) if result.path else None,
        "message": result.message,
        "public_quality": public_quality,
    }
    _push_production_ws_event(
        request,
        {
            "type": "style_guide_ensure",
            "account_id": payload.account_id,
            "status": result.status,
            "created": result.created,
            "path": str(result.path) if result.path else None,
            "message": result.message,
        },
        notify_slack=False,
    )
    return response


@router.get("/provider-doctor")
async def production_provider_doctor_status(
    request: Request,
    account_id: str | None = Query(default=None),
    include_oauth: bool = Query(default=True),
) -> dict[str, Any]:
    """Return one operator snapshot for voice, visuals, OAuth, cost guard and latest failed job."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return build_provider_doctor_status(
            effective_settings,
            session=session,
            account_id=account_id,
            include_oauth=include_oauth,
        )


@router.post("/provider-doctor/actions/{action_id}")
async def production_provider_doctor_action(
    request: Request,
    action_id: str,
    account_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Execute a safe Provider Doctor action and return the refreshed snapshot."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        doctor_before = build_provider_doctor_status(
            effective_settings,
            session=session,
            account_id=account_id,
            include_oauth=True,
        )
        try:
            action = provider_doctor_action_by_id(doctor_before, action_id)
        except ProviderDoctorActionError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not bool(action.get("safe_to_run")) or str(action.get("kind") or "") != "frontend_api":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Provider Doctor action is manual or requires owner approval.",
                "action": action,
            },
        )

    if action_id == "run_voice_provider_check":
        result = await write_voice_provider_doctor_report(effective_settings)
        event_type = "voice_provider_check"
        ready_key = "public_voice_ready"
    elif action_id == "run_visual_backend_check":
        result = await write_visual_backend_doctor_report(effective_settings)
        event_type = "visual_backend_check"
        ready_key = "public_visual_ready"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No executor registered for Provider Doctor action: {action_id}",
        )

    _push_production_ws_event(
        request,
        {
            "type": "provider_doctor_action",
            "action_id": action_id,
            "event": event_type,
            "status": "ready" if result.get(ready_key) else "blocked",
            ready_key: bool(result.get(ready_key)),
            "next_action": result.get("next_action"),
            "report_path": result.get("report_path"),
        },
        notify_slack=False,
    )

    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        refreshed = build_provider_doctor_status(
            effective_settings,
            session=session,
            account_id=account_id,
            include_oauth=True,
        )
    return {
        "status": "completed",
        "action": action,
        "result": result,
        "provider_doctor": refreshed,
    }


@router.get("/operating-standards")
async def production_operating_standards(
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return quality and safety standards derived from the latest Atnos research report."""
    return build_operating_standards(request.app.state.settings, project_id=project_id)


@router.get("/faceless-playbook")
async def production_faceless_playbook(
    request: Request,
    project_id: str = Query(min_length=1),
    title: str = Query(default=""),
    description: str = Query(default=""),
    content_profile: str = Query(default=""),
) -> dict[str, Any]:
    """Return the active faceless production playbook for a project/channel."""
    return build_faceless_playbook_for_project(
        request.app.state.settings,
        project_id=project_id,
        title=title,
        description=description,
        content_profile=content_profile,
    )


@router.post("/visual-backend/check")
async def production_visual_backend_check(request: Request) -> dict[str, Any]:
    """Run a live backend probe, persist report and return whether public-quality visuals are ready."""
    report = await write_visual_backend_doctor_report(request.app.state.settings)
    _push_production_ws_event(
        request,
        {
            "type": "visual_backend_check",
            "status": "ready" if report.get("public_visual_ready") else "blocked",
            "public_visual_ready": bool(report.get("public_visual_ready")),
            "next_action": report.get("next_action"),
            "report_path": report.get("report_path"),
        },
        notify_slack=False,
    )
    return report


@router.post("/visual-backend/smoke-render")
async def production_visual_backend_smoke_render(
    request: Request,
    payload: VisualBackendSmokeRenderRequest = Body(default_factory=VisualBackendSmokeRenderRequest),
) -> dict[str, Any]:
    """Run one short local WAN/LTX smoke render. Never uploads and never publishes."""
    result = await run_visual_backend_smoke_render(
        request.app.state.settings,
        mode=payload.mode,
        prompt=payload.prompt,
    )
    _push_production_ws_event(
        request,
        {
            "type": "visual_backend_smoke_render",
            "status": result.get("status"),
            "mode": result.get("mode"),
            "public_visual_ready": bool(result.get("public_visual_ready")),
            "artifact_dir": result.get("artifact_dir"),
            "message": result.get("next_action") or "visual backend smoke render completed",
        },
        notify_slack=False,
    )
    return result


@router.post("/money-machine/run-next")
async def production_money_machine_run_next(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: MoneyMachineRunNextRequest = Body(default_factory=MoneyMachineRunNextRequest),
) -> dict[str, Any]:
    """Plan or execute the next safe money-machine action. Public publishing is never automatic here."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        result = MoneyMachineActionService(
            effective_settings,
            session=session,
        ).run_next(
            account_id=payload.account_id,
            max_uploads=payload.max_uploads,
            per_account_limit=payload.per_account_limit,
            execute=payload.execute,
            approved_private_upload=payload.approved_private_upload,
        )
    if payload.execute and payload.execute_recovery:
        money_machine = result.get("money_machine") if isinstance(result.get("money_machine"), dict) else {}
        cost_guard = money_machine.get("cost_guard") if isinstance(money_machine.get("cost_guard"), dict) else {}
        if cost_guard and (bool(cost_guard.get("blocked")) or not bool(cost_guard.get("allowed"))):
            return {
                **result,
                "status": "cost_guard_blocked",
                "execute_recovery": False,
                "recovery_count": 0,
                "recovery_results": [],
                "reason": "Cost guard blocked recovery regeneration.",
                "cost_guard": cost_guard,
            }
        recovery_items = [
            item
            for item in result.get("blocked", [])
            if str(item.get("action") or "") == "public_quality_blocked"
            and str(item.get("recovery_endpoint") or "").strip()
        ][: max(1, min(int(payload.max_uploads), 10))]
        recovery_results: list[dict[str, Any]] = []
        for item in recovery_items:
            try:
                plan = await prepare_regeneration_job(
                    effective_settings,
                    source_job_id=str(item.get("job_id") or ""),
                    reason=str(item.get("recovery_action") or "money_machine_recovery"),
                    event_sink=lambda event: _push_production_ws_event(request, event, notify_slack=False),
                )
            except Exception as exc:
                recovery_results.append(
                    {
                        "source_job_id": str(item.get("job_id") or ""),
                        "status": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            _push_production_ws_event(
                request,
                {
                    "type": "pipeline_regeneration",
                    "source_job_id": plan.source_job_id,
                    "job_id": plan.context.job_id,
                    "account_id": plan.context.account_id,
                    "topic_id": plan.context.topic.id,
                    "status": "queued",
                    "reason": str(item.get("recovery_action") or "money_machine_recovery"),
                    "message": "money-machine regeneration job queued",
                },
                notify_slack=False,
            )
            background_tasks.add_task(plan.orchestrator.run_job, plan.context)
            recovery_results.append(plan.to_payload(reason=str(item.get("recovery_action") or "money_machine_recovery")))
        result = {
            **result,
            "status": "recovery_queued" if recovery_results else result.get("status", "planned"),
            "execute_recovery": True,
            "recovery_count": len([item for item in recovery_results if item.get("status") == "queued"]),
            "recovery_results": recovery_results,
        }
    return result


@router.post("/money-machine/run-publication")
async def production_money_machine_run_publication(
    request: Request,
    payload: MoneyMachineRunPublicationRequest = Body(default_factory=MoneyMachineRunPublicationRequest),
) -> dict[str, Any]:
    """Plan or execute supervised promotion of private Shorts. Requires explicit public approval."""
    with request.app.state.database.session_scope() as session:
        effective_settings = _effective_settings_for_session(request, session)
        return MoneyMachineActionService(
            effective_settings,
            session=session,
        ).run_publication(
            account_id=payload.account_id,
            max_publications=payload.max_publications,
            per_account_limit=payload.per_account_limit,
            execute=payload.execute,
            approved_public_promotion=payload.approved_public_promotion,
            publish_at=payload.publish_at,
        )


@router.post("/preflight")
async def production_preflight(request: Request, payload: ProductionStartRequest) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        return (
            await ProductionPreflightService(
                session,
                request.app.state.settings,
            ).check(account_id=payload.account_id, topic_id=payload.topic_id)
        ).to_dict()


@router.get("/delegation-review/status")
def production_delegation_review_status(
    request: Request,
    account_id: str = Query(min_length=1),
    topic_id: str = Query(min_length=1),
) -> dict[str, Any]:
    """Return evidence-backed AI review plan status for an account/topic."""
    with request.app.state.database.session_scope() as session:
        return ProductionDelegationService(
            session,
            request.app.state.settings,
        ).status(account_id=account_id, topic_id=topic_id)


@router.post("/delegation-review/plan")
async def production_delegation_review_plan(
    request: Request,
    payload: ProductionDelegationReviewPlanRequest,
) -> dict[str, Any]:
    """Create an auditable AI delegation review plan. Never renders, uploads or publishes."""
    with request.app.state.database.session_scope() as session:
        return await ProductionDelegationService(
            session,
            request.app.state.settings,
        ).create_review_plan(
            account_id=payload.account_id,
            topic_id=payload.topic_id,
            allow_browser_ai=payload.allow_browser_ai,
            max_steps=payload.max_steps,
        )


@router.post("/delegation-review/run-next")
async def production_delegation_review_run_next(
    request: Request,
    payload: ProductionDelegationReviewRunNextRequest,
) -> dict[str, Any]:
    """Execute one pending AI review child task. Never renders, uploads, publishes, trades or pays."""
    with request.app.state.database.session_scope() as session:
        return await ProductionDelegationService(
            session,
            request.app.state.settings,
        ).run_next_review_task(account_id=payload.account_id, topic_id=payload.topic_id)


@router.post("/delegation-review/run-all")
async def production_delegation_review_run_all(
    request: Request,
    payload: ProductionDelegationReviewRunAllRequest,
) -> dict[str, Any]:
    """Execute pending AI review child tasks sequentially. Stops on blocker/error. Never renders or uploads."""
    with request.app.state.database.session_scope() as session:
        return await ProductionDelegationService(
            session,
            request.app.state.settings,
        ).run_all_review_tasks(
            account_id=payload.account_id,
            topic_id=payload.topic_id,
            limit=payload.limit,
        )


@router.post("/runtime/prepare")
async def prepare_production_runtime(
    request: Request,
    payload: RuntimePrepareRequest = Body(default_factory=RuntimePrepareRequest),
) -> dict[str, Any]:
    """Probe Ollama model roles and optionally start ComfyUI before running production."""
    return (
        await ProductionRuntimePreparationService(request.app.state.settings).prepare(
            start_comfyui=payload.start_comfyui,
            comfyui_wait_timeout_s=payload.comfyui_wait_timeout_s,
            ensure_models=payload.ensure_models,
            ensure_ollama_models=payload.ensure_ollama_models,
            model_pipeline=payload.model_pipeline,
        )
    ).to_dict()


@router.post("/recover-stale")
async def recover_stale_production_jobs(
    request: Request,
    payload: StaleJobRecoveryRequest = Body(default_factory=StaleJobRecoveryRequest),
) -> dict[str, Any]:
    """Recover production jobs left active by a crashed worker or hard timeout."""
    result = ProductionJobRecoveryService(request.app.state.settings).recover_stale_jobs(
        stale_after_minutes=payload.stale_after_minutes,
        account_id=payload.account_id,
    )
    if int(result.get("recovered_count") or 0) > 0:
        _push_production_ws_event(
            request,
            {
                "type": "pipeline_recovery",
                "status": "completed",
                "recovered_count": result.get("recovered_count"),
                "restored_topic_count": result.get("restored_topic_count"),
                "message": "stale production jobs recovered",
            },
            notify_slack=False,
        )
    return result


@router.post("/autopilot/run-once")
async def run_production_autopilot_once(
    request: Request,
    payload: ProductionAutopilotRunRequest = Body(default_factory=ProductionAutopilotRunRequest),
) -> dict[str, Any]:
    """Run autonomous topic moderation and QA-gated production. Does not publish public videos."""
    service = AutonomousContentLoopService(
        request.app.state.settings,
        event_sink=lambda event: _push_production_ws_event(request, event, notify_slack=False),
        session_scope=request.app.state.database.session_scope,
    )
    return await service.run_once(account_id=payload.account_id, max_jobs=payload.max_jobs)


@router.post("/content-loop/run-once")
async def run_autonomous_content_loop_once(
    request: Request,
    payload: ProductionAutopilotRunRequest = Body(default_factory=ProductionAutopilotRunRequest),
) -> dict[str, Any]:
    """Alias for the full autonomous Shorts loop: queue -> approve -> produce."""
    return await run_production_autopilot_once(request, payload)


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def start_production(
    request: Request,
    payload: ProductionStartRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        preflight = await ProductionPreflightService(
            session,
            request.app.state.settings,
        ).check(account_id=payload.account_id, topic_id=payload.topic_id)
    preflight_payload = preflight.to_dict()
    if not preflight.ready_to_start_local_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Production preflight failed; job was not queued.",
                "preflight": preflight_payload,
            },
        )
    with request.app.state.database.session_scope() as session:
        delegation_service = ProductionDelegationService(session, request.app.state.settings)
        delegation_status = delegation_service.status(account_id=payload.account_id, topic_id=payload.topic_id)
        if not bool(delegation_status.get("ready_for_production_start")):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Production review gate failed; job was not queued.",
                    "production_review": delegation_status,
                    "next_action": delegation_status.get("next_action"),
                },
            )
        delegation_review = delegation_status

    orchestrator = ProductionOrchestrator(
        request.app.state.settings,
        event_sink=lambda event: _push_production_ws_event(request, event, notify_slack=False),
    )
    try:
        context = await orchestrator.prepare(account_id=payload.account_id, topic_id=payload.topic_id)
    except CostGuardBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Production budget guard blocked this job.",
                "cost_guard": exc.decision,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    background_tasks.add_task(orchestrator.run_job, context)
    return {
        "job_id": context.job_id,
        "account_id": payload.account_id,
        "topic_id": payload.topic_id,
        "status": "queued",
        "preflight": preflight_payload,
        "delegation_review": delegation_review,
    }


@router.get("/jobs")
async def list_production_jobs(
    request: Request,
    account_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List recent production jobs so the UI can recover state without WebSocket history."""
    safe_limit = max(1, min(int(limit), 100))
    settings = request.app.state.settings
    with connect(settings) as db:
        params: list[Any] = []
        where = ""
        if account_id:
            where = "WHERE j.account_id = ?"
            params.append(account_id)
        rows = db.execute(
            f"""
            SELECT
                j.*,
                t.title AS topic_title,
                t.description AS topic_description,
                t.total_score AS topic_total_score
            FROM pipeline_jobs j
            LEFT JOIN topic_queue t ON t.id = j.topic_id
            {where}
            ORDER BY j.created_at DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    return {"jobs": [_production_job_payload(request, str(row["id"]), row=dict(row)) for row in rows]}


@router.get("/jobs/{job_id}")
async def get_production_job(job_id: str, request: Request) -> dict[str, Any]:
    payload = _production_job_payload(request, job_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} was not found.")
    return payload


def _review_artifact_root(request: Request, job_id: str) -> Path:
    with connect(request.app.state.settings) as db:
        row = db.execute("SELECT artifact_dir FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} was not found.")
    root = Path(str(row["artifact_dir"] or "")).resolve()
    if not root.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production artifacts are not available.")
    return root


def _review_artifact_path(root: Path, kind: str) -> Path:
    relative = {
        "video": Path("assets") / "final_short.mp4",
        "thumbnail": Path("thumbnail") / "thumbnail_v1.png",
    }.get(kind)
    if relative is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown review artifact.")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Review {kind} is not available.")
    return path


@router.get("/jobs/{job_id}/review-package")
async def get_production_review_package(job_id: str, request: Request) -> dict[str, Any]:
    """Return only the three owner-facing Short deliverables and their QA state."""
    job = _production_job_payload(request, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} was not found.")
    root = _review_artifact_root(request, job_id)
    package_path = root / "youtube_private_upload_package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        package = {}
    qa_path = root / "qa_report.json"
    try:
        qa_report = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        qa_report = {}
    metadata = package.get("upload_metadata") if isinstance(package.get("upload_metadata"), dict) else {}
    qa_blockers = qa_report.get("blocking_failures") if isinstance(qa_report, dict) else None
    blocking_failures = qa_blockers if isinstance(qa_blockers, list) else job.get("private_upload_blockers") or []
    video_ready = (root / "assets" / "final_short.mp4").is_file()
    thumbnail_ready = (root / "thumbnail" / "thumbnail_v1.png").is_file()
    return {
        "job_id": job_id,
        "status": str(job.get("status") or "unknown"),
        "ready_for_review": bool(job.get("ready_for_review")) and video_ready and thumbnail_ready,
        "title": str(metadata.get("title") or job.get("topic_title") or ""),
        "description": str(metadata.get("description") or ""),
        "tags": [str(item) for item in metadata.get("tags", []) if str(item).strip()],
        "video_ready": video_ready,
        "thumbnail_ready": thumbnail_ready,
        "video_url": f"/api/production/jobs/{job_id}/review-artifacts/video" if video_ready else None,
        "thumbnail_url": f"/api/production/jobs/{job_id}/review-artifacts/thumbnail" if thumbnail_ready else None,
        "blocking_failures": [str(item) for item in blocking_failures if str(item).strip()],
        "quality_review_status": job.get("quality_review_status"),
        "updated_at": job.get("updated_at"),
    }


@router.get("/jobs/{job_id}/review-artifacts/{kind}")
async def get_production_review_artifact(job_id: str, kind: Literal["video", "thumbnail"], request: Request) -> FileResponse:
    root = _review_artifact_root(request, job_id)
    path = _review_artifact_path(root, kind)
    media_type = "video/mp4" if kind == "video" else "image/png"
    return FileResponse(str(path), media_type=media_type)


@router.get("/jobs/{job_id}/distribution-plan")
async def get_production_distribution_plan(job_id: str, request: Request) -> dict[str, Any]:
    """Return QA-gated export/upload readiness for YouTube Shorts, TikTok and Instagram Reels."""
    try:
        return build_distribution_plan(request.app.state.settings, job_id=job_id)
    except DistributionPlanError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/distribution-package")
async def create_production_distribution_package(
    job_id: str,
    request: Request,
    payload: DistributionPackageRequest = Body(default_factory=DistributionPackageRequest),
) -> dict[str, Any]:
    """Write platform release packages for QA-gated Shorts distribution."""
    try:
        result = build_distribution_release_package(
            request.app.state.settings,
            job_id=job_id,
            platforms=payload.platforms,
        )
    except DistributionPlanError as exc:
        message = str(exc)
        if "was not found" in message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
        if "Unknown distribution platform" in message:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc

    _push_production_ws_event(
        request,
        {
            "type": "distribution_package",
            "job_id": job_id,
            "status": result.get("status"),
            "created_count": result.get("created_count"),
            "blocked_count": result.get("blocked_count"),
            "message": f"distribution packages: {result.get('status')}",
        },
        notify_slack=False,
    )
    return result


@router.patch("/jobs/{job_id}/hook")
async def select_hook(job_id: str, request: Request, payload: HookSelectionRequest) -> dict[str, Any]:
    from atnos.intelligence.storage import json_dumps, utc_now_iso

    settings = request.app.state.settings
    with connect(settings) as db:
        row = db.execute("SELECT hooks FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} was not found.")
        hooks = json_loads(row["hooks"], {})
        variants = hooks.get("variants") if isinstance(hooks, dict) else None
        if not isinstance(variants, list):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Hooks are not generated yet.")
        selected = next((item for item in variants if str(item.get("id")) == payload.hook_id), None)
        if selected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Hook {payload.hook_id} was not found.")
        hooks["selected_hook_id"] = payload.hook_id
        hooks["selected"] = selected
        hooks["selection_mode"] = "user_selected"
        db.execute(
            "UPDATE pipeline_jobs SET hooks = ?, selected_hook_id = ?, updated_at = ? WHERE id = ?",
            (json_dumps(hooks), payload.hook_id, utc_now_iso(), job_id),
        )
        db.commit()
    _push_production_ws_event(
        request,
        {
            "type": "pipeline_step",
            "job_id": job_id,
            "step": "hook",
            "status": "selected",
            "progress": 0.15,
            "message": f"hook selected: {payload.hook_id}",
        },
    )
    return {"job_id": job_id, "selected_hook_id": payload.hook_id, "hooks": hooks}


@router.post("/jobs/{job_id}/upload-private")
async def upload_private(job_id: str, request: Request) -> dict[str, Any]:
    """Upload a QA-approved short to YouTube as private. Never publishes public."""
    try:
        with request.app.state.database.session_scope() as session:
            result = upload_private_from_package(
                settings=request.app.state.settings,
                session=session,
                job_id=job_id,
            )
    except ProductionUploadError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    _push_production_ws_event(
        request,
        {
            "type": "pipeline_step",
            "job_id": job_id,
            "step": "private_upload",
            "status": result.get("status"),
            "progress": 1.0 if result.get("status") == "uploaded_private" else 0.92,
            "message": result.get("message") or result.get("youtube_url") or "private upload updated",
        },
        notify_slack=False,
    )
    return result


@router.post("/jobs/{job_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_production_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ProductionRegenerateRequest = Body(default_factory=ProductionRegenerateRequest),
) -> dict[str, Any]:
    """Create a fresh production attempt for a blocked/obsolete job. Never uploads or publishes."""
    try:
        plan = await prepare_regeneration_job(
            request.app.state.settings,
            source_job_id=job_id,
            reason=payload.reason,
            event_sink=lambda event: _push_production_ws_event(request, event, notify_slack=False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        detail: dict[str, Any] | str = message
        if "already active:" in message:
            detail = {
                "message": "A production job for this topic is already active.",
                "active_job_id": message.rsplit(":", maxsplit=1)[-1].strip(),
            }
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc

    _push_production_ws_event(
        request,
        {
            "type": "pipeline_regeneration",
            "source_job_id": job_id,
            "job_id": plan.context.job_id,
            "account_id": plan.context.account_id,
            "topic_id": plan.context.topic.id,
            "status": "queued",
            "reason": payload.reason,
            "message": "regeneration job queued",
        },
        notify_slack=False,
    )
    background_tasks.add_task(plan.orchestrator.run_job, plan.context)
    return plan.to_payload(reason=payload.reason)


@router.post("/jobs/{job_id}/schedule-public")
async def schedule_public_publication(
    job_id: str,
    request: Request,
    payload: PublicPromotionRequest,
) -> dict[str, Any]:
    """Promote a reviewed private upload to scheduled-public/public. Requires explicit approval."""
    try:
        with request.app.state.database.session_scope() as session:
            effective_settings = _effective_settings_for_session(request, session)
            result = promote_private_job_to_public(
                settings=effective_settings,
                session=session,
                job_id=job_id,
                approved=payload.approved,
                publish_at=payload.publish_at,
            )
    except ProductionPublicationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    _push_production_ws_event(
        request,
        {
            "type": "pipeline_step",
            "job_id": job_id,
            "step": "public_publish",
            "status": result.get("status"),
            "progress": 1.0 if result.get("status") in {"scheduled_public", "published_public"} else 0.92,
            "message": result.get("message") or result.get("publish_at") or result.get("youtube_url") or "public promotion updated",
        },
        notify_slack=False,
    )
    return result


@router.post("/analytics/followup")
async def run_production_analytics_followup(
    request: Request,
    payload: AnalyticsFollowupRequest,
) -> dict[str, Any]:
    try:
        with request.app.state.database.session_scope() as session:
            result = run_analytics_followup(
                settings=request.app.state.settings,
                session=session,
                followup_id=payload.followup_id,
            )
    except ProductionUploadError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _push_production_ws_event(
        request,
        {
            "type": "pipeline_step",
            "job_id": result.get("job_id") or "",
            "step": "analytics",
            "status": result.get("status"),
            "progress": 1.0,
            "message": "analytics follow-up complete",
        },
        notify_slack=False,
    )
    return result


@router.post("/analytics/run-due")
async def run_due_production_analytics_followups(request: Request, limit: int = 10) -> dict[str, Any]:
    with request.app.state.database.session_scope() as session:
        results = run_due_analytics_followups(
            settings=request.app.state.settings,
            session=session,
            limit=limit,
        )
    return {"status": "completed", "count": len(results), "results": results}


def _push_production_ws_event(request: Request, event: dict[str, Any], *, notify_slack: bool = True) -> None:
    events = getattr(request.app.state, "production_ws_events", None)
    if not isinstance(events, list):
        events = []
        request.app.state.production_ws_events = events
    events.append(event)
    del events[:-100]
    if notify_slack:
        SlackWebhookNotifier(request.app.state.settings).notify_pipeline_event(event)


def _production_job_payload(
    request: Request,
    job_id: str,
    *,
    row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    settings = request.app.state.settings
    with connect(settings) as db:
        if row is None:
            job_row = db.execute(
                """
                SELECT
                    j.*,
                    t.title AS topic_title,
                    t.description AS topic_description,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if job_row is None:
                return None
            row = dict(job_row)
        step_rows = db.execute(
            """
            SELECT step, status, progress, attempt, message, started_at, completed_at
            FROM pipeline_job_steps
            WHERE job_id = ?
            ORDER BY id
            """,
            (job_id,),
        ).fetchall()

    payload = dict(row)
    for field in (
        "hooks",
        "script",
        "audio_timestamps",
        "asset_plan",
        "video_manifest",
        "qa_report",
        "quality_review_result",
        "upload_result",
        "public_publish_result",
        "analytics_followup_plan",
    ):
        payload[field] = json_loads(str(payload.get(field) or ""), None)
    payload["steps"] = [dict(step) for step in step_rows]
    quality_blocked = str(payload.get("quality_review_status") or "") == QUALITY_REVIEW_BLOCKED
    payload["ready_for_review"] = bool(payload.get("qa_report_path") and payload.get("upload_package_path")) and not quality_blocked
    payload["ready_for_private_upload"] = payload.get("status") == "qa_passed" and not quality_blocked
    payload["ready_for_public_promotion"] = str(payload.get("status") or "") in PUBLIC_PROMOTION_READY_STATUSES
    payload["review_status"] = str(payload.get("review_status") or "") or _production_review_status(payload)
    payload["private_upload_blockers"] = _private_upload_blockers(payload)
    payload["public_promotion_blockers"] = public_promotion_blockers(settings, payload)
    payload["public_quality_evidence"] = public_quality_evidence(payload)
    return payload


def _production_review_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "")
    if status == "qa_passed":
        return "ready_for_private_upload_review"
    if status == "qa_failed":
        return "qa_review_required"
    if status in {"upload_connection_required", "private_upload_failed", "private_upload_quota_exceeded"}:
        return "private_upload_retry_required"
    if status == "uploaded_private":
        return "uploaded_private"
    if status in {"public_publish_connection_required", "public_publish_failed", "public_publish_quota_exceeded"}:
        return "public_promotion_retry_required"
    if status == "scheduled_public":
        return "scheduled_public"
    if status == "published_public":
        return "published_public"
    if payload.get("ready_for_review"):
        return "review_required"
    return "not_ready"


def _private_upload_blockers(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not payload.get("upload_package_path"):
        blockers.append("upload_package_missing")
    if not payload.get("qa_report_path"):
        blockers.append("qa_report_missing")
    if str(payload.get("quality_review_status") or "") == QUALITY_REVIEW_BLOCKED:
        blockers.append("quality_review_blocked")
    if str(payload.get("status") or "") != "qa_passed":
        blockers.append("qa_not_passed")
    qa_report = payload.get("qa_report")
    if isinstance(qa_report, dict):
        if not bool(qa_report.get("auto_uploadable")):
            blockers.append("qa_not_auto_uploadable")
        blocking_failures = qa_report.get("blocking_failures")
        if isinstance(blocking_failures, list) and blocking_failures:
            blockers.append("qa_blocking_failures")
    return list(dict.fromkeys(blockers))
