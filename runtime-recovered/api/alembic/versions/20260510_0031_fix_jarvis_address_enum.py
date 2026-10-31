"""fix jarvis address enum values

Revision ID: 20260510_0031
Revises: 20260510_0030
Create Date: 2026-05-10 22:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0031"
down_revision = "20260510_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE execution_policies
            SET jarvis_address_as = CASE LOWER(TRIM(jarvis_address_as))
                WHEN 'none' THEN 'NONE'
                WHEN 'first_name' THEN 'FIRST_NAME'
                WHEN 'sir_madam' THEN 'SIR_MADAM'
                ELSE jarvis_address_as
            END
            """
        )
    )

    with op.batch_alter_table("execution_policies", recreate="auto") as batch_op:
        batch_op.alter_column(
            "jarvis_address_as",
            existing_type=sa.String(length=24),
            server_default="NONE",
            existing_nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE execution_policies
            SET jarvis_address_as = CASE jarvis_address_as
                WHEN 'NONE' THEN 'none'
                WHEN 'FIRST_NAME' THEN 'first_name'
                WHEN 'SIR_MADAM' THEN 'sir_madam'
                ELSE jarvis_address_as
            END
            """
        )
    )

    with op.batch_alter_table("execution_policies", recreate="auto") as batch_op:
        batch_op.alter_column(
            "jarvis_address_as",
            existing_type=sa.String(length=24),
            server_default="none",
            existing_nullable=False,
        )
