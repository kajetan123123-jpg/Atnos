from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class MediaPipelinePreset(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "media_pipeline_presets"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_platform: Mapped[str] = mapped_column(String(120), nullable=False, default="youtube")
    tts_engine: Mapped[str] = mapped_column(String(120), nullable=False, default="kokoro")
    image_provider: Mapped[str] = mapped_column(String(120), nullable=False, default="comfyui")
    video_renderer: Mapped[str] = mapped_column(String(120), nullable=False, default="moviepy-ffmpeg")
    subtitle_engine: Mapped[str] = mapped_column(String(120), nullable=False, default="faster-whisper")
    prompt_style: Mapped[str | None] = mapped_column(Text(), nullable=True)
    target_duration_sec: Mapped[int] = mapped_column(nullable=False, default=40)
    vram_floor_gb: Mapped[int] = mapped_column(nullable=False, default=4)
    publish_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="trusted_auto")
    cooldown_hours: Mapped[int] = mapped_column(nullable=False, default=24)
    steps_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
