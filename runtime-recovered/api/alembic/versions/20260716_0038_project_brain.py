"""project brain evidence foundation

Revision ID: 20260716_0038
Revises: 20260709_0037
Create Date: 2026-07-16 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0038"
down_revision = "20260709_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_goals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=1024), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_project_goals_project_id"), "project_goals", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_goals_status"), "project_goals", ["status"], unique=False)

    op.create_table(
        "project_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("phase", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("readiness_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("working_json", sa.JSON(), nullable=False),
        sa.Column("failing_json", sa.JSON(), nullable=False),
        sa.Column("risks_json", sa.JSON(), nullable=False),
        sa.Column("open_questions_json", sa.JSON(), nullable=False),
        sa.Column("next_step_json", sa.JSON(), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_project_states_project_id"), "project_states", ["project_id"], unique=True)
    op.create_index(op.f("ix_project_states_readiness_status"), "project_states", ["readiness_status"], unique=False)

    op.create_table(
        "project_blockers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.String(length=240), nullable=False),
        sa.Column("title", sa.String(length=320), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=24), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=1024), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "source_key", name="uq_project_blockers_source_key"),
    )
    op.create_index(op.f("ix_project_blockers_project_id"), "project_blockers", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_blockers_status"), "project_blockers", ["status"], unique=False)

    op.create_table(
        "project_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_key", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=1024), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="observed"),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "fact_key", name="uq_project_facts_fact_key"),
    )
    op.create_index(op.f("ix_project_facts_project_id"), "project_facts", ["project_id"], unique=False)

    op.create_table(
        "project_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_trace_id", sa.Integer(), sa.ForeignKey("execution_traces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evidence_key", sa.String(length=240), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=1024), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "evidence_key", name="uq_project_evidence_key"),
    )
    op.create_index(op.f("ix_project_evidence_project_id"), "project_evidence", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_evidence_execution_trace_id"), "project_evidence", ["execution_trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_evidence_execution_trace_id"), table_name="project_evidence")
    op.drop_index(op.f("ix_project_evidence_project_id"), table_name="project_evidence")
    op.drop_table("project_evidence")
    op.drop_index(op.f("ix_project_facts_project_id"), table_name="project_facts")
    op.drop_table("project_facts")
    op.drop_index(op.f("ix_project_blockers_status"), table_name="project_blockers")
    op.drop_index(op.f("ix_project_blockers_project_id"), table_name="project_blockers")
    op.drop_table("project_blockers")
    op.drop_index(op.f("ix_project_states_readiness_status"), table_name="project_states")
    op.drop_index(op.f("ix_project_states_project_id"), table_name="project_states")
    op.drop_table("project_states")
    op.drop_index(op.f("ix_project_goals_status"), table_name="project_goals")
    op.drop_index(op.f("ix_project_goals_project_id"), table_name="project_goals")
    op.drop_table("project_goals")
