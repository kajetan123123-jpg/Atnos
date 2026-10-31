from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import getLogger
from time import perf_counter
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    from atnos.core.config import Settings
    from atnos.repositories.runtime_control_settings_repository import (
        RuntimeControlSettingsRepository,
    )
    from atnos.repositories.user_repository import UserRepository
    from sqlalchemy.orm import Session

    from atnos.db.models.runtime_control_settings import RuntimeControlSettings

SUPPORTED_VOICE_STT_ENGINES = frozenset(
    {
        "browser-speech",
        "whisper-local",
        "faster-whisper-local",
    }
)

logger = getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeControlSnapshot:
    browser_control_enabled: bool
    stealth_headless_enabled: bool
    browser_allow_any_url: bool
    external_actions_enabled: bool
    scheduler_enabled: bool
    oauth_enabled: bool
    autonomy_mode: str
    emergency_stop_enabled: bool
    pause_all_jobs: bool
    login_automation_enabled: bool
    two_factor_automation_enabled: bool
    publish_automation_enabled: bool
    public_upload_enabled: bool
    default_retry_budget: int
    default_vram_floor_gb: int
    voice_stt_engine: str
    voice_stt_model_path: str | None
    voice_stt_device: str
    ambient_listening_enabled: bool
    ambient_wake_word: str
    ambient_mic_device: str | None
    ambient_muted: bool
    ambient_context_enabled: bool
    trend_watch_enabled: bool
    trend_watch_query: str
    trend_watch_region: str
    trend_watch_interval_minutes: int
    trend_watch_last_run_at: datetime | None
    voice_replies_enabled: bool
    voice_reply_volume: float
    voice_reply_voice_id: int | None
    last_user_interaction_at: datetime | None
    last_briefing_at: datetime | None


