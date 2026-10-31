"""operational memory triage metadata

Revision ID: 20260523_0034
Revises: 20260523_0033
Create Date: 2026-05-23 22:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260523_0034"
down_revision = "20260523_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operational_work_items", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "review_state",
                sa.Enum(
                    "UNREVIEWED",
                    "IN_REVIEW",
                    "REVIEWED",
                    name="operationalworkitemreviewstate",
                    native_enum=False,
                    length=24,
                ),
                nullable=False,
                server_default="UNREVIEWED",
            )
        )
        batch_op.add_column(sa.Column("blocked_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operational_work_items", recreate="auto") as batch_op:
        batch_op.drop_column("blocked_reason")
        batch_op.drop_column("review_state")
