"""services/ui_api.py - "Ổ chia" giữa giao diện và backend (Yến, contracts Mục 11).

app.py CHỈ import file này. File này:
  1. gọi hàm thật ở repository, clock, fefo, po_service, inventory_service,
     audit_service, chat_service;
  2. đổi dữ liệu thô của DB sang dạng dễ hiển thị: giải mã chuỗi JSON (`causes`,
     `options_json`), lấy đánh đổi A/B từ `options[].tradeoff`, ghép tên nguyên liệu,
     đơn vị, mức tối thiểu và tên NCC.

Không chứa luật nghiệp vụ riêng: làm tròn MOQ gọi calc.round_up_moq; kiểm tra quyền,
MOQ, trạng thái PO, chống chạy trùng... đều do hàm ghi tự làm.
"""
from __future__ import annotations

import inspect
import json

from agent import tools as _tools
from data import repository as _repo
from services import audit_service as _audit
from services import auth as _auth
from services import calc as _calc
from services import chat_service as _chat
from services import clock as _clock
from services import fefo as _fefo
from services import inventory_service as _inventory
from services import po_service as _po
from services.audit_log import EVENT_TYPES

GuardrailError = _po.GuardrailError


# ---------------------------------------------------------------------------
# Giờ hệ thống (services/clock.py)
# ---------------------------------------------------------------------------
def now():
    return _clock.now()


def today():
    return _clock.today()


# ---------------------------------------------------------------------------
# Đọc dữ liệu
# ---------------------------------------------------------------------------
def get_ingredients() -> list[dict]:
    return _repo.get_ingredients()


def get_batches_fefo(ingredient_id, at=None) -> list[dict]:
    return _repo.get_batches_fefo(ingredient_id, at or now())


def get_suppliers(ingredient_id) -> list[dict]:
    return _repo.get_suppliers(ingredient_id)


def get_moq(ingredient_id, sup_id) -> float | None:
    """MOQ của một NCC cho một nguyên liệu (repository.get_suppliers).
    Không tìm thấy NCC hoặc MOQ không hợp lệ thì trả None (KHÔNG tự đoán MOQ = 1):
    giao diện báo lỗi và khóa nút Tạo PO, tránh làm tròn và tính tổng tiền sai."""
    for s in _repo.get_suppliers(ingredient_id):
        if s["sup_id"] == sup_id and s.get("moq") and float(s["moq"]) > 0:
            return float(s["moq"])
    return None


def round_up_moq(qty: float, moq: float | None) -> float:
    """Làm tròn LÊN bội số MOQ, dùng đúng hàm của calc (vd 23 với MOQ 10 -> 30).
    Không có MOQ thì giữ nguyên số lượng. Hàm ghi create_draft_po vẫn kiểm tra lại MOQ."""
    if not moq or moq <= 0:
        return float(qty)
    return _calc.round_up_moq(qty, moq)


# Giờ chốt "kịp đơn đặt trước" lấy từ calc (QA1) để chú thích A/B không ghi lệch code.
PREORDER_CUTOFF = _calc.PREORDER_CUTOFF


# Ngưỡng cảnh báo mềm: đặt ít hơn lượng cần bổ sung, hoặc gấp hơn 3 lần.
OVER_ORDER_FACTOR = 3


def order_qty_warning(qty: float, shortage_qty: float, unit: str = "") -> str | None:
    """Câu cảnh báo vàng khi số lượng đặt bất thường; None nếu bình thường.
    Chỉ nhắc nhở, KHÔNG chặn: quản lý vẫn được tạo PO."""
    if qty <= 0 or not shortage_qty or shortage_qty <= 0:
        return None
    unit = f" {unit}" if unit else ""
    if qty < shortage_qty:
        return (f"Số lượng đặt ({qty:g}{unit}) ít hơn lượng cần bổ sung ({shortage_qty:g}{unit}), "
                "có thể vẫn thiếu hàng.")
    if qty > OVER_ORDER_FACTOR * shortage_qty:
        return (f"Số lượng đặt ({qty:g}{unit}) gấp hơn {OVER_ORDER_FACTOR} lần lượng cần bổ sung "
                f"({shortage_qty:g}{unit}), dễ tồn kho và hết hạn.")
    return None


def fefo_projection(ingredient_id, days: int, at=None) -> dict:
    return _fefo.fefo_projection(ingredient_id, days, at or now())


