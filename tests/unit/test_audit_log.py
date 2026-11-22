import json
import sqlite3

import pytest

from services.audit_log import write_audit_log

# Bảng tạm cho test; schema thật do data/init_db.py (P01) tạo, cột phải giống bảng này.
SCHEMA = """CREATE TABLE audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT NOT NULL,
    event_type TEXT NOT NULL, entity_type TEXT, entity_id TEXT, run_id INTEGER, detail_json TEXT)"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as conn:
        conn.execute(SCHEMA)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    monkeypatch.setenv("DEMO_DATE", "2026-11-20T08:00")
    return path


def test_write_and_read_back(db):
    log_id = write_audit_log("duyen", "PO_APPROVED", "purchase_order", 7, detail={"note": "duyệt"})
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT created_at, actor, event_type, entity_id, detail_json FROM audit_logs "
                           "WHERE log_id = ?", (log_id,)).fetchone()
    assert row[:4] == ("2026-11-20T08:00:00", "duyen", "PO_APPROVED", "7")
    assert json.loads(row[4]) == {"note": "duyệt"}


def test_unknown_event_is_rejected_and_not_written(db):
    with pytest.raises(ValueError):
        write_audit_log("duyen", "PO_DELETED")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0


def test_shares_caller_transaction(db):
    conn = sqlite3.connect(db)
    write_audit_log("system", "RUN_STARTED", conn=conn)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    conn.close()


def test_chat_failed_is_an_event_type():
    """DU1: Gemini lỗi thì chat ghi CHAT_FAILED, tách khỏi CHAT_ANSWERED."""
    from services.audit_log import EVENT_TYPES
    assert "CHAT_FAILED" in EVENT_TYPES
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES))


def test_own_connection_goes_through_repository(db, monkeypatch):
    """DU1: tự mở kết nối qua repository.get_connection(), không gọi sqlite3.connect trực tiếp."""
    import services.audit_log as audit_log
    calls = []
    real = audit_log.get_connection

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(audit_log, "get_connection", spy)
    write_audit_log("system", "RUN_STARTED")
    assert calls == [1]
