from __future__ import annotations

"""Evidence-first configuration service for a single managed YouTube channel.

No endpoint in this module calls YouTube or changes a public channel. It records
an explicit channel identity, a reviewable Channel Identity Manifest and a
Channel Publishing Policy. Upload code can use ``publish_eligibility`` as a
hard precondition instead of guessing based on project names.
"""

from datetime import UTC, datetime
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.db.models.project import Project
from atnos.db.models.youtube_channel_identity import YouTubeChannelIdentity
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService


_ACCOUNT_BINDING_RE = re.compile(r"^youtube:[^\s@]+@[^\s@]+\.[^\s@]+$", re.IGNORECASE)
_CHANNEL_ID_RE = re.compile(r"^UC[\w-]{8,}$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,}$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")

MANIFEST_REQUIRED_FIELDS = (
    "mission",
    "target_audience",
    "viewer_promise",
    "primary_topics",
    "forbidden_topics",
    "tone",
    "narration_style",
    "pace",
    "hook_structure",
    "editing_style",
    "subtitle_style",
    "color_palette",
    "typography",
    "music_style",
    "voice_character",
    "duration_range_seconds",
    "thumbnail_rules",
    "title_rules",
    "publishing_cadence",
    "recognizable_elements",
)

POLICY_REQUIRED_FIELDS = (
    "allowed_channel_ids",
    "allowed_days",
    "allowed_hours_local",
    "max_publications_per_day",
    "minimum_quality_score",
    "visibility_mode",
    "require_private_first",
    "require_quality_gate",
    "require_render_validation",
    "notify_owner",
    "stop_conditions",
)


class YouTubeChannelIdentityError(ValueError):
    """Zwracany, gdy konfiguracja kanału nie spełnia bezpieczeństwa routingu."""


class YouTubeChannelIdentityNotFoundError(YouTubeChannelIdentityError):
    """Zwracany dla nieistniejącego projektu."""