def stock_overview(days: int = 3) -> list[dict]:
    """Bảng "Tồn kho & dự báo": tách tồn dự kiến thành từng phần của công thức.

    Mọi con số lấy từ hàm có sẵn, giao diện KHÔNG tự tính:
      on_hand    – lô còn hạn (repository.get_batches_fefo)
      preorder   – đơn đặt trước trong `days` ngày   (tools.calc_preorder_demand)
      daily / retail – bán lẻ mỗi ngày / cả kỳ       (tools.calc_preorder_demand)
      incoming   – hàng PO đã gửi sắp về trong kỳ    (tools.check_delayed_po → by_ingredient)
      expiring   – sẽ hết hạn trước khi kịp dùng     (fefo.fefo_projection)
      projected  – tồn dự kiến sau `days` ngày       (fefo.fefo_projection)
    `consistent` = các phần cộng trừ ra đúng `projected` (chỉ để giao diện cảnh báo nếu lệch).
    """
    at = now()
    demand = {r["ingredient_id"]: r for r in _tools.calc_preorder_demand(days=days).get("items", [])}
    incoming = {r["ingredient_id"]: float(r.get("incoming_stock") or 0)
                for r in _tools.check_delayed_po(days=days).get("by_ingredient", [])}
    rows = []
    for ing in get_ingredients():
        iid = ing["id"]
        lots = _repo.get_batches_fefo(iid, at)
        proj = _fefo.fefo_projection(iid, days, at)
        need = demand.get(iid, {})
        row = {
            "ingredient_id": iid, "name": ing["name"], "unit": ing["unit"],
            "on_hand": round(sum(float(b["qty"]) for b in lots), 6),
            "preorder": float(need.get("preorder_demand") or 0),
            "daily": float(need.get("daily_usage") or ing.get("daily_usage") or 0),
            "retail": float(need.get("retail_demand") or 0),
            "incoming": incoming.get(iid, 0.0),
            "expiring": float(proj["expired_qty"]),
            "projected": float(proj["projected_stock"]),
            "min_stock": float(ing["min_stock"]),
            "expired_lots": proj.get("expired", []),
        }
        total = row["on_hand"] - row["preorder"] - row["retail"] + row["incoming"] - row["expiring"]
        row["consistent"] = abs(total - row["projected"]) < 1e-6
        row["short"] = row["projected"] < row["min_stock"]
        rows.append(row)
    return rows


def list_preorders(from_date=None, to_date=None) -> list[dict]:
    return _repo.get_pre_orders(from_date, to_date)


def _sup_names() -> dict:
    return {s["sup_id"]: s["name"] for s in _repo.get_suppliers()}


def list_pos(status=None, ingredient_id=None) -> list[dict]:
    """Mọi PO, ghép thêm `sup_name` (DB chỉ có sup_id)."""
    names = _sup_names()
    pos = _repo.get_purchase_orders(status=status, ingredient_id=ingredient_id)
    for p in pos:
        p["sup_name"] = names.get(p["sup_id"], p["sup_id"])
    return pos


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _alert_for_display(row: dict, ing_by_id: dict) -> dict:
    alert = dict(row)
    ing = ing_by_id.get(alert["ingredient_id"], {})
    alert["ingredient_name"] = ing.get("name", alert["ingredient_id"])
    alert["unit"] = ing.get("unit", "")
    alert["min_stock"] = ing.get("min_stock")

    # causes: chuẩn là [{"code", "qty"}]; dạng cũ chỉ có mã ["PREORDER", ...] vẫn đọc được
    causes = []
    for c in _json_list(alert.get("causes")):
        causes.append(c if isinstance(c, dict) else {"code": str(c), "qty": None})
    alert["causes"] = causes

    # options: lấy từ options_json; đánh đổi nằm trong từng phương án (khóa "tradeoff")
    options = _json_list(alert.get("options_json"))
    alert["options"] = options
    alert["ai_fallback"] = bool(alert.get("ai_fallback"))
    by_letter = {o.get("option"): o for o in options}
    alert["tradeoff_a"] = (by_letter.get("A") or {}).get("tradeoff")
    alert["tradeoff_b"] = (by_letter.get("B") or {}).get("tradeoff")

    # open_po_id và note không có cột riêng trong DB nên suy ra lại
    alert["open_po_id"] = None
    if any(c["code"] == "OPEN_PO" for c in causes):
        open_pos = [p for p in _repo.get_purchase_orders(ingredient_id=alert["ingredient_id"])
                    if p["closed_reason"] is None and p["status"] in ("DRAFT", "APPROVED")]
        alert["open_po_id"] = open_pos[0]["po_id"] if open_pos else "đang mở"
    if len(options) == 1:
        alert["note"] = options[0].get("reason")
    elif not options and not alert["open_po_id"]:
        alert["note"] = "Cần xử lý thủ công"
    else:
        alert["note"] = None
    return alert


