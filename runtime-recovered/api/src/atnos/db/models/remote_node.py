from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import (
    RemoteDisplayMode,
    RemoteExecutionExecutor,
    RemoteExecutionOrigin,
    RemoteExecutionStatus,
    RemoteExecutionStepStatus,
    RemoteNodeStatus,
    RemoteNodeTransport,
    RemoteNodeTrustMode,
    RemoteRoutingTarget,
)


class RemoteNode(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_nodes"

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    node_type: Mapped[str] = mapped_column(String(40), nullable=False, default="ubuntu-worker")
    platform: Mapped[str] = mapped_column(String(40), nullable=False, default="linux")
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(120), nullable=False, default="atnos")
    status: Mapped[RemoteNodeStatus] = mapped_column(
        Enum(RemoteNodeStatus, native_enum=False, length=24),
        nullable=False,
        default=RemoteNodeStatus.PROVISIONING,
        index=True,
    )
    trust_mode: Mapped[RemoteNodeTrustMode] = mapped_column(
        Enum(RemoteNodeTrustMode, native_enum=False, length=24),
        nullable=False,
        default=RemoteNodeTrustMode.TRUSTED_AUTO,
    )
    transport: Mapped[RemoteNodeTransport] = mapped_column(
        Enum(RemoteNodeTransport, native_enum=False, length=24),
        nullable=False,
        default=RemoteNodeTransport.WEBSOCKET,
    )
    display_mode: Mapped[RemoteDisplayMode] = mapped_column(
        Enum(RemoteDisplayMode, native_enum=False, length=24),
        nullable=False,
        default=RemoteDisplayMode.UNKNOWN,
    )
    browser_default_target: Mapped[str] = mapped_column(String(40), nullable=False, default="personal_profile")
    public_capabilities_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)


class RemoteNodeCredential(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_node_credentials"

    node_id: Mapped[int] = mapped_column(
        ForeignKey("remote_nodes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    enrollment_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cert_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotation_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemoteNodeSession(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_node_sessions"

    node_id: Mapped[int] = mapped_column(
        ForeignKey("remote_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transport: Mapped[RemoteNodeTransport] = mapped_column(
        Enum(RemoteNodeTransport, native_enum=False, length=24),
        nullable=False,
        default=RemoteNodeTransport.WEBSOCKET,
    )
    remote_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RemoteNodeCapabilitySnapshot(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_node_capability_snapshots"

    node_id: Mapped[int] = mapped_column(
        ForeignKey("remote_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cpu_percent: Mapped[float | None] = mapped_column(nullable=True)
    load_avg_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    ram_total_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_free_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_free_gb: Mapped[float | None] = mapped_column(nullable=True)
    network_online: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sudo_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ollama_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    browser_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    desktop_bridge_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ffmpeg_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    docker_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_mode: Mapped[RemoteDisplayMode] = mapped_column(
        Enum(RemoteDisplayMode, native_enum=False, length=24),
        nullable=False,
        default=RemoteDisplayMode.UNKNOWN,
    )
    browser_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="personal_profile")
    python_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ffmpeg_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RemoteExecutionRun(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_execution_runs"

    origin: Mapped[RemoteExecutionOrigin] = mapped_column(
        Enum(RemoteExecutionOrigin, native_enum=False, length=24),
        nullable=False,
        default=RemoteExecutionOrigin.MANUAL,
    )
    requested_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    task_kind: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    routing_target: Mapped[RemoteRoutingTarget] = mapped_column(
        Enum(RemoteRoutingTarget, native_enum=False, length=24),
        nullable=False,
        default=RemoteRoutingTarget.REMOTE,
    )
    assigned_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[RemoteExecutionStatus] = mapped_column(
        Enum(RemoteExecutionStatus, native_enum=False, length=24),
        nullable=False,
        default=RemoteExecutionStatus.QUEUED,
        index=True,
    )
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    fallback_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemoteExecutionStep(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_execution_steps"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("remote_execution_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    executor: Mapped[RemoteExecutionExecutor] = mapped_column(
        Enum(RemoteExecutionExecutor, native_enum=False, length=24),
        nullable=False,
        default=RemoteExecutionExecutor.WORKER_CORE,
    )
    action_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    command: Mapped[str | None] = mapped_column(Text(), nullable=True)
    browser_target: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sudo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[RemoteExecutionStepStatus] = mapped_column(
        Enum(RemoteExecutionStepStatus, native_enum=False, length=24),
        nullable=False,
        default=RemoteExecutionStepStatus.QUEUED,
    )
    stdout: Mapped[str | None] = mapped_column(Text(), nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text(), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemoteRoutingPolicy(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "remote_routing_policies"

    task_kind: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    preferred_target: Mapped[RemoteRoutingTarget] = mapped_column(
        Enum(RemoteRoutingTarget, native_enum=False, length=24),
        nullable=False,
        default=RemoteRoutingTarget.REMOTE,
    )
    windows_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remote_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    required_capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
