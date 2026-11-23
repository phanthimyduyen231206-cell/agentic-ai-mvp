"""Ghi nhật ký hệ thống vào bảng audit_logs.

Chủ file: Duyên (D04). Đây là hàm của application code; Gemini không gọi được (không có trong tool registry).
"""
import json

from data.repository import get_connection
from services import clock

# Danh sách sự kiện duy nhất của hệ thống (đặc tả Mục 7). init_db.py dùng chung danh sách này
# cho ràng buộc CHECK của bảng audit_logs.
EVENT_TYPES = (
    "RUN_STARTED", "RUN_SUCCEEDED", "RUN_FAILED", "RUN_REJECTED_DUPLICATE",
    "ALERT_CREATED", "OPTION_SELECTED", "ALERT_DISMISSED",
    "PO_CREATED", "PO_APPROVED", "PO_REJECTED", "PO_CANCELLED", "PO_SEND_FAILED", "PO_SENT",
    "DELIVERY_DELAYED", "DELIVERY_RECEIVED", "BATCH_RECEIVED", "STOCK_ISSUED",
    "GUARDRAIL_BLOCKED", "CHAT_ANSWERED", "CHAT_REFUSED",
    "CHAT_FAILED",  # Gemini lỗi, chat trả câu dự phòng (không tính là đã trả lời)
)


def write_audit_log(actor, event_type, entity_type=None, entity_id=None, run_id=None, detail=None, conn=None):
    """Ghi một dòng nhật ký và trả log_id.

    Truyền `conn` khi đang trong một transaction để dòng log commit/rollback cùng thao tác chính.
    Không truyền `conn` thì hàm tự mở kết nối và tự commit.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type không hợp lệ: {event_type!r}")
    if not actor:
        raise ValueError("actor không được để trống")

    own = conn is None
    if own:
        conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO audit_logs (created_at, actor, event_type, entity_type, entity_id, run_id, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                clock.now().isoformat(timespec="seconds"),
                str(actor),
                event_type,
                entity_type,
                None if entity_id is None else str(entity_id),
                run_id,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        if own:
            conn.commit()
        return cur.lastrowid
    finally:
        if own:
            conn.close()
