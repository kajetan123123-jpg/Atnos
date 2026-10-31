from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class RuntimeControlSettings(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "runtime_control_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    browser_control_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stealth_headless_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    browser_allow_any_url: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_actions_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    oauth_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    autonomy_mode: Mapped[str] = mapped_column(String(24), default="supervised", nullable=False)
    emergency_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pause_all_jobs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    login_automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    two_factor_automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publish_automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    public_upload_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_retry_budget: Mapped[int] = mapped_column(default=2, nullable=False)
    default_vram_floor_gb: Mapped[int] = mapped_column(default=4, nullable=False)
    voice_stt_engine: Mapped[str] = mapped_column(String(40), default="browser-speech", nullable=False)
    voice_stt_model_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    voice_stt_device: Mapped[str] = mapped_column(String(40), default="cpu", nullable=False)
    ambient_listening_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ambient_wake_word: Mapped[str] = mapped_column(String(120), default="Atnos", nullable=False)
    ambient_mic_device: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ambient_muted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ambient_context_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trend_watch_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trend_watch_query: Mapped[str] = mapped_column(String(255), default="short video hooks", nullable=False)
    trend_watch_region: Mapped[str] = mapped_column(String(16), default="PL", nullable=False)
    trend_watch_interval_minutes: Mapped[int] = mapped_column(default=180, nullable=False)
    trend_watch_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voice_replies_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    voice_reply_volume: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    voice_reply_voice_id: Mapped[int | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_user_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_briefing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="runtime_control_settings")
    voice_reply_voice: Mapped["VoiceProfile | None"] = relationship(foreign_keys=[voice_reply_voice_id])
