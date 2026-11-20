"""ask_chat(): điểm vào luồng chat chỉ đọc (Tab 3 – Hỏi đáp).

Chủ file: Mai Anh (M04). Chữ ký hàm: xem docs/contracts.md mục 10.

    ask_chat(question, user, llm=None) -> {"answer": str, "refused": bool}

Các bước:
1. Chặn câu hỏi rỗng hoặc dài quá 500 ký tự TRƯỚC khi gửi Gemini (không tốn lượt gọi).
2. Gọi core_agent.run_agent ở chế độ READ_ONLY_CHAT – Gemini chỉ có 4 tool đọc,
   mọi hàm ghi (create_draft_po…) bị chặn trong core_agent.
3. Ghi audit log: CHAT_REFUSED nếu bị từ chối, ngược lại CHAT_ANSWERED.

Hàm không bao giờ ghi gì ngoài 1 dòng audit log, và không raise khi Gemini lỗi
(core_agent đã trả câu dự phòng). Ghi log lỗi thì vẫn trả câu trả lời cho người dùng.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.core_agent import run_agent
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


def _log(user: Any, refused: bool, detail: dict) -> None:
    event = "CHAT_REFUSED" if refused else "CHAT_ANSWERED"
    try:
        write_audit_log(_actor(user), event, entity_type="chat", detail=detail)
    except Exception:  # noqa: BLE001 – lỗi ghi log không được làm hỏng câu trả lời
        logger.exception("Không ghi được audit log %s", event)


def _summarize_trace(trace: list[dict]) -> dict:
    """Rút gọn trace của core_agent để lưu vào detail_json của audit log."""
    fallback = next((t.get("reason") for t in trace if t.get("step") == "fallback"), None)
    return {
        "tools_called": [t["tool_name"] for t in trace if t.get("step") == "tool_call"],
        "tools_blocked": [t["tool_name"] for t in trace if t.get("step") == "tool_blocked"],
        "fallback": fallback,
    }


def ask_chat(question: str, user: Any, llm: Any = None) -> dict:
    """Trả lời câu hỏi kho trong chế độ chỉ đọc (contracts mục 10).

    `llm=None` → Gemini thật; test truyền FakeLLM().
    """
    text = (question or "").strip()

    if not text:
        result = {"answer": MSG_EMPTY, "refused": True}
        _log(user, True, {"reason": "EMPTY", "question": ""})
        return result

    if len(text) > MAX_QUESTION_CHARS:
        result = {"answer": MSG_TOO_LONG, "refused": True}
        _log(user, True, {"reason": "TOO_LONG", "length": len(text),
                          "question": text[:_DETAIL_LIMIT]})
        return result

    trace: list[dict] = []
    result = run_agent("READ_ONLY_CHAT", {"question": text}, llm=llm, trace=trace)
    answer = {"answer": str(result.get("answer", "")), "refused": bool(result.get("refused", False))}

    detail = {
        "question": text[:_DETAIL_LIMIT],
        "answer": answer["answer"][:_DETAIL_LIMIT],
        **_summarize_trace(trace),
    }
    if answer["refused"]:
        detail["reason"] = "WRITE_OR_OUT_OF_SCOPE"
    _log(user, answer["refused"], detail)
    return answer