def list_alerts(run_id=None, status=None) -> list[dict]:
    ing_by_id = {i["id"]: i for i in get_ingredients()}
    return [_alert_for_display(a, ing_by_id) for a in _repo.get_alerts(run_id=run_id, status=status)]


def get_today_run() -> dict | None:
    """Phiên rà soát mới nhất của hôm nay, hoặc None nếu hôm nay chưa rà soát."""
    return _repo.get_latest_audit_run(run_date=today())


def get_audit_runs(run_date=None, status=None) -> list[dict]:
    return _repo.get_audit_runs(run_date=run_date, status=status)


def get_audit_logs(event_type=None, limit=None) -> list[dict]:
    """Nhật ký mới nhất trước. event_type=None là lấy mọi loại."""
    return _repo.get_audit_logs(event_type=event_type, limit=limit)


def list_event_types() -> tuple[str, ...]:
    """Danh sách loại sự kiện (Mục 7) cho ô lọc ở Tab 4."""
    return EVENT_TYPES


# ---------------------------------------------------------------------------
# Thao tác ghi (chuyển thẳng sang hàm thật; hàm ghi tự kiểm tra guardrail)
# ---------------------------------------------------------------------------
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]:
    return _inventory.issue_stock_fefo(ingredient_id, qty, user)


def create_draft_po(alert_id: int, option: str, qty: float, user) -> str:
    return _po.create_draft_po(alert_id, option, qty, user)


def approve_po(po_id: str, user) -> None:
    return _po.approve_po(po_id, user)


def reject_po(po_id: str, user, reason: str = "") -> None:
    return _po.reject_po(po_id, user, reason)


def cancel_po(po_id: str, user) -> None:
    return _po.cancel_po(po_id, user)


def send_po(po_id: str, user) -> str:
    return _po.send_po(po_id, user)


def mark_received(po_id: str, qty: float, user, expiry_date=None) -> str:
    return _po.mark_received(po_id, qty, user, expiry_date)


def dismiss_alert(alert_id: int, user) -> None:
    return _po.dismiss_alert(alert_id, user)


def run_morning_audit(trigger: str, rerun: bool = False, user=None) -> dict:
    return _audit.run_morning_audit(trigger, rerun=rerun, user=user)


def recover_stale_runs() -> list[int]:
    """Gọi khi app khởi động: phiên RUNNING bị bỏ dở quá lâu -> FAILED."""
    return _audit.recover_stale_runs()


# Số lượt hỏi đáp gần nhất gửi kèm cho chat (đặc tả: 3–4 lượt). chat_service tự cắt mỗi câu ≤ 300 ký tự.
CHAT_HISTORY_TURNS = 4


def ask_chat(question: str, user, history=None) -> dict:
    """Hỏi đáp chỉ đọc. Gửi kèm tối đa CHAT_HISTORY_TURNS lượt gần nhất để chat hiểu câu hỏi nối tiếp.

    history: [{"question", "answer", "refused"}, ...] (đúng như st.session_state["chat_history"]).
    Trả {"answer", "refused", "fallback"}; fallback = True khi AI không phản hồi, dùng câu dự phòng.
    Bản chat_service chưa nhận history (trước PR MA2) thì vẫn chạy, chỉ không gửi lịch sử.
    """
    turns = [{"question": h.get("question", ""), "answer": h.get("answer", ""),
              "refused": bool(h.get("refused"))} for h in (history or [])][-CHAT_HISTORY_TURNS:]
    if "history" in inspect.signature(_chat.ask_chat).parameters:
        result = _chat.ask_chat(question, user, history=turns)
    else:
        result = _chat.ask_chat(question, user)
    return {"answer": result.get("answer", ""), "refused": bool(result.get("refused")),
            "fallback": bool(result.get("fallback"))}


# ---------------------------------------------------------------------------
# Đăng nhập (services/auth.py – Duyên, DU2): giao diện không tự so PIN
# ---------------------------------------------------------------------------
def login_manager(name: str, pin: str):
    """Quản lý: tên + PIN riêng; sai 5 lần liên tiếp thì khóa tên đó 5 phút.
    Trả (user | None, câu thông báo tiếng Việt)."""
    result = _auth.login_manager(name, pin)
    return result.user, result.message


def login_staff(name: str):
    """Nhân viên: bắt buộc nhập tên để nhật ký ghi đúng người. Trả (user | None, câu thông báo)."""
    result = _auth.login_staff(name)
    return result.user, result.message
