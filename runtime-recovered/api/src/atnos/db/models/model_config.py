from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ModelQualityTier, ModelTask


class ModelConfig(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        Index(
            "uq_model_configs_provider_task_default_true",
            "provider_id",
            "task",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
    )

    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    runtime_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    task: Mapped[ModelTask] = mapped_column(
        Enum(ModelTask, native_enum=False, length=32),
        nullable=False,
    )
    quality_tier: Mapped[ModelQualityTier] = mapped_column(
        Enum(ModelQualityTier, native_enum=False, length=16),
        default=ModelQualityTier.STANDARD,
        nullable=False,
    )
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    provider: Mapped["Provider"] = relationship(back_populates="model_configs")
