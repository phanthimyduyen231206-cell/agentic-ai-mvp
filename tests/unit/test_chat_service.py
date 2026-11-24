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
    assert set(out) == {"answer", "refused", "fallback"}
    assert out["refused"] is False and out["answer"] and out["fallback"] is False
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
    assert out == {"answer": MSG_TOO_LONG, "refused": True, "fallback": False}
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
    assert out == {"answer": MSG_EMPTY, "refused": True, "fallback": False}
    assert llm.call_count == 0
    assert _logs("CHAT_REFUSED")[0]["detail"]["reason"] == "EMPTY"


@pytest.mark.parametrize("behavior", ["error", "invalid_json"])
def test_gemini_failure_returns_fallback_and_logs_chat_failed(db, behavior):
    """MA2: dùng câu dự phòng → ghi CHAT_FAILED, KHÔNG ghi CHAT_ANSWERED; trả fallback = True."""
    out = ask_chat("Bơ lạt còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior=behavior))
    assert out["refused"] is False and out["answer"] and out["fallback"] is True
    assert _logs("CHAT_ANSWERED") == []
    log = _logs("CHAT_FAILED")[0]
    assert log["actor"] == "quanly"
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


# ---------------------------------------------------------------------------
# MA2: lịch sử hội thoại, CHAT_FAILED, vòng tool
# ---------------------------------------------------------------------------
def test_follow_up_question_uses_history(db):
    llm = FakeLLM(behavior="tool_call")
    history = [{"question": "Bơ lạt còn bao nhiêu?", "answer": "Bơ lạt còn đủ dùng.", "refused": False}]
    out = ask_chat("Thế còn mấy lô?", MANAGER, history=history, llm=llm)
    assert out["refused"] is False and out["fallback"] is False
    assert llm.calls[1]["tool_results"][0]["arguments"]["ingredient_id"] == "BO_LAT"
    detail = _logs("CHAT_ANSWERED")[0]["detail"]
    assert detail["history_turns"] == 1 and detail["question"] == "Thế còn mấy lô?"


def test_only_last_4_turns_sent(db):
    llm = FakeLLM()
    history = [{"question": f"Câu cũ số {i}", "answer": "ok", "refused": False} for i in range(1, 8)]
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, history=history, llm=llm)
    msg = llm.calls[0]["user_message"]
    assert "Câu cũ số 3" not in msg and all(f"Câu cũ số {i}" in msg for i in (4, 5, 6, 7))
    assert _logs("CHAT_ANSWERED")[0]["detail"]["history_turns"] == 4


def test_made_up_numbers_logged_as_chat_failed(db):
    out = ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior="made_up_numbers"))
    assert out["fallback"] is True
    detail = _logs("CHAT_FAILED")[0]["detail"]
    assert detail["fallback"] == "numbers_mismatch" and "42" in detail["numbers_mismatch"]


def test_parallel_tools_logged(db):
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior="parallel_tool_calls"))
    detail = _logs("CHAT_ANSWERED")[0]["detail"]
    assert detail["tools_called"] == ["check_inventory_fefo", "calc_preorder_demand"]


def test_over_six_rounds_answered_not_failed(db):
    out = ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior="tool_loop"))
    assert out["fallback"] is False and out["refused"] is False
    detail = _logs("CHAT_ANSWERED")[0]["detail"]
    assert detail["tool_rounds_exceeded"] is True and len(detail["tools_called"]) == 6
