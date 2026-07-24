from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, Index, Integer,
    String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SyncState(Base):
    __tablename__ = "sync_state"
    zoho_task_id     = Column(String, primary_key=True)
    external_task_id = Column(String, nullable=False)
    provider         = Column(String(16), nullable=False, server_default="todoist")
    last_hash        = Column(String(64), nullable=False)
    last_synced_at   = Column(DateTime(timezone=True), nullable=False)
    zoho_last_seen   = Column(DateTime(timezone=True), nullable=True)
    orphan_check_count = Column(Integer, nullable=False, default=0)
    created_at       = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_sync_state_external_task_id", "external_task_id"),
        CheckConstraint("provider IN ('todoist', 'nirvana')", name="ck_sync_state_provider"),
    )


class SyncEvent(Base):
    __tablename__ = "sync_events"
    id           = Column(BigInteger, primary_key=True, autoincrement=True)
    zoho_task_id = Column(String, nullable=False)
    action       = Column(String(32), nullable=False)
    source       = Column(String(32), nullable=False)
    detail       = Column(JSONB, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_sync_events_created_at", "created_at"),
        Index("idx_sync_events_zoho_task_id_created_at", "zoho_task_id", "created_at"),
    )


class KVStore(Base):
    __tablename__ = "kv_store"
    key        = Column(String, primary_key=True)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        onupdate=func.now())
