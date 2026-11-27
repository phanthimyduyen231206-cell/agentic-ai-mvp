"""ask_chat(): điểm vào luồng chat chỉ đọc (Tab 3 – Hỏi đáp).

Chủ file: Mai Anh (M04, MA2). Chữ ký hàm: xem docs/contracts.md mục 10.

    ask_chat(question, user, history=None, llm=None) -> {"answer": str, "refused": bool, "fallback": bool}

- history: các lượt hỏi đáp trước trong phiên, dạng [{"question", "answer", "refused"}]
  (đúng như st.session_state["chat_history"] của app.py). Chỉ 4 lượt gần nhất, mỗi câu ≤ 300 ký tự
  được gửi kèm để Gemini hiểu câu nối tiếp ("còn lô nào nữa?").
- fallback = True khi câu trả lời là câu dự phòng do code sinh (Gemini lỗi, JSON sai, số lệch,
  quá vòng tool). Giao diện dùng cờ này để gắn nhãn "Câu dự phòng".

Các bước:
1. Chặn câu hỏi rỗng hoặc dài quá 500 ký tự TRƯỚC khi gửi Gemini (không tốn lượt gọi).
2. Gọi core_agent.run_agent ở chế độ READ_ONLY_CHAT – Gemini chỉ có 4 tool đọc,
   mọi hàm ghi (create_draft_po…) bị chặn trong core_agent.
3. Ghi audit log đúng 1 dòng:
   - CHAT_REFUSED: câu hỏi rỗng / quá dài / đòi ghi dữ liệu / ngoài phạm vi;
   - CHAT_FAILED: dùng câu dự phòng (không tính là đã trả lời, contracts mục 9);
   - CHAT_ANSWERED: Gemini trả lời bình thường, đã qua hậu kiểm số liệu.

Hàm không bao giờ ghi gì ngoài 1 dòng audit log, và không raise khi Gemini lỗi
(core_agent đã trả câu dự phòng). Ghi log lỗi thì vẫn trả câu trả lời cho người dùng.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.core_agent import clean_history, run_agent
from services.audit_log import write_audit_log

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 500

MSG_EMPTY = "Anh/chị nhập câu hỏi về tồn kho, lô hàng, đơn đặt trước, NCC hoặc PO giúp mình nhé."
MSG_TOO_LONG = (
    f"Câu hỏi dài quá {MAX_QUESTION_CHARS} ký tự. Anh/chị rút gọn lại giúp mình, "
    "ví dụ: \"Kem tươi còn bao nhiêu?\""
)

_DETAIL_LIMIT = 500  # cắt bớt câu hỏi / câu trả lời khi lưu vào audit log


def _actor(user: Any) -> str:
    """username trong phiên đăng nhập (contracts mục 0); thiếu thì ghi 'unknown'."""
    if isinstance(user, dict) and user.get("username"):
        return str(user["username"])
    return "unknown"


def _log(user: Any, event: str, detail: dict) -> None:
    try:
        write_audit_log(_actor(user), event, entity_type="chat", detail=detail)
    except Exception:  # noqa: BLE001 – lỗi ghi log không được làm hỏng câu trả lời
        logger.exception("Không ghi được audit log %s", event)


def _summarize_trace(trace: list[dict]) -> dict:
    """Rút gọn trace của core_agent để lưu vào detail_json của audit log."""
    fallback = next((t.get("reason") for t in trace if t.get("step") == "fallback"), None)
    summary = {
        "tools_called": [t["tool_name"] for t in trace if t.get("step") == "tool_call"],
        "tools_blocked": [t["tool_name"] for t in trace if t.get("step") == "tool_blocked"],
        "tool_rounds_exceeded": any(t.get("step") == "tool_rounds_exceeded" for t in trace),
        "fallback": fallback,
    }
    error = next((t.get("error") for t in trace if t.get("step") == "llm_error"), None)
    if error:
        summary["error"] = error
    numbers = next((t.get("numbers") for t in trace if t.get("reason") == "numbers_mismatch"), None)
    if numbers:
        summary["numbers_mismatch"] = numbers
    url = next((t.get("url") for t in trace if t.get("step") == "trace" and t.get("url")), None)
    if url:  # chỉ có khi LANGSMITH_TRACING=true (giao ước G2)
        summary["trace_url"] = url
    return summary


def ask_chat(question: str, user: Any, history: Any = None, llm: Any = None) -> dict:
    """Trả lời câu hỏi kho trong chế độ chỉ đọc (contracts mục 10).

    `history` = các lượt trước [{"question", "answer", "refused"}] hoặc None.
    `llm=None` → Gemini thật; test truyền FakeLLM().
    """
    text = (question or "").strip()

    if not text:
        _log(user, "CHAT_REFUSED", {"reason": "EMPTY", "question": ""})
        return {"answer": MSG_EMPTY, "refused": True, "fallback": False}

    if len(text) > MAX_QUESTION_CHARS:
        _log(user, "CHAT_REFUSED", {"reason": "TOO_LONG", "length": len(text),
                                    "question": text[:_DETAIL_LIMIT]})
        return {"answer": MSG_TOO_LONG, "refused": True, "fallback": False}

    turns = clean_history(history)
    trace: list[dict] = []
    result = run_agent("READ_ONLY_CHAT", {"question": text, "history": turns}, llm=llm, trace=trace,
                       meta={"user": _actor(user)})
    summary = _summarize_trace(trace)
    answer = {
        "answer": str(result.get("answer", "")),
        "refused": bool(result.get("refused", False)),
        "fallback": summary["fallback"] is not None,
    }

    detail = {
        "question": text[:_DETAIL_LIMIT],
        "answer": answer["answer"][:_DETAIL_LIMIT],
        "history_turns": len(turns),
        **summary,
    }
    if answer["fallback"]:
        event = "CHAT_FAILED"
    elif answer["refused"]:
        event = "CHAT_REFUSED"
        detail["reason"] = "WRITE_OR_OUT_OF_SCOPE"
    else:
        event = "CHAT_ANSWERED"
    _log(user, event, detail)
    return answer
