"""Unit test cho services/chat_service.py (M04) – chạy bằng FakeLLM, DB tạm, không cần mạng.

Chạy: pytest tests/unit/test_chat_service.py
"""
import json
from datetime import datetime

import pytest

from data import repository as repo
from data.init_db import init_db
from services import chat_service
from services.chat_service import MAX_QUESTION_CHARS, MSG_EMPTY, MSG_TOO_LONG, ask_chat
from tests.fake_llm import FakeLLM

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}
STAFF = {"username": "nhanvien", "role": "STAFF"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "chat.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def _logs(event_type=None):
    rows = repo.get_audit_logs(event_type=event_type)
    for r in rows:
        r["detail"] = json.loads(r.get("detail_json") or "{}")
    return rows


def test_normal_question_answered_and_logged(db):
    out = ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM())
    assert set(out) == {"answer", "refused"}
    assert out["refused"] is False and out["answer"]
    logs = _logs("CHAT_ANSWERED")
    assert len(logs) == 1
    assert logs[0]["actor"] == "quanly"
    assert logs[0]["detail"]["question"] == "Kem tươi còn bao nhiêu?"
    assert _logs("CHAT_REFUSED") == []


def test_staff_can_also_chat(db):
    out = ask_chat("Bơ lạt còn bao nhiêu?", STAFF, llm=FakeLLM())
    assert out["refused"] is False
    assert _logs("CHAT_ANSWERED")[0]["actor"] == "nhanvien"


def test_write_request_refused_and_logged(db):
    out = ask_chat("Duyệt PO-004 giúp mình", MANAGER, llm=FakeLLM())
    assert out["refused"] is True
    logs = _logs("CHAT_REFUSED")
    assert len(logs) == 1 and logs[0]["detail"]["reason"] == "WRITE_OR_OUT_OF_SCOPE"


def test_question_over_500_chars_blocked_before_gemini(db):
    llm = FakeLLM()
    out = ask_chat("a" * (MAX_QUESTION_CHARS + 1), MANAGER, llm=llm)
    assert out == {"answer": MSG_TOO_LONG, "refused": True}
    assert llm.call_count == 0
    log = _logs("CHAT_REFUSED")[0]
    assert log["detail"]["reason"] == "TOO_LONG" and log["detail"]["length"] == MAX_QUESTION_CHARS + 1


def test_question_exactly_500_chars_is_allowed(db):
    llm = FakeLLM()
    out = ask_chat("b" * MAX_QUESTION_CHARS, MANAGER, llm=llm)
    assert out["refused"] is False
    assert llm.call_count == 1


def test_question_length_counts_after_strip(db):
    llm = FakeLLM()
    ask_chat("   " + "c" * MAX_QUESTION_CHARS + "   ", MANAGER, llm=llm)
    assert llm.call_count == 1


@pytest.mark.parametrize("question", ["", "   ", None])
def test_empty_question_refused_without_gemini(db, question):
    llm = FakeLLM()
    out = ask_chat(question, MANAGER, llm=llm)
    assert out == {"answer": MSG_EMPTY, "refused": True}
    assert llm.call_count == 0
    assert _logs("CHAT_REFUSED")[0]["detail"]["reason"] == "EMPTY"


@pytest.mark.parametrize("behavior", ["error", "invalid_json"])
def test_gemini_failure_returns_fallback_and_logs(db, behavior):
    out = ask_chat("Bơ lạt còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior=behavior))
    assert out["refused"] is False and out["answer"]
    log = _logs("CHAT_ANSWERED")[0]
    assert log["detail"]["fallback"] in ("llm_failed", "invalid_json")


def test_tool_call_recorded_in_log(db):
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior="tool_call"))
    detail = _logs("CHAT_ANSWERED")[0]["detail"]
    assert detail["tools_called"] == ["check_inventory_fefo"]
    assert detail["tools_blocked"] == []


def test_forbidden_tool_blocked_and_recorded(db):
    before = len(repo.get_purchase_orders())
    ask_chat("Tạo PO giúp mình", MANAGER, llm=FakeLLM(behavior="forbidden_tool"))
    assert len(repo.get_purchase_orders()) == before
    logs = _logs()
    assert "create_draft_po" in logs[-1]["detail"]["tools_blocked"]
    assert not any(r["event_type"] == "PO_CREATED" for r in logs)


def test_missing_user_logged_as_unknown(db):
    ask_chat("Kem tươi còn bao nhiêu?", None, llm=FakeLLM())
    assert _logs("CHAT_ANSWERED")[0]["actor"] == "unknown"


def test_audit_log_failure_does_not_break_answer(db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("DB bị khóa")

    monkeypatch.setattr(chat_service, "write_audit_log", broken)
    out = ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM())
    assert out["refused"] is False and out["answer"]
