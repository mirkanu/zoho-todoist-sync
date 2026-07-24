"""rename todoist_task_id to external_task_id, add provider column

Revision ID: 002_add_provider_column
Revises: 001_initial_schema
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa

revision = "002_add_provider_column"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename first so the new column below can reference the final name.
    op.alter_column("sync_state", "todoist_task_id", new_column_name="external_task_id")

    # NOT NULL + server_default backfills every existing row to 'todoist' in the
    # same ALTER TABLE statement (Postgres applies server_default to existing rows).
    # Every sync_state row has been Todoist-only since v1.0 shipped 2026-05-01 (D-12).
    op.add_column(
        "sync_state",
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="todoist"),
    )
    op.create_check_constraint(
        "ck_sync_state_provider", "sync_state", "provider IN ('todoist', 'nirvana')"
    )

    op.drop_index("idx_sync_state_todoist_task_id", table_name="sync_state")
    op.create_index("idx_sync_state_external_task_id", "sync_state", ["external_task_id"])


def downgrade() -> None:
    op.drop_index("idx_sync_state_external_task_id", table_name="sync_state")
    op.create_index("idx_sync_state_todoist_task_id", "sync_state", ["external_task_id"])
    op.drop_constraint("ck_sync_state_provider", "sync_state", type_="check")
    op.drop_column("sync_state", "provider")
    op.alter_column("sync_state", "external_task_id", new_column_name="todoist_task_id")
