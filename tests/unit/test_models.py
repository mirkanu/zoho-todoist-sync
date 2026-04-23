# tests/unit/test_models.py
from sqlalchemy.dialects.postgresql import JSONB
from app.db.models import Base, SyncState, SyncEvent, KVStore


def test_sync_state_tablename():
    assert SyncState.__tablename__ == "sync_state"


def test_sync_state_columns_exist():
    cols = {c.name for c in SyncState.__table__.columns}
    assert cols == {
        "zoho_task_id", "todoist_task_id", "last_hash",
        "last_synced_at", "zoho_last_seen", "orphan_check_count",
        "created_at",
    }


def test_sync_state_primary_key():
    pk_cols = {c.name for c in SyncState.__table__.primary_key.columns}
    assert pk_cols == {"zoho_task_id"}


def test_sync_state_last_hash_is_string_64():
    col = SyncState.__table__.columns["last_hash"]
    assert col.type.length == 64
    assert not col.nullable


def test_sync_state_last_synced_at_is_timezone_aware():
    col = SyncState.__table__.columns["last_synced_at"]
    assert col.type.timezone is True
    assert not col.nullable


def test_sync_state_has_todoist_task_id_index():
    names = {ix.name for ix in SyncState.__table__.indexes}
    assert "idx_sync_state_todoist_task_id" in names


def test_sync_events_tablename():
    assert SyncEvent.__tablename__ == "sync_events"


def test_sync_events_columns_exist():
    cols = {c.name for c in SyncEvent.__table__.columns}
    assert cols == {"id", "zoho_task_id", "action", "source", "detail", "created_at"}


def test_sync_events_detail_is_jsonb():
    col = SyncEvent.__table__.columns["detail"]
    assert isinstance(col.type, JSONB)


def test_sync_events_action_is_string_32():
    col = SyncEvent.__table__.columns["action"]
    assert col.type.length == 32
    assert not col.nullable


def test_sync_events_source_is_string_32():
    col = SyncEvent.__table__.columns["source"]
    assert col.type.length == 32
    assert not col.nullable


def test_sync_events_indexes():
    names = {ix.name for ix in SyncEvent.__table__.indexes}
    assert "idx_sync_events_created_at" in names
    assert "idx_sync_events_zoho_task_id_created_at" in names


def test_kv_store_tablename():
    assert KVStore.__tablename__ == "kv_store"


def test_kv_store_columns():
    cols = {c.name for c in KVStore.__table__.columns}
    assert cols == {"key", "value", "updated_at"}


def test_kv_store_primary_key():
    pk_cols = {c.name for c in KVStore.__table__.primary_key.columns}
    assert pk_cols == {"key"}


def test_all_tables_in_base_metadata():
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {"sync_state", "sync_events", "kv_store"}
