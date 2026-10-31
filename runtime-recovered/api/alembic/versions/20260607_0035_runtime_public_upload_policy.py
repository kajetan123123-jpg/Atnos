"""runtime public upload policy

Revision ID: 20260607_0035
Revises: 20260523_0034
Create Date: 2026-06-07 20:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260607_0035"
down_revision = "20260523_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runtime_control_settings", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "public_upload_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("runtime_control_settings", recreate="auto") as batch_op:
        batch_op.drop_column("public_upload_enabled")
