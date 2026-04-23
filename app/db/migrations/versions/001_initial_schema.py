"""initial schema: sync_state, sync_events, kv_store

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("sync_state",
        sa.Column("zoho_task_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("todoist_task_id", sa.String(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zoho_last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("orphan_check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_sync_state_todoist_task_id", "sync_state", ["todoist_task_id"])

    op.create_table("sync_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("zoho_task_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("detail", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_sync_events_created_at", "sync_events", ["created_at"])
    op.create_index(
        "idx_sync_events_zoho_task_id_created_at",
        "sync_events",
        ["zoho_task_id", "created_at"],
    )

    op.create_table("kv_store",
        sa.Column("key", sa.String(), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("kv_store")
    op.drop_index("idx_sync_events_zoho_task_id_created_at", table_name="sync_events")
    op.drop_index("idx_sync_events_created_at", table_name="sync_events")
    op.drop_table("sync_events")
    op.drop_index("idx_sync_state_todoist_task_id", table_name="sync_state")
    op.drop_table("sync_state")