class RuntimeSettingsService:
    def __init__(
        self,
        session: "Session",
        settings: "Settings",
        *,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.progress_callback = progress_callback
        self._users: "UserRepository | None" = None
        self._controls: "RuntimeControlSettingsRepository | None" = None

    @property
    def users(self) -> "UserRepository":
        if self._users is None:
            from atnos.repositories.user_repository import UserRepository

            self._users = UserRepository(self.session)
        return self._users

    @property
    def controls(self) -> "RuntimeControlSettingsRepository":
        if self._controls is None:
            from atnos.repositories.runtime_control_settings_repository import (
                RuntimeControlSettingsRepository,
            )

            self._controls = RuntimeControlSettingsRepository(self.session)
        return self._controls

    def get_or_create_owner_controls(self) -> "RuntimeControlSettings":
        with self._trace("runtime_settings/get_or_create_owner_controls"):
            with self._trace("runtime_settings/owner_lookup"):
                owner = self.users.get_owner()
            if owner is None:
                raise ValueError("Owner user is not configured.")

            with self._trace("runtime_settings/controls_lookup"):
                existing = self.controls.get_for_user(owner.id)
            if existing is not None:
                return existing

            with self._trace("runtime_settings/controls_create"):
                return self.controls.create(
                    user_id=owner.id,
                    browser_control_enabled=True,
                    stealth_headless_enabled=False,
                    browser_allow_any_url=False,
                    external_actions_enabled=True,
                    scheduler_enabled=True,
                    oauth_enabled=True,
                    autonomy_mode="supervised",
                    emergency_stop_enabled=False,
                    pause_all_jobs=False,
                    login_automation_enabled=False,
                    two_factor_automation_enabled=False,
                    publish_automation_enabled=False,
                    public_upload_enabled=bool(self.settings.allow_public_upload),
                    default_retry_budget=3,
                    default_vram_floor_gb=6,
                    voice_stt_engine=self.settings.voice_stt_engine,
                    voice_stt_model_path=self._normalize_optional_text(self.settings.voice_stt_model_path),
                    voice_stt_device=self._normalize_optional_text(self.settings.voice_stt_device) or "cpu",
                    ambient_listening_enabled=str(getattr(self.settings, "app_env", "") or "").strip().lower()
                    in {"development", "dev", "local"},
                    ambient_wake_word="Atnos",
                    ambient_mic_device=None,
                    ambient_muted=False,
                    ambient_context_enabled=str(getattr(self.settings, "app_env", "") or "").strip().lower()
                    in {"development", "dev", "local"},
                    trend_watch_enabled=False,
                    trend_watch_query="short video hooks",
                    trend_watch_region="PL",
                    trend_watch_interval_minutes=180,
                    trend_watch_last_run_at=None,
                    voice_replies_enabled=False,
                    voice_reply_volume=0.8,
                    voice_reply_voice_id=None,
                    last_user_interaction_at=None,
                    last_briefing_at=None,
                )

    def snapshot(self) -> RuntimeControlSnapshot:
        try:
            snapshot_row = self._load_owner_controls_snapshot_row()
        except Exception as exc:
            if self.settings.app_env != "test":
                raise
            logger.debug("Runtime controls snapshot unavailable in test context: %s", exc)
            return self._snapshot_from_settings_defaults()
        if snapshot_row is not None:
            return self._snapshot_from_mapping(snapshot_row)

        try:
            controls = self.get_or_create_owner_controls()
        except Exception as exc:
            if self.settings.app_env != "test":
                raise
            logger.debug("Runtime controls fallback unavailable in test context: %s", exc)
            return self._snapshot_from_settings_defaults()
        return self._snapshot_from_controls(controls)

    def _load_owner_controls_snapshot_row(self) -> dict[str, object] | None:
        from sqlalchemy import text

        row = self.session.execute(
            text(
                """
                SELECT
                    r.browser_control_enabled,
                    r.stealth_headless_enabled,
                    r.browser_allow_any_url,
                    r.external_actions_enabled,
                    r.scheduler_enabled,
                    r.oauth_enabled,
                    r.autonomy_mode,
                    r.emergency_stop_enabled,
                    r.pause_all_jobs,
                    r.login_automation_enabled,
                    r.two_factor_automation_enabled,
                    r.publish_automation_enabled,
                    r.public_upload_enabled,
                    r.default_retry_budget,
                    r.default_vram_floor_gb,
                    r.voice_stt_engine,
                    r.voice_stt_model_path,
                    r.voice_stt_device,
                    r.ambient_listening_enabled,
                    r.ambient_wake_word,
                    r.ambient_mic_device,
                    r.ambient_muted,
                    r.ambient_context_enabled,
                    r.trend_watch_enabled,
                    r.trend_watch_query,
                    r.trend_watch_region,
                    r.trend_watch_interval_minutes,
                    r.trend_watch_last_run_at,
                    r.voice_replies_enabled,
                    r.voice_reply_volume,
                    r.voice_reply_voice_id,
                    r.last_user_interaction_at,
                    r.last_briefing_at
                FROM runtime_control_settings r
                JOIN users u ON u.id = r.user_id
                WHERE u.is_owner = 1
                LIMIT 1
                """
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def _snapshot_from_mapping(self, controls: dict[str, object]) -> RuntimeControlSnapshot:
        return RuntimeControlSnapshot(
            browser_control_enabled=self._as_bool(controls["browser_control_enabled"]),
            stealth_headless_enabled=self._as_bool(controls["stealth_headless_enabled"]),
            browser_allow_any_url=self._as_bool(controls["browser_allow_any_url"]),
            external_actions_enabled=self._as_bool(controls["external_actions_enabled"]),
            scheduler_enabled=self._as_bool(controls["scheduler_enabled"]),
            oauth_enabled=self._as_bool(controls["oauth_enabled"]),
            autonomy_mode=str(controls["autonomy_mode"]),
            emergency_stop_enabled=self._as_bool(controls["emergency_stop_enabled"]),
            pause_all_jobs=self._as_bool(controls["pause_all_jobs"]),
            login_automation_enabled=self._as_bool(controls["login_automation_enabled"]),
            two_factor_automation_enabled=self._as_bool(controls["two_factor_automation_enabled"]),
            publish_automation_enabled=self._as_bool(controls["publish_automation_enabled"]),
            public_upload_enabled=self._as_bool(controls["public_upload_enabled"]),
            default_retry_budget=int(controls["default_retry_budget"] or 0),
            default_vram_floor_gb=int(controls["default_vram_floor_gb"] or 1),
            voice_stt_engine=str(controls["voice_stt_engine"] or self.settings.voice_stt_engine),
            voice_stt_model_path=self._normalize_optional_text(controls["voice_stt_model_path"]),
            voice_stt_device=self._normalize_optional_text(controls["voice_stt_device"]) or "cpu",
            ambient_listening_enabled=self._as_bool(controls["ambient_listening_enabled"]),
            ambient_wake_word=str(controls["ambient_wake_word"] or "Atnos").strip() or "Atnos",
            ambient_mic_device=self._normalize_optional_text(controls["ambient_mic_device"]),
            ambient_muted=self._as_bool(controls["ambient_muted"]),
            ambient_context_enabled=self._as_bool(controls["ambient_context_enabled"]),
            trend_watch_enabled=self._as_bool(controls["trend_watch_enabled"]),
            trend_watch_query=str(controls["trend_watch_query"] or "short video hooks").strip() or "short video hooks",
            trend_watch_region=str(controls["trend_watch_region"] or "PL").strip().upper() or "PL",
            trend_watch_interval_minutes=max(30, int(controls["trend_watch_interval_minutes"] or 180)),
            trend_watch_last_run_at=self._ensure_utc_datetime(controls["trend_watch_last_run_at"]),
            voice_replies_enabled=self._as_bool(controls["voice_replies_enabled"]),
            voice_reply_volume=float(controls["voice_reply_volume"] or 0.8),
            voice_reply_voice_id=(
                int(controls["voice_reply_voice_id"])
                if controls["voice_reply_voice_id"] is not None
                else None
            ),
            last_user_interaction_at=self._ensure_utc_datetime(controls["last_user_interaction_at"]),
            last_briefing_at=self._ensure_utc_datetime(controls["last_briefing_at"]),
        )

    def _snapshot_from_controls(self, controls: "RuntimeControlSettings") -> RuntimeControlSnapshot:
        return RuntimeControlSnapshot(
            browser_control_enabled=controls.browser_control_enabled,
            stealth_headless_enabled=controls.stealth_headless_enabled,
            browser_allow_any_url=controls.browser_allow_any_url,
            external_actions_enabled=controls.external_actions_enabled,
            scheduler_enabled=controls.scheduler_enabled,
            oauth_enabled=controls.oauth_enabled,
            autonomy_mode=controls.autonomy_mode,
            emergency_stop_enabled=controls.emergency_stop_enabled,
            pause_all_jobs=controls.pause_all_jobs,
            login_automation_enabled=controls.login_automation_enabled,
            two_factor_automation_enabled=controls.two_factor_automation_enabled,
            publish_automation_enabled=controls.publish_automation_enabled,
            public_upload_enabled=controls.public_upload_enabled,
            default_retry_budget=controls.default_retry_budget,
            default_vram_floor_gb=controls.default_vram_floor_gb,
            voice_stt_engine=controls.voice_stt_engine,
            voice_stt_model_path=self._normalize_optional_text(controls.voice_stt_model_path),
            voice_stt_device=self._normalize_optional_text(controls.voice_stt_device) or "cpu",
            ambient_listening_enabled=controls.ambient_listening_enabled,
            ambient_wake_word=(controls.ambient_wake_word or "Atnos").strip() or "Atnos",
            ambient_mic_device=self._normalize_optional_text(controls.ambient_mic_device),
            ambient_muted=controls.ambient_muted,
            ambient_context_enabled=controls.ambient_context_enabled,
            trend_watch_enabled=controls.trend_watch_enabled,
            trend_watch_query=(controls.trend_watch_query or "short video hooks").strip() or "short video hooks",
            trend_watch_region=(controls.trend_watch_region or "PL").strip().upper() or "PL",
            trend_watch_interval_minutes=max(30, int(controls.trend_watch_interval_minutes or 180)),
            trend_watch_last_run_at=self._ensure_utc_datetime(controls.trend_watch_last_run_at),
            voice_replies_enabled=controls.voice_replies_enabled,
            voice_reply_volume=float(controls.voice_reply_volume),
            voice_reply_voice_id=controls.voice_reply_voice_id,
            last_user_interaction_at=self._ensure_utc_datetime(controls.last_user_interaction_at),
            last_briefing_at=self._ensure_utc_datetime(controls.last_briefing_at),
        )

    def _snapshot_from_settings_defaults(self) -> RuntimeControlSnapshot:
        return RuntimeControlSnapshot(
            browser_control_enabled=True,
            stealth_headless_enabled=False,
            browser_allow_any_url=False,
            external_actions_enabled=True,
            scheduler_enabled=True,
            oauth_enabled=self.settings.oauth_enabled,
            autonomy_mode="supervised",
            emergency_stop_enabled=False,
            pause_all_jobs=False,
            login_automation_enabled=False,
            two_factor_automation_enabled=False,
            publish_automation_enabled=False,
            public_upload_enabled=bool(self.settings.allow_public_upload),
            default_retry_budget=3,
            default_vram_floor_gb=6,
            voice_stt_engine=self.settings.voice_stt_engine,
            voice_stt_model_path=self._normalize_optional_text(self.settings.voice_stt_model_path),
            voice_stt_device=self._normalize_optional_text(self.settings.voice_stt_device) or "cpu",
            ambient_listening_enabled=str(getattr(self.settings, "app_env", "") or "").strip().lower()
            in {"development", "dev", "local"},
            ambient_wake_word="Atnos",
            ambient_mic_device=None,
            ambient_muted=False,
            ambient_context_enabled=str(getattr(self.settings, "app_env", "") or "").strip().lower()
            in {"development", "dev", "local"},
            trend_watch_enabled=False,
            trend_watch_query="short video hooks",
            trend_watch_region="PL",
            trend_watch_interval_minutes=180,
            trend_watch_last_run_at=None,
            voice_replies_enabled=False,
            voice_reply_volume=0.8,
            voice_reply_voice_id=None,
            last_user_interaction_at=None,
            last_briefing_at=None,
        )

    def build_effective_settings(self) -> Settings:
        controls = self.snapshot()
        return self.settings.model_copy(
            update={
                "oauth_enabled": controls.oauth_enabled,
                "allow_public_upload": controls.public_upload_enabled,
                "voice_stt_engine": controls.voice_stt_engine,
                "voice_stt_model_path": controls.voice_stt_model_path,
                "voice_stt_device": controls.voice_stt_device,
            }
        )

    def update_owner_controls(
        self,
        *,
        existing_controls: "RuntimeControlSettings | None" = None,
        **kwargs,
    ) -> RuntimeControlSnapshot:
        controls = existing_controls or self.get_or_create_owner_controls()
        updates: dict[str, object] = {}

        if "browser_control_enabled" in kwargs:
            updates["browser_control_enabled"] = bool(kwargs.get("browser_control_enabled"))
        if "stealth_headless_enabled" in kwargs:
            updates["stealth_headless_enabled"] = bool(kwargs.get("stealth_headless_enabled"))
        if "browser_allow_any_url" in kwargs:
            updates["browser_allow_any_url"] = bool(kwargs.get("browser_allow_any_url"))
        if "external_actions_enabled" in kwargs:
            updates["external_actions_enabled"] = bool(kwargs.get("external_actions_enabled"))
        if "scheduler_enabled" in kwargs:
            updates["scheduler_enabled"] = bool(kwargs.get("scheduler_enabled"))
        if "oauth_enabled" in kwargs:
            updates["oauth_enabled"] = bool(kwargs.get("oauth_enabled"))
        if "autonomy_mode" in kwargs:
            autonomy_mode = str(kwargs.get("autonomy_mode") or "").strip().lower()
            if autonomy_mode not in {"blocked", "supervised", "trusted_auto"}:
                raise ValueError("Unsupported autonomy_mode.")
            updates["autonomy_mode"] = autonomy_mode
        if "emergency_stop_enabled" in kwargs:
            updates["emergency_stop_enabled"] = bool(kwargs.get("emergency_stop_enabled"))
        if "pause_all_jobs" in kwargs:
            updates["pause_all_jobs"] = bool(kwargs.get("pause_all_jobs"))
        if "login_automation_enabled" in kwargs:
            updates["login_automation_enabled"] = bool(kwargs.get("login_automation_enabled"))
        if "two_factor_automation_enabled" in kwargs:
            updates["two_factor_automation_enabled"] = bool(kwargs.get("two_factor_automation_enabled"))
        if "publish_automation_enabled" in kwargs:
            updates["publish_automation_enabled"] = bool(kwargs.get("publish_automation_enabled"))
        if "public_upload_enabled" in kwargs:
            updates["public_upload_enabled"] = bool(kwargs.get("public_upload_enabled"))
        if "default_retry_budget" in kwargs:
            updates["default_retry_budget"] = max(0, int(kwargs.get("default_retry_budget") or 0))
        if "default_vram_floor_gb" in kwargs:
            updates["default_vram_floor_gb"] = max(1, int(kwargs.get("default_vram_floor_gb") or 1))
        if "voice_stt_engine" in kwargs:
            voice_stt_engine = str(kwargs.get("voice_stt_engine") or "").strip().lower()
            normalized_engine = voice_stt_engine
            if normalized_engine not in SUPPORTED_VOICE_STT_ENGINES:
                raise ValueError(f"Unsupported voice_stt_engine '{kwargs.get('voice_stt_engine')}'.")
            updates["voice_stt_engine"] = normalized_engine
        if "voice_stt_model_path" in kwargs:
            updates["voice_stt_model_path"] = self._normalize_optional_text(kwargs.get("voice_stt_model_path"))
        if "voice_stt_device" in kwargs:
            updates["voice_stt_device"] = self._normalize_optional_text(kwargs.get("voice_stt_device")) or "cpu"
        if "ambient_listening_enabled" in kwargs:
            updates["ambient_listening_enabled"] = bool(kwargs.get("ambient_listening_enabled"))
        if "ambient_wake_word" in kwargs:
            updates["ambient_wake_word"] = self._normalize_optional_text(kwargs.get("ambient_wake_word")) or "Atnos"
        if "ambient_mic_device" in kwargs:
            updates["ambient_mic_device"] = self._normalize_optional_text(kwargs.get("ambient_mic_device"))
        if "ambient_muted" in kwargs:
            updates["ambient_muted"] = bool(kwargs.get("ambient_muted"))
        if "ambient_context_enabled" in kwargs:
            updates["ambient_context_enabled"] = bool(kwargs.get("ambient_context_enabled"))
        if "trend_watch_enabled" in kwargs:
            updates["trend_watch_enabled"] = bool(kwargs.get("trend_watch_enabled"))
        if "trend_watch_query" in kwargs:
            updates["trend_watch_query"] = self._normalize_optional_text(kwargs.get("trend_watch_query")) or "short video hooks"
        if "trend_watch_region" in kwargs:
            updates["trend_watch_region"] = (self._normalize_optional_text(kwargs.get("trend_watch_region")) or "PL").upper()
        if "trend_watch_interval_minutes" in kwargs:
            updates["trend_watch_interval_minutes"] = max(30, int(kwargs.get("trend_watch_interval_minutes") or 30))
        if "trend_watch_last_run_at" in kwargs:
            updates["trend_watch_last_run_at"] = kwargs.get("trend_watch_last_run_at")
        if "voice_replies_enabled" in kwargs:
            updates["voice_replies_enabled"] = bool(kwargs.get("voice_replies_enabled"))
        if "voice_reply_volume" in kwargs:
            updates["voice_reply_volume"] = min(1.0, max(0.0, float(kwargs.get("voice_reply_volume") or 0.0)))
        if "voice_reply_voice_id" in kwargs:
            raw_voice_id = kwargs.get("voice_reply_voice_id")
            updates["voice_reply_voice_id"] = int(raw_voice_id) if raw_voice_id is not None else None
        if "last_user_interaction_at" in kwargs:
            updates["last_user_interaction_at"] = kwargs.get("last_user_interaction_at")
        if "last_briefing_at" in kwargs:
            updates["last_briefing_at"] = kwargs.get("last_briefing_at")

        updated_controls = self.controls.update(controls, **updates)
        return self._snapshot_from_controls(updated_controls)

    def mark_user_interaction(self, *, at: datetime | None = None) -> RuntimeControlSnapshot:
        return self.update_owner_controls(last_user_interaction_at=at or datetime.now(UTC))

    def mark_briefing_delivered(self, *, at: datetime | None = None) -> RuntimeControlSnapshot:
        return self.update_owner_controls(last_briefing_at=at or datetime.now(UTC))

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _ensure_utc_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @contextmanager
    def _trace(self, checkpoint: str) -> Iterator[None]:
        self._emit_progress(checkpoint, "start")
        started = perf_counter()
        try:
            yield
        except Exception:
            elapsed_ms = max(0, int((perf_counter() - started) * 1000))
            logger.exception(
                "Runtime settings checkpoint failed | checkpoint=%s | elapsed_ms=%s",
                checkpoint,
                elapsed_ms,
            )
            self._emit_progress(checkpoint, "failed")
            raise
        else:
            elapsed_ms = max(0, int((perf_counter() - started) * 1000))
            logger.info(
                "Runtime settings checkpoint complete | checkpoint=%s | elapsed_ms=%s",
                checkpoint,
                elapsed_ms,
            )
            self._emit_progress(checkpoint, "ok")

    def _emit_progress(self, checkpoint: str, outcome: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(checkpoint, outcome)