class YouTubeChannelIdentityService:
    """Utrwala kanał i publikację bez wykonywania działań zewnętrznych."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def snapshot(self, project_id: int) -> dict[str, Any]:
        project = self._require_project(project_id)
        identity = self._identity_for(project.id)
        if identity is None:
            return {
                "project_id": project.id,
                "status": "not_started",
                "identity": None,
                "manifest": {"status": "not_started", "missing_fields": list(MANIFEST_REQUIRED_FIELDS)},
                "publishing_policy": {"status": "not_started", "missing_fields": list(POLICY_REQUIRED_FIELDS)},
                "oauth": {"status": "not_linked", "connection_id": None},
                "publish_eligibility": self._eligibility(None),
            }
        return self._serialize(identity)

    def upsert_identity(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        project = self._require_project(project_id)
        binding_key = _required_text(payload, "account_binding_key", max_length=320).casefold()
        if not _ACCOUNT_BINDING_RE.fullmatch(binding_key):
            raise YouTubeChannelIdentityError(
                "account_binding_key musi mieć format youtube:owner@example.com; sama nazwa kanału nie wystarcza."
            )

        youtube_channel_id = _optional_text(payload, "youtube_channel_id", max_length=128)
        if youtube_channel_id and not _CHANNEL_ID_RE.fullmatch(youtube_channel_id):
            raise YouTubeChannelIdentityError("youtube_channel_id musi być stabilnym identyfikatorem zaczynającym się od UC.")
        channel_handle = _optional_text(payload, "channel_handle", max_length=160)
        if channel_handle and not _HANDLE_RE.fullmatch(channel_handle):
            raise YouTubeChannelIdentityError("channel_handle musi zaczynać się od @ i zawierać co najmniej trzy znaki.")
        channel_url = _optional_text(payload, "channel_url", max_length=1024)
        if channel_url and not channel_url.startswith("https://www.youtube.com/"):
            raise YouTubeChannelIdentityError("channel_url musi wskazywać pełny adres https://www.youtube.com/...")
        language_code = _optional_text(payload, "language_code", max_length=24)
        if language_code and not _LANGUAGE_RE.fullmatch(language_code):
            raise YouTubeChannelIdentityError("language_code musi mieć format en albo en-US.")
        oauth_connection_id = _optional_positive_int(payload, "oauth_connection_id")
        if oauth_connection_id is not None and self.session.get(OAuthConnection, oauth_connection_id) is None:
            raise YouTubeChannelIdentityError("Nie znaleziono wskazanego połączenia OAuth.")

        identity = self._identity_for(project.id)
        if identity is None:
            identity = YouTubeChannelIdentity(project_id=project.id, account_binding_key=binding_key)
            self.session.add(identity)

        binding_changed = identity.account_binding_key != binding_key
        channel_changed = identity.youtube_channel_id != youtube_channel_id
        identity.account_binding_key = binding_key
        identity.youtube_channel_id = youtube_channel_id
        identity.channel_handle = channel_handle
        identity.channel_url = channel_url
        identity.display_name = _optional_text(payload, "display_name", max_length=240)
        identity.owner_label = _optional_text(payload, "owner_label", max_length=240)
        identity.language_code = language_code
        identity.target_market = _optional_text(payload, "target_market", max_length=80)
        identity.niche = _optional_text(payload, "niche", max_length=4000)
        identity.oauth_connection_id = oauth_connection_id
        if binding_changed or channel_changed:
            identity.owner_confirmed = False
            identity.owner_confirmed_at = None
            identity.manifest_approved_at = None
            identity.publishing_policy_approved_at = None
            identity.manifest_status = "draft" if identity.manifest_json else "not_started"
            identity.publishing_policy_status = "draft" if identity.publishing_policy_json else "not_started"
        identity.identity_status = "pending_confirmation" if youtube_channel_id else "insufficient_data"
        identity.verification_evidence_json = {
            "source": "operator_configuration",
            "updated_at": _now().isoformat(),
            "requires_owner_confirmation": bool(youtube_channel_id),
            "external_api_verified": False,
        }
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise YouTubeChannelIdentityError(
                "To konto lub identyfikator kanału jest już przypisany do innego projektu."
            ) from exc
        return self._serialize(identity)

    def confirm_identity(self, project_id: int, *, confirmed_channel_id: str) -> dict[str, Any]:
        identity = self._require_identity(project_id)
        if not identity.youtube_channel_id:
            raise YouTubeChannelIdentityError("Nie można potwierdzić kanału bez youtube_channel_id zaczynającego się od UC.")
        if confirmed_channel_id.strip() != identity.youtube_channel_id:
            raise YouTubeChannelIdentityError("Potwierdzony identyfikator nie zgadza się z kanałem zapisanym w projekcie.")
        identity.owner_confirmed = True
        identity.owner_confirmed_at = _now()
        identity.identity_status = "owner_confirmed"
        identity.verification_evidence_json = {
            **_object(identity.verification_evidence_json),
            "owner_confirmation": {
                "channel_id": identity.youtube_channel_id,
                "confirmed_at": identity.owner_confirmed_at.isoformat(),
                "external_api_verified": False,
            },
        }
        self.session.flush()
        return self._serialize(identity)

    def verify_live_identity(self, project_id: int, *, settings: Settings) -> dict[str, Any]:
        """Read the OAuth-selected channel and persist an auditable observation.

        This calls only ``youtube.channels.list(mine=true)``. It does not upload,
        edit a channel, or treat a successful read as publication permission.
        A human must still confirm the discovered stable ``UC...`` id before the
        manifest and publishing policy can be approved.
        """
        identity = self._require_identity(project_id)
        if identity.oauth_connection_id is None:
            raise YouTubeChannelIdentityError("Add a connected YouTube OAuth before running a live channel probe.")

        try:
            observed = YouTubeConnectorService(
                self.session,
                settings,
                account_id=identity.oauth_connection_id,
            ).get_owned_channel()
        except YouTubeConnectorError as exc:
            raise YouTubeChannelIdentityError(
                "YouTube live probe failed. Check the persistent OAuth connection and youtube.readonly scope."
            ) from exc

        observed_id = str(observed.get("channel_id") or "").strip()
        if not _CHANNEL_ID_RE.fullmatch(observed_id):
            raise YouTubeChannelIdentityError("YouTube live probe did not return a valid UC channel id.")

        mismatch = bool(identity.youtube_channel_id and identity.youtube_channel_id != observed_id)
        if mismatch:
            identity.owner_confirmed = False
            identity.owner_confirmed_at = None
            identity.manifest_approved_at = None
            identity.publishing_policy_approved_at = None
            identity.manifest_status = "draft" if identity.manifest_json else "not_started"
            identity.publishing_policy_status = "draft" if identity.publishing_policy_json else "not_started"
            identity.identity_status = "identity_mismatch"
        else:
            if not identity.youtube_channel_id:
                identity.youtube_channel_id = observed_id
                identity.owner_confirmed = False
                identity.owner_confirmed_at = None
                identity.manifest_approved_at = None
                identity.publishing_policy_approved_at = None
                identity.manifest_status = "draft" if identity.manifest_json else "not_started"
                identity.publishing_policy_status = "draft" if identity.publishing_policy_json else "not_started"
            identity.identity_status = "owner_confirmed" if identity.owner_confirmed else "live_verified_pending_confirmation"

        if not identity.display_name and observed.get("title"):
            identity.display_name = str(observed["title"])
        if not identity.channel_url and observed.get("channel_url"):
            identity.channel_url = str(observed["channel_url"])
        if not identity.channel_handle and str(observed.get("custom_url") or "").startswith("@"):
            identity.channel_handle = str(observed["custom_url"])
        identity.last_verified_at = _now()
        identity.verification_evidence_json = {
            **_object(identity.verification_evidence_json),
            "live_probe": {
                "source": str(observed.get("source") or "youtube.channels.list(mine=true)"),
                "observed_channel_id": observed_id,
                "observed_title": observed.get("title"),
                "observed_channel_url": observed.get("channel_url"),
                "observed_custom_url": observed.get("custom_url"),
                "checked_at": identity.last_verified_at.isoformat(),
                "matched_configured_channel_id": not mismatch,
                "external_api_verified": not mismatch,
            },
        }
        self.session.flush()
        return self._serialize(identity)

    def update_manifest(self, project_id: int, manifest: dict[str, Any]) -> dict[str, Any]:
        identity = self._require_identity(project_id)
        normalized = _object(manifest)
        missing = _manifest_missing_fields(normalized)
        identity.manifest_json = normalized
        identity.manifest_approved_at = None
        identity.manifest_status = "ready_for_approval" if not missing else "draft"
        self.session.flush()
        return self._serialize(identity)

    def approve_manifest(self, project_id: int) -> dict[str, Any]:
        identity = self._require_identity(project_id)
        missing = _manifest_missing_fields(_object(identity.manifest_json))
        if missing:
            raise YouTubeChannelIdentityError(f"Manifest nie jest kompletny. Brakuje: {', '.join(missing)}.")
        if not identity.owner_confirmed:
            raise YouTubeChannelIdentityError("Najpierw potwierdź właściwy youtube_channel_id dla tego projektu.")
        identity.manifest_status = "approved"
        identity.manifest_approved_at = _now()
        self.session.flush()
        return self._serialize(identity)

    def update_publishing_policy(self, project_id: int, policy: dict[str, Any]) -> dict[str, Any]:
        identity = self._require_identity(project_id)
        normalized = _object(policy)
        _validate_policy_shape(normalized)
        identity.publishing_policy_json = normalized
        identity.publishing_policy_approved_at = None
        identity.publishing_policy_status = "ready_for_approval" if not _policy_missing_fields(normalized) else "draft"
        self.session.flush()
        return self._serialize(identity)

    def approve_publishing_policy(self, project_id: int) -> dict[str, Any]:
        identity = self._require_identity(project_id)
        policy = _object(identity.publishing_policy_json)
        missing = _policy_missing_fields(policy)
        if missing:
            raise YouTubeChannelIdentityError(f"Polityka publikacji nie jest kompletna. Brakuje: {', '.join(missing)}.")
        if not identity.owner_confirmed or not identity.youtube_channel_id:
            raise YouTubeChannelIdentityError("Najpierw potwierdź właściwy youtube_channel_id dla tego projektu.")
        if identity.manifest_status != "approved":
            raise YouTubeChannelIdentityError("Polityka publikacji wymaga zatwierdzonego Channel Identity Manifest.")
        allowed = {str(value).strip() for value in policy.get("allowed_channel_ids", [])}
        if identity.youtube_channel_id not in allowed:
            raise YouTubeChannelIdentityError("Polityka musi zawierać dokładny youtube_channel_id aktywnego kanału.")
        identity.publishing_policy_status = "approved"
        identity.publishing_policy_approved_at = _now()
        self.session.flush()
        return self._serialize(identity)

    def _require_project(self, project_id: int) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise YouTubeChannelIdentityNotFoundError(f"Nie znaleziono projektu #{project_id}.")
        return project

    def _identity_for(self, project_id: int) -> YouTubeChannelIdentity | None:
        return self.session.scalar(
            select(YouTubeChannelIdentity).where(YouTubeChannelIdentity.project_id == project_id)
        )

    def _require_identity(self, project_id: int) -> YouTubeChannelIdentity:
        self._require_project(project_id)
        identity = self._identity_for(project_id)
        if identity is None:
            raise YouTubeChannelIdentityError("Najpierw dodaj trwałą tożsamość kanału do projektu.")
        return identity

    def _serialize(self, identity: YouTubeChannelIdentity) -> dict[str, Any]:
        oauth = self._oauth_snapshot(identity.oauth_connection_id)
        manifest = _object(identity.manifest_json)
        policy = _object(identity.publishing_policy_json)
        return {
            "project_id": identity.project_id,
            "status": identity.identity_status,
            "identity": {
                "id": identity.id,
                "account_binding_key": identity.account_binding_key,
                "youtube_channel_id": identity.youtube_channel_id,
                "channel_handle": identity.channel_handle,
                "channel_url": identity.channel_url,
                "display_name": identity.display_name,
                "owner_label": identity.owner_label,
                "language_code": identity.language_code,
                "target_market": identity.target_market,
                "niche": identity.niche,
                "oauth_connection_id": identity.oauth_connection_id,
                "identity_status": identity.identity_status,
                "owner_confirmed": identity.owner_confirmed,
                "owner_confirmed_at": _iso(identity.owner_confirmed_at),
                "last_verified_at": _iso(identity.last_verified_at),
                "verification_evidence": _object(identity.verification_evidence_json),
            },
            "manifest": {
                "status": identity.manifest_status,
                "approved_at": _iso(identity.manifest_approved_at),
                "missing_fields": _manifest_missing_fields(manifest),
                "value": manifest,
            },
            "publishing_policy": {
                "status": identity.publishing_policy_status,
                "approved_at": _iso(identity.publishing_policy_approved_at),
                "missing_fields": _policy_missing_fields(policy),
                "value": policy,
            },
            "oauth": oauth,
            "publish_eligibility": self._eligibility(identity),
        }

    def _oauth_snapshot(self, connection_id: int | None) -> dict[str, Any]:
        if connection_id is None:
            return {"status": "not_linked", "connection_id": None, "ready": False}
        connection = self.session.get(OAuthConnection, connection_id)
        if connection is None:
            return {"status": "missing", "connection_id": connection_id, "ready": False}
        return {
            "status": connection.status,
            "connection_id": connection.id,
            "account_id": connection.account_id,
            "connected_at": _iso(connection.connected_at),
            "ready": connection.status == "connected",
        }

    def _eligibility(self, identity: YouTubeChannelIdentity | None) -> dict[str, Any]:
        reasons: list[dict[str, str]] = []
        if identity is None:
            reasons.append({"code": "identity_missing", "detail": "Brak trwałej tożsamości kanału."})
        else:
            manifest = _object(identity.manifest_json)
            policy = _object(identity.publishing_policy_json)
            if not identity.youtube_channel_id:
                reasons.append({"code": "channel_id_missing", "detail": "Brak stabilnego youtube_channel_id (UC...)."})
            if not identity.owner_confirmed:
                reasons.append({"code": "identity_unconfirmed", "detail": "Właściciel nie potwierdził mapowania kanału."})
            if identity.identity_status == "identity_mismatch":
                reasons.append({"code": "live_identity_mismatch", "detail": "Live OAuth probe points to a different channel than this project configuration."})
            if identity.manifest_status != "approved":
                reasons.append({"code": "manifest_unapproved", "detail": "Channel Identity Manifest nie jest zatwierdzony."})
            if identity.publishing_policy_status != "approved":
                reasons.append({"code": "publishing_policy_unapproved", "detail": "Channel Publishing Policy nie jest zatwierdzona."})
            if _manifest_missing_fields(manifest):
                reasons.append({"code": "manifest_incomplete", "detail": "Manifest nie zawiera wszystkich wymaganych pól."})
            if _policy_missing_fields(policy):
                reasons.append({"code": "publishing_policy_incomplete", "detail": "Polityka publikacji nie zawiera wszystkich wymaganych pól."})
        ready = not reasons
        return {
            "status": "eligible_by_local_policy" if ready else "blocked",
            "eligible_by_local_policy": ready,
            "external_upload_checked": False,
            "reasons": reasons,
            "note": "Ten wynik nie wykonuje uploadu ani live probe OAuth; potwierdza wyłącznie lokalne warunki bezpieczeństwa routingu.",
        }


def _manifest_missing_fields(manifest: dict[str, Any]) -> list[str]:
    return [field for field in MANIFEST_REQUIRED_FIELDS if not _meaningful(manifest.get(field))]


def _policy_missing_fields(policy: dict[str, Any]) -> list[str]:
    return [field for field in POLICY_REQUIRED_FIELDS if not _meaningful(policy.get(field))]


def _validate_policy_shape(policy: dict[str, Any]) -> None:
    if "max_publications_per_day" in policy:
        value = policy["max_publications_per_day"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 24:
            raise YouTubeChannelIdentityError("max_publications_per_day musi być liczbą całkowitą od 1 do 24.")
    if "minimum_quality_score" in policy:
        value = policy["minimum_quality_score"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 100:
            raise YouTubeChannelIdentityError("minimum_quality_score musi mieścić się w zakresie 0-100.")
    if "visibility_mode" in policy and policy["visibility_mode"] not in {"private", "unlisted", "scheduled", "public"}:
        raise YouTubeChannelIdentityError("visibility_mode musi mieć wartość private, unlisted, scheduled albo public.")
    for bool_key in ("require_private_first", "require_quality_gate", "require_render_validation", "notify_owner"):
        if bool_key in policy and not isinstance(policy[bool_key], bool):
            raise YouTubeChannelIdentityError(f"{bool_key} musi być wartością true albo false.")
    for list_key in ("allowed_channel_ids", "allowed_days", "allowed_hours_local", "stop_conditions"):
        if list_key in policy and not isinstance(policy[list_key], list):
            raise YouTubeChannelIdentityError(f"{list_key} musi być listą.")


def _required_text(payload: dict[str, Any], key: str, *, max_length: int) -> str:
    value = _optional_text(payload, key, max_length=max_length)
    if value is None:
        raise YouTubeChannelIdentityError(f"{key} jest wymagane.")
    return value


def _optional_text(payload: dict[str, Any], key: str, *, max_length: int) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise YouTubeChannelIdentityError(f"{key} musi być tekstem.")
    value = raw.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise YouTubeChannelIdentityError(f"{key} przekracza limit {max_length} znaków.")
    return value


def _optional_positive_int(payload: dict[str, Any], key: str) -> int | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise YouTubeChannelIdentityError(f"{key} musi być dodatnią liczbą całkowitą.")
    return raw


def _meaningful(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return value is not None


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
