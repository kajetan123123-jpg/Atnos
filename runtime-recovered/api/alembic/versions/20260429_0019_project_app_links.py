"""project app links metadata layer

Revision ID: 20260429_0019
Revises: 20260429_0018
Create Date: 2026-04-29 18:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_0019"
down_revision = "20260429_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS _alembic_tmp_creator_projects"))

    with op.batch_alter_table("creator_projects", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_creator_projects_project_id"),
            ["project_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_creator_projects_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "project_app_links",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("integration_key", sa.String(length=120), nullable=False),
        sa.Column("custom_integration_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("permission_profile", sa.String(length=32), nullable=False),
        sa.Column("allowed_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("safety_notes", sa.Text(), nullable=True),
        sa.Column("is_default_for_project", sa.Boolean(), nullable=False),
        sa.Column("is_default_for_content", sa.Boolean(), nullable=False),
        sa.Column("is_default_for_analytics", sa.Boolean(), nullable=False),
        sa.Column("is_default_for_storage", sa.Boolean(), nullable=False),
        sa.Column("is_default_for_publish_preview", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["custom_integration_id"], ["custom_integrations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_app_links_project_id"), "project_app_links", ["project_id"], unique=False)
    op.create_index(
        op.f("ix_project_app_links_integration_key"),
        "project_app_links",
        ["integration_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_app_links_custom_integration_id"),
        "project_app_links",
        ["custom_integration_id"],
        unique=False,
    )
    op.create_index(op.f("ix_project_app_links_account_id"), "project_app_links", ["account_id"], unique=False)

    creator_projects = sa.table(
        "creator_projects",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("project_id", sa.Integer()),
    )
    projects = sa.table(
        "projects",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("status", sa.String()),
        sa.column("is_pinned", sa.Boolean()),
    )

    rows = list(bind.execute(sa.select(creator_projects.c.id, creator_projects.c.name)))
    for creator_project_id, name in rows:
        slug = f"creator-project-{creator_project_id}"
        bind.execute(
            projects.insert().values(
                name=name,
                slug=slug,
                status="active",
                is_pinned=False,
            )
        )
        project_id = bind.execute(
            sa.select(projects.c.id).where(projects.c.slug == slug)
        ).scalar_one()
        bind.execute(
            creator_projects.update()
            .where(creator_projects.c.id == creator_project_id)
            .values(project_id=project_id)
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_app_links_account_id"), table_name="project_app_links")
    op.drop_index(op.f("ix_project_app_links_custom_integration_id"), table_name="project_app_links")
    op.drop_index(op.f("ix_project_app_links_integration_key"), table_name="project_app_links")
    op.drop_index(op.f("ix_project_app_links_project_id"), table_name="project_app_links")
    op.drop_table("project_app_links")
    with op.batch_alter_table("creator_projects", recreate="auto") as batch_op:
        batch_op.drop_constraint("fk_creator_projects_project_id_projects", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_creator_projects_project_id"))
        batch_op.drop_column("project_id")
