"""services/ui_api.py - "Ổ chia" giữa giao diện và backend (Yến, contracts Mục 11).

app.py CHỈ import file này. File này:
  1. chọn nguồn: hàm giả (services/stubs.py) hoặc hàm thật (repository, po_service...);
  2. đổi dữ liệu thô của DB sang dạng dễ hiển thị: giải mã chuỗi JSON (`causes`,
     `options_json`), lấy đánh đổi A/B từ `options[].tradeoff`, ghép tên nguyên liệu,
     đơn vị, mức tối thiểu và tên NCC.
Phần (2) dùng chung cho cả hàm giả lẫn hàm thật, vì stubs.py trả dữ liệu cùng dạng với DB.

Không chứa luật nghiệp vụ: kiểm tra quyền, MOQ, trạng thái PO... đều do hàm ghi tự làm.

CÁCH ĐỔI SANG HÀM THẬT (việc Y03, Y09): đổi DUNG_HAM_GIA = False ở dưới.
Hàm thật nào chưa có (file còn rỗng) thì giao diện báo "chưa có" thay vì sập.
"""
from __future__ import annotations

import json

# ===========================================================================
# CÔNG TẮC NGUỒN DỮ LIỆU
#   True  = dùng hàm giả trong services/stubs.py (dữ liệu trong bộ nhớ)
#   False = dùng hàm thật và database data/bakery.db (chạy data/init_db.py trước)
# ===========================================================================
DUNG_HAM_GIA = True

if DUNG_HAM_GIA:
    from services import stubs as _repo
    from services import stubs as _fefo
    from services import stubs as _po
    from services import stubs as _clock
    from services import stubs as _inventory
    from services import stubs as _audit
    from services import stubs as _chat
else:
    import importlib

    from data import repository as _repo
    from services import clock as _clock
    from services import fefo as _fefo
    from services import po_service as _po

    def _module(name):
        """Import muộn: module chưa xong hoặc thiếu thư viện thì trả None, không làm sập app."""
        try:
            return importlib.import_module(name)
        except Exception:  # noqa: BLE001
            return None

    _inventory = _module("services.inventory_service")
    _audit = _module("services.audit_service")
    _chat = _module("services.chat_service")

GuardrailError = _po.GuardrailError


def _missing(module, func_name):
    """Lấy hàm từ module; chưa có thì trả None."""
    return getattr(module, func_name, None) if module is not None else None


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


def fefo_projection(ingredient_id, days: int, at=None) -> dict:
    return _fefo.fefo_projection(ingredient_id, days, at or now())


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
    return _repo.get_audit_logs(event_type=event_type, limit=limit)


# ---------------------------------------------------------------------------
# Thao tác ghi (chuyển thẳng sang hàm thật; hàm ghi tự kiểm tra guardrail)
# ---------------------------------------------------------------------------
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]:
    func = _missing(_inventory, "issue_stock_fefo")
    if func is None:
        raise GuardrailError("NOT_READY", "Chức năng xuất kho (inventory_service, P04) chưa có.")
    return func(ingredient_id, qty, user)


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
    if not DUNG_HAM_GIA and _missing(_inventory, "receive_batch") is None:
        raise GuardrailError("NOT_READY", "Chức năng nhận hàng cần inventory_service (P04), hiện chưa có.")
    return _po.mark_received(po_id, qty, user, expiry_date)


def dismiss_alert(alert_id: int, user) -> None:
    return _po.dismiss_alert(alert_id, user)


def run_morning_audit(trigger: str, rerun: bool = False, user=None) -> dict:
    func = _missing(_audit, "run_morning_audit")
    if func is None:
        return {"run_id": None, "status": "FAILED", "alert_ids": [],
                "message": "Chức năng rà soát (audit_service, M05) chưa có."}
    return func(trigger, rerun=rerun, user=user)


def ask_chat(question: str, user) -> dict:
    func = _missing(_chat, "ask_chat")
    if func is None:
        return {"answer": "Chức năng hỏi đáp (chat_service) chưa chạy được.", "refused": True}
    return func(question, user)


# ---------------------------------------------------------------------------
# Chỉ dùng khi phát triển (nút Reset ở thanh bên). Hàm thật không có.
# ---------------------------------------------------------------------------
def reset(scenario: str = "with_alerts") -> None:
    if DUNG_HAM_GIA:
        _repo.reset(scenario)
