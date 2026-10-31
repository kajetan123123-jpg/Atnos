from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atnos.core.config import Settings
from atnos.production.project_manifest import canonical_json, file_sha256
from atnos.production.steps.voice_synthesis import build_voice_identity


STYLE_ID = "WEALTH_BLUEPRINT_STYLE_V1"
VOICE_APPROVAL_FILENAME = "production_voice_approval.json"
SIGNING_KEY_ENV = "ATNOS_VISUAL_BASELINE_SIGNING_KEY"


class ProductionVoiceApprovalError(RuntimeError):
    pass


class ProductionVoiceApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    style_id: Literal["WEALTH_BLUEPRINT_STYLE_V1"] = STYLE_ID
    channel: Literal["wealth_blueprint"] = "wealth_blueprint"
    approval_status: Literal["production_approved"] = "production_approved"
    voice_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_family: str = Field(min_length=1)
    reference_audio_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    approved_sample_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    approval_note: str = ""
    approved_at: str
    record_hash: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    approval_signature: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")


def production_voice_approval_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "production" / "styles" / STYLE_ID / VOICE_APPROVAL_FILENAME


def approve_production_voice(
    settings: Settings,
    voice_manifest: dict[str, Any],
    *,
    reviewer: str,
    approval_note: str = "",
) -> ProductionVoiceApproval:
    """Zapisz podpisaną akceptację konkretnej tożsamości głosu po decyzji właściciela."""
    if not reviewer.strip():
        raise ProductionVoiceApprovalError("Production voice approval requires a named reviewer")
    if voice_manifest.get("production_ready") is not True:
        raise ProductionVoiceApprovalError("Only a technically production-ready voice can be approved")
    sample_path = Path(str(voice_manifest.get("output_file") or voice_manifest.get("audio_path") or ""))
    sample_hash = file_sha256(sample_path)
    if sample_hash is None:
        raise ProductionVoiceApprovalError(f"Approved voice sample is missing: {sample_path}")
    identity = build_voice_identity(settings, voice_manifest)
    provider = str(identity.get("provider_family") or "")
    if provider not in {"f5_tts", "elevenlabs", "openai_tts"}:
        raise ProductionVoiceApprovalError(f"Voice provider is not eligible for production approval: {provider}")
    reference_hash = str(identity.get("reference_audio_sha256") or "")
    if provider == "f5_tts" and not reference_hash:
        raise ProductionVoiceApprovalError("F5-TTS approval requires a verifiable reference-audio hash")
    candidate = ProductionVoiceApproval(
        voice_identity_hash=str(identity["identity_hash"]),
        provider_family=provider,
        reference_audio_sha256=reference_hash,
        approved_sample_sha256=sample_hash,
        reviewer=reviewer.strip(),
        approval_note=approval_note.strip(),
        approved_at=datetime.now(UTC).isoformat(),
    )
    hashed = candidate.model_copy(update={"record_hash": _record_hash(candidate)})
    signed = hashed.model_copy(update={"approval_signature": _approval_signature(hashed)})
    _atomic_json_write(production_voice_approval_path(settings), signed.model_dump(mode="json"))
    return signed


def verify_production_voice_approval(
    settings: Settings,
    voice_manifest: dict[str, Any],
) -> dict[str, Any]:
    path = production_voice_approval_path(settings)
    if not path.is_file():
        return {
            "status": "not_configured",
            "approved": False,
            "approval_path": str(path),
            "voice_status": "candidate",
        }
    try:
        approval = load_production_voice_approval(path)
        identity = build_voice_identity(settings, voice_manifest)
        if approval.voice_identity_hash != identity.get("identity_hash"):
            return {
                "status": "identity_mismatch",
                "approved": False,
                "approval_path": str(path),
                "voice_status": "candidate",
                "approved_identity_hash": approval.voice_identity_hash,
                "current_identity_hash": identity.get("identity_hash"),
            }
    except (OSError, ValueError, ProductionVoiceApprovalError) as exc:
        return {
            "status": "invalid",
            "approved": False,
            "approval_path": str(path),
            "voice_status": "candidate",
            "error": str(exc),
        }
    return {
        "status": "approved",
        "approved": True,
        "approval_path": str(path),
        "voice_status": "production_approved",
        "provider_family": approval.provider_family,
        "voice_identity_hash": approval.voice_identity_hash,
        "reviewer": approval.reviewer,
        "approved_at": approval.approved_at,
    }


def load_production_voice_approval(path: Path) -> ProductionVoiceApproval:
    """Load and authenticate a production voice approval without accepting a new voice."""
    approval = ProductionVoiceApproval.model_validate_json(path.read_text(encoding="utf-8"))
    if approval.record_hash != _record_hash(approval):
        raise ProductionVoiceApprovalError("Production voice approval record hash mismatch")
    expected_signature = _approval_signature(approval)
    if not hmac.compare_digest(approval.approval_signature, expected_signature):
        raise ProductionVoiceApprovalError("Production voice approval signature mismatch")
    return approval


def _record_hash(approval: ProductionVoiceApproval) -> str:
    payload = approval.model_dump(mode="json")
    payload.pop("record_hash", None)
    payload.pop("approval_signature", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _approval_signature(approval: ProductionVoiceApproval) -> str:
    key = os.getenv(SIGNING_KEY_ENV, "").encode("utf-8")
    if len(key) < 32:
        raise ProductionVoiceApprovalError(
            f"Production voice approval requires {SIGNING_KEY_ENV} with at least 32 bytes"
        )
    payload = f"{approval.record_hash}:{approval.voice_identity_hash}:{approval.approved_sample_sha256}"
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
