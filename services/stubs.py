"""services/stubs.py - HÀM GIẢ cho giao diện (Yến, việc Y01/Y02).

Mục đích: để giao diện chạy được khi hàm thật chưa xong. Chỉ services/ui_api.py
được import file này (contracts Mục 11); app.py KHÔNG import trực tiếp.

Nguyên tắc:
- Tên hàm và DẠNG dữ liệu trả về GIỐNG hàm thật (repository, fefo, po_service,
  inventory_service, audit_service, chat_service). Vì vậy dữ liệu "thô" giống DB:
  `causes`, `options_json` là chuỗi JSON, PO không có `sup_name`... Phần đổi sang
  dạng dễ hiển thị nằm ở ui_api.py và dùng chung cho cả hàm giả lẫn hàm thật.
- Dữ liệu mẫu LẤY THẲNG từ data/init_db.py (nguyên liệu, công thức, đơn đặt, NCC,
  lô, PO) nên không bao giờ lệch dữ liệu thật. 4 cảnh báo là kết quả của
  services/calc.py chạy trên dữ liệu chuẩn lúc DEMO_DATE=2026-11-20T08:00.
- Trạng thái giữ trong bộ nhớ (biến _S). Tắt app là mất; nút Reset ở thanh bên
  đưa về ban đầu.
- Hàm ghi ném GuardrailError(code, message) giống po_service để giao diện tập xử lý lỗi.

File này bị xóa ở việc Y09 (khi mọi hàm thật đã có).
"""
from __future__ import annotations

import copy
import json
import math
import time
from datetime import date, datetime, timedelta

from data.init_db import (INGREDIENTS, PRE_ORDERS, RECIPES, SUPPLIER_ROWS, build_batches,
                          build_purchase_orders)

# "Bây giờ" giả, đúng giờ demo của nhóm (DEMO_DATE=2026-11-20T08:00).
FAKE_NOW = datetime(2026, 11, 20, 8, 0, 0)
H_DAYS = 3


class GuardrailError(Exception):
    """Giống GuardrailError(code, message) của services/po_service.py."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def now() -> datetime:
    return FAKE_NOW


def today() -> date:
    return FAKE_NOW.date()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Dữ liệu cố định lấy từ data/init_db.py
# ---------------------------------------------------------------------------
_INGREDIENTS = [{"id": i[0], "name": i[1], "unit": i[2], "min_stock": float(i[3]),
                 "shelf_life_days": i[4], "daily_usage": float(i[5])} for i in INGREDIENTS]
_ING = {i["id"]: i for i in _INGREDIENTS}

_RECIPES = [{"cake_name": r[0], "ingredient_id": r[1], "qty_per_cake": float(r[2])} for r in RECIPES]

_SUPPLIERS = []
for (_sid, _name, _lead, _email), _ing, _price, _moq, _rel, _override in SUPPLIER_ROWS:
    _SUPPLIERS.append({
        "sup_id": _sid, "name": _name, "ingredient_id": _ing, "price": float(_price),
        "lead_time_hours": _lead, "moq": float(_moq), "reliability": _rel,
        "contact_email": None if _override == "" else _email, "active": 1,
    })


# 4 cảnh báo = kết quả calc.compute_alerts() trên dữ liệu chuẩn (PO-004 đã DELAYED).
# Lưu theo DẠNG DB (contracts Mục 10): causes và options_json là chuỗi JSON,
# đánh đổi nằm trong từng phương án ở khóa "tradeoff".
def _opt(letter, sup_id, sup_name, price, qty, lead_hours, meets, diff, reason, tradeoff):
    arrival = FAKE_NOW + timedelta(hours=lead_hours)
    return {
        "option": letter, "sup_id": sup_id, "sup_name": sup_name, "unit_price": float(price),
        "order_qty": float(qty), "total_cost": float(price * qty), "lead_time_hours": lead_hours,
        "lead_time_days": math.ceil(lead_hours / 24), "arrival_at": _iso(arrival),
        "meets_earliest_preorder": meets, "cost_diff_pct_vs_A": float(diff), "reason": reason,
        "tradeoff": tradeoff,
    }


_ALERT_DRAFTS = [
    {
        "ingredient_id": "BOT_MI", "projected_stock": 0.0, "shortage_qty": 10.0,
        "causes": [{"code": "DAILY_USAGE", "qty": 36.0}, {"code": "PREORDER", "qty": 4.0}],
        "main_cause": "DAILY_USAGE",
        "options": [_opt("A", "NCC_CHINH", "NCC Chính", 20000, 10, 24, True, 0,
                         "Vừa rẻ nhất vừa nhanh nhất trong các NCC hợp lệ",
                         "NCC Chính vừa rẻ nhất vừa nhanh nhất, 200.000đ, hàng về 08:00 ngày mai.")],
        "explanation": "Bột mì thiếu chủ yếu do bán lẻ 12 kg/ngày (36 kg trong 3 ngày), cộng 4 kg cho "
                       "đơn đặt trước, nên tồn dự kiến về 0 kg, thấp hơn mức tối thiểu 10 kg.",
    },
    {
        "ingredient_id": "BO_LAT", "projected_stock": -10.0, "shortage_qty": 13.0,
        "causes": [{"code": "SUPPLIER_DELAY", "qty": 20.0}, {"code": "PREORDER", "qty": 12.0},
                   {"code": "DAILY_USAGE", "qty": 3.0}],
        "main_cause": "SUPPLIER_DELAY",
        "options": [
            _opt("A", "NCC_CHINH", "NCC Chính", 250000, 15, 24, True, 0,
                 "Tổng chi phí thấp nhất trong các NCC hợp lệ",
                 "Phương án A (NCC Chính) rẻ nhất, 3.750.000đ cho 15 kg, hàng về 08:00 ngày mai."),
            _opt("B", "NCC_HOA_TOC", "NCC Hỏa tốc", 287500, 14, 2, True, 7.3,
                 "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay",
                 "Phương án B (NCC Hỏa tốc) đắt hơn 7,3%, 4.025.000đ cho 14 kg, hàng về 10:00 sáng nay."),
        ],
        "explanation": "PO-004 (20 kg từ NCC Chính) đã quá hạn giao nên không được tính vào tồn. Tồn hiện "
                       "5 kg, sáng mai cần 12 kg cho croissant và bán lẻ 1 kg/ngày, nên dự kiến thiếu 10 kg.",
    },
    {
        "ingredient_id": "KEM_TUOI", "projected_stock": -2.0, "shortage_qty": 4.0,
        "causes": [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0},
                   {"code": "DAILY_USAGE", "qty": 3.0}],
        "main_cause": "EXPIRING_LOT",
        "options": [
            _opt("A", "NCC_CHINH", "NCC Chính", 90000, 4, 24, True, 0,
                 "Tổng chi phí thấp nhất trong các NCC hợp lệ",
                 "Phương án A (NCC Chính) rẻ nhất, 360.000đ, nhưng hàng về lúc 08:00 ngày mai."),
            _opt("B", "NCC_HOA_TOC", "NCC Hỏa tốc", 103500, 4, 2, True, 15.0,
                 "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay",
                 "Phương án B (NCC Hỏa tốc) đắt hơn 15%, 414.000đ, nhưng hàng về lúc 10:00 sáng nay."),
        ],
        "explanation": "Kem tươi dự kiến thiếu vì 6 lít thuộc lô KEM-L1 sẽ hết hạn lúc 20:00 tối nay "
                       "trước khi kịp dùng. Ngoài ra còn 3 lít cho đơn đặt trước ngày mai và 3 lít bán lẻ "
                       "trong 3 ngày tới.",
    },
    {
        "ingredient_id": "MASCARPONE", "projected_stock": -0.6, "shortage_qty": 1.1,
        "causes": [{"code": "PREORDER", "qty": 1.0}, {"code": "DAILY_USAGE", "qty": 0.6}],
        "main_cause": "PREORDER",
        "options": [_opt("A", "NCC_CHINH", "NCC Chính", 300000, 2, 24, True, 0,
                         "Chỉ có 1 NCC hợp lệ",
                         "Chỉ có NCC Chính bán Mascarpone nên không có phương án để so sánh.")],
        "explanation": "Mascarpone thiếu do 1 kg cho đơn Tiramisu và 0,6 kg bán lẻ trong 3 ngày; tồn dự "
                       "kiến −0,6 kg, thấp hơn mức tối thiểu 0,5 kg.",
    },
]


def _fresh_state(scenario: str) -> dict:
    today_ = today()
    batches = [{"batch_id": b[0], "ingredient_id": b[1], "qty": float(b[2]), "expiry_date": b[3],
                "received_at": b[4], "po_id": b[5]} for b in build_batches(today_, "standard")]
    cols, po_rows = build_purchase_orders(today_, "standard")
    pos = []
    for row in po_rows:
        po = {c: None for c in (
            "po_id", "alert_id", "sup_id", "ingredient_id", "order_qty", "unit_price", "total_cost",
            "status", "delivery_status", "expected_delivery_date", "received_qty", "created_by",
            "created_at", "approved_by", "approved_at", "sent_by", "sent_at", "send_attempts",
            "last_send_error", "closed_reason", "closed_by", "closed_at")}
        po.update(dict(zip(cols, row)))
        po.update({"order_qty": float(po["order_qty"]), "received_qty": float(po["received_qty"]),
                   "send_attempts": 1})
        pos.append(po)
    preorders = [{"order_id": o[0], "cake_name": o[1], "quantity": o[2],
                  "delivery_date": (today_ + timedelta(days=o[3])).isoformat()} for o in PRE_ORDERS]
    state = {"batches": batches, "pos": pos, "preorders": preorders,
             "alerts": [], "runs": [], "logs": []}
    if scenario == "with_alerts":
        _run_audit_into(state, "SCHEDULED", rerun=False, actor="scheduler")
    return state


def _log(state, actor, event_type, entity_type=None, entity_id=None, run_id=None, detail=None):
    state["logs"].append({
        "log_id": len(state["logs"]) + 1, "created_at": _iso(FAKE_NOW), "actor": actor,
        "event_type": event_type, "entity_type": entity_type,
        "entity_id": None if entity_id is None else str(entity_id), "run_id": run_id,
        "detail_json": json.dumps(detail, ensure_ascii=False) if detail else None,
    })


def _run_audit_into(state, trigger, rerun, actor):
    """Giả lập audit_service: đánh dấu PO trễ, tạo phiên + 4 cảnh báo (nếu chưa có)."""
    run_id = len(state["runs"]) + 1
    started = FAKE_NOW + timedelta(minutes=5 * (run_id - 1))
    _log(state, actor, "RUN_STARTED", "audit_run", run_id, run_id)
    for po in state["pos"]:
        if (po["status"] == "SENT" and po["delivery_status"] == "PENDING"
                and po["expected_delivery_date"] < today().isoformat()
                and po["received_qty"] < po["order_qty"]):
            po["delivery_status"] = "DELAYED"
            _log(state, "system", "DELIVERY_DELAYED", "purchase_order", po["po_id"], run_id)
    new_ids = []
    if not state["alerts"]:
        for draft in _ALERT_DRAFTS:
            alert_id = len(state["alerts"]) + 1
            state["alerts"].append({
                "alert_id": alert_id, "run_id": run_id, "ingredient_id": draft["ingredient_id"],
                "event_type": "SHORTAGE", "status": "OPEN", "main_cause": draft["main_cause"],
                "causes": json.dumps(draft["causes"], ensure_ascii=False),
                "projected_stock": draft["projected_stock"], "shortage_qty": draft["shortage_qty"],
                "options_json": json.dumps(draft["options"], ensure_ascii=False),
                "explanation": draft["explanation"], "snapshot_hash": f"stub-{draft['ingredient_id']}",
                "selected_option": None, "selected_qty": None, "selected_by": None,
                "selected_at": None, "po_id": None, "created_at": _iso(started), "resolved_at": None,
            })
            new_ids.append(alert_id)
            _log(state, "system", "ALERT_CREATED", "alert", alert_id, run_id)
    state["runs"].append({
        "run_id": run_id, "run_date": today().isoformat(), "trigger": trigger,
        "is_rerun": 1 if rerun else 0, "status": "SUCCEEDED", "started_at": _iso(started),
        "finished_at": _iso(started + timedelta(seconds=7)),
        "alert_count": len(state["alerts"]), "error": None,
    })
    _log(state, actor, "RUN_SUCCEEDED", "audit_run", run_id, run_id)
    return run_id, new_ids


_S: dict = _fresh_state("with_alerts")


def reset(scenario: str = "with_alerts") -> None:
    """Đưa dữ liệu giả về ban đầu. scenario: 'with_alerts' (đã rà soát 08:00, có 4 cảnh báo)
    hoặc 'no_run' (hôm nay chưa rà soát, PO-004 còn PENDING)."""
    _S.clear()
    _S.update(_fresh_state(scenario))


# ---------------------------------------------------------------------------
# Tiện ích nội bộ
# ---------------------------------------------------------------------------
def _actor(user) -> str:
    return (user or {}).get("username") or "unknown"


def _require_manager(user, action) -> None:
    if not user or user.get("role") != "MANAGER":
        _log(_S, _actor(user), "GUARDRAIL_BLOCKED", detail={"action": action, "code": "NOT_MANAGER"})
        raise GuardrailError("NOT_MANAGER", "Chỉ quản lý (MANAGER) mới được thực hiện thao tác này.")


def _block(user, action, code, message):
    _log(_S, _actor(user), "GUARDRAIL_BLOCKED", detail={"action": action, "code": code})
    raise GuardrailError(code, message)


def _find_po(po_id: str) -> dict | None:
    return next((p for p in _S["pos"] if p["po_id"] == po_id), None)


def _find_alert(alert_id) -> dict | None:
    return next((a for a in _S["alerts"] if a["alert_id"] == alert_id), None)


def _resolve_alert_of(po: dict) -> None:
    alert = _find_alert(po.get("alert_id"))
    if alert is not None:
        alert["status"] = "RESOLVED"
        alert["resolved_at"] = _iso(FAKE_NOW)


def _usable_lots(ingredient_id, at):
    lots = [b for b in _S["batches"] if b["ingredient_id"] == ingredient_id and b["qty"] > 0
            and datetime.fromisoformat(b["expiry_date"]) > at]
    lots.sort(key=lambda b: (b["expiry_date"], b["received_at"]))
    return lots


# ---------------------------------------------------------------------------
# Giống data/repository.py (Phương Anh, P03)
# ---------------------------------------------------------------------------
def get_ingredients() -> list[dict]:
    return copy.deepcopy(_INGREDIENTS)


def get_ingredient(ingredient_id) -> dict | None:
    return copy.deepcopy(_ING.get(ingredient_id))


def get_batches_fefo(ingredient_id, at) -> list[dict]:
    """Chỉ lô còn hạn tại `at`, qty > 0, sắp theo expiry_date rồi received_at."""
    return copy.deepcopy(_usable_lots(ingredient_id, at))


def get_suppliers(ingredient_id=None, active_only=False) -> list[dict]:
    return [copy.deepcopy(s) for s in _SUPPLIERS
            if ingredient_id is None or s["ingredient_id"] == ingredient_id]


def get_supplier(sup_id) -> dict | None:
    return copy.deepcopy(next((s for s in _SUPPLIERS if s["sup_id"] == sup_id), None))


def get_pre_orders(from_date=None, to_date=None) -> list[dict]:
    rows = [p for p in _S["preorders"]
            if (from_date is None or p["delivery_date"] >= str(from_date))
            and (to_date is None or p["delivery_date"] <= str(to_date))]
    return copy.deepcopy(sorted(rows, key=lambda p: (p["delivery_date"], p["order_id"])))


def get_purchase_orders(status=None, ingredient_id=None, open_only=False) -> list[dict]:
    rows = [p for p in _S["pos"]
            if (status is None or p["status"] == status)
            and (ingredient_id is None or p["ingredient_id"] == ingredient_id)
            and (not open_only or p["closed_reason"] is None)]
    return copy.deepcopy(rows)


def get_open_purchase_orders(ingredient_id=None) -> list[dict]:
    return [p for p in get_purchase_orders(ingredient_id=ingredient_id, open_only=True)
            if p["delivery_status"] != "RECEIVED"]


def get_alerts(run_id=None, status=None, ingredient_id=None) -> list[dict]:
    rows = [a for a in _S["alerts"]
            if (run_id is None or a["run_id"] == run_id)
            and (status is None or a["status"] == status)
            and (ingredient_id is None or a["ingredient_id"] == ingredient_id)]
    return copy.deepcopy(rows)


def get_audit_runs(run_date=None, status=None) -> list[dict]:
    rows = [r for r in _S["runs"]
            if (run_date is None or r["run_date"] == str(run_date)[:10])
            and (status is None or r["status"] == status)]
    return copy.deepcopy(sorted(rows, key=lambda r: r["run_id"], reverse=True))


def get_latest_audit_run(run_date=None) -> dict | None:
    runs = get_audit_runs(run_date=run_date)
    return runs[0] if runs else None


def get_audit_logs(event_type=None, run_id=None, entity_type=None, entity_id=None,
                   limit=None) -> list[dict]:
    rows = [r for r in _S["logs"]
            if (event_type is None or r["event_type"] == event_type)
            and (run_id is None or r["run_id"] == run_id)
            and (entity_type is None or r["entity_type"] == entity_type)
            and (entity_id is None or r["entity_id"] == str(entity_id))]
    rows = sorted(rows, key=lambda r: r["log_id"], reverse=True)
    return copy.deepcopy(rows[:limit] if limit else rows)


# ---------------------------------------------------------------------------
# Giống services/fefo.py (Quỳnh Anh, Q01) - mô phỏng theo ngày, Mục 5.2
# ---------------------------------------------------------------------------
def fefo_projection(ingredient_id, days: int, at: datetime | None = None) -> dict:
    at = at or FAKE_NOW
    ing = _ING[ingredient_id]
    lots = [dict(b) for b in _usable_lots(ingredient_id, at)]
    per_cake = {r["cake_name"]: r["qty_per_cake"] for r in _RECIPES if r["ingredient_id"] == ingredient_id}
    remaining_expired, unmet = 0.0, 0.0
    plan, expired = [], []
    for t in range(days):
        day = at.date() + timedelta(days=t)
        for po in _S["pos"]:  # hàng của PO SENT, PENDING, dự kiến về ngày t
            if (po["ingredient_id"] == ingredient_id and po["status"] == "SENT"
                    and po["delivery_status"] == "PENDING" and po["expected_delivery_date"] == day.isoformat()):
                lots.append({"batch_id": f"{po['po_id']}-sap-ve", "qty": po["order_qty"] - po["received_qty"],
                             "expiry_date": _iso(datetime.combine(day, datetime.min.time())
                                                 + timedelta(days=ing["shelf_life_days"])),
                             "received_at": day.isoformat()})
        lots.sort(key=lambda b: (b["expiry_date"], b["received_at"]))
        need = ing["daily_usage"] + sum(p["quantity"] * per_cake.get(p["cake_name"], 0)
                                        for p in _S["preorders"] if p["delivery_date"] == day.isoformat())
        for lot in lots:
            if need <= 1e-9:
                break
            if lot["qty"] <= 0 or datetime.fromisoformat(lot["expiry_date"]).date() < day:
                continue
            take = min(lot["qty"], need)
            lot["qty"] -= take
            need -= take
            plan.append({"day": day.isoformat(), "batch_id": lot["batch_id"], "take_qty": round(take, 6)})
        unmet += need
        for lot in lots:  # cuối ngày: phần còn lại của lô hết hạn trong ngày t bị bỏ
            if lot["qty"] > 0 and datetime.fromisoformat(lot["expiry_date"]).date() == day:
                expired.append({"day": day.isoformat(), "batch_id": lot["batch_id"], "qty": round(lot["qty"], 6)})
                remaining_expired += lot["qty"]
                lot["qty"] = 0.0
    remaining = sum(lot["qty"] for lot in lots)
    return {"remaining_qty": round(remaining, 6), "expired_qty": round(remaining_expired, 6),
            "unmet_qty": round(unmet, 6), "projected_stock": round(remaining - unmet, 6),
            "plan": plan, "expired": expired}


# ---------------------------------------------------------------------------
# Giống services/inventory_service.py (Phương Anh, P04)
# ---------------------------------------------------------------------------
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]:
    if ingredient_id not in _ING:
        _block(user, "issue_stock_fefo", "INGREDIENT_NOT_FOUND", "Không tìm thấy nguyên liệu.")
    if qty <= 0:
        _block(user, "issue_stock_fefo", "QTY_INVALID", "Số lượng xuất phải lớn hơn 0.")
    lots = _usable_lots(ingredient_id, FAKE_NOW)
    available = sum(b["qty"] for b in lots)
    if qty > available + 1e-9:
        _block(user, "issue_stock_fefo", "QTY_INVALID",
               f"Xuất quá tồn còn hạn: yêu cầu {qty:g}, chỉ còn {available:g}.")
    taken, remaining = [], qty
    for lot in lots:
        if remaining <= 1e-9:
            break
        take = min(lot["qty"], remaining)
        lot["qty"] = round(lot["qty"] - take, 6)
        remaining = round(remaining - take, 6)
        taken.append({"batch_id": lot["batch_id"], "taken": take})
    _log(_S, _actor(user), "STOCK_ISSUED", "ingredient", ingredient_id, detail={"qty": qty, "taken": taken})
    return taken


# ---------------------------------------------------------------------------
# Giống services/po_service.py (Duyên, D05)
# ---------------------------------------------------------------------------
def create_draft_po(alert_id: int, option: str, qty: float, user) -> str:
    _require_manager(user, "create_draft_po")
    if qty <= 0:
        _block(user, "create_draft_po", "QTY_INVALID", "Số lượng đặt phải lớn hơn 0.")
    alert = _find_alert(alert_id)
    if alert is None:
        _block(user, "create_draft_po", "ALERT_NOT_OPEN", f"Không tìm thấy cảnh báo #{alert_id}.")
    if alert["status"] != "OPEN":
        if alert.get("po_id"):
            _block(user, "create_draft_po", "DUPLICATE_PO",
                   f"Cảnh báo #{alert_id} đã có PO {alert['po_id']}, không tạo thêm.")
        _block(user, "create_draft_po", "ALERT_NOT_OPEN",
               f"Cảnh báo #{alert_id} không còn chờ xử lý (đang {alert['status']}).")
    for p in _S["pos"]:
        if (p["ingredient_id"] == alert["ingredient_id"] and p["closed_reason"] is None
                and p["status"] in ("DRAFT", "APPROVED")):
            _block(user, "create_draft_po", "DUPLICATE_PO",
                   f"Nguyên liệu này đang có PO {p['po_id']} chưa xử lý xong.")
    opt = next((o for o in json.loads(alert["options_json"]) if o["option"] == option), None)
    if opt is None:
        _block(user, "create_draft_po", "SUPPLIER_INVALID", f"Phương án {option!r} không có trong cảnh báo này.")
    sup = next(s for s in _SUPPLIERS if s["sup_id"] == opt["sup_id"] and s["ingredient_id"] == alert["ingredient_id"])
    moq = sup["moq"]
    if qty + 1e-6 < moq or abs(qty / moq - round(qty / moq)) > 1e-6:
        _block(user, "create_draft_po", "MOQ_VIOLATION",
               f"Số lượng {qty:g} phải ≥ MOQ {moq:g} và là bội số của {moq:g}.")
    po_id = f"PO-{len(_S['pos']) + 1:03d}"
    po = {c: None for c in _S["pos"][0]}
    po.update({"po_id": po_id, "alert_id": alert_id, "sup_id": opt["sup_id"],
               "ingredient_id": alert["ingredient_id"], "order_qty": float(qty),
               "unit_price": sup["price"], "total_cost": round(qty * sup["price"], 2),
               "status": "DRAFT", "received_qty": 0.0, "created_by": _actor(user),
               "created_at": _iso(FAKE_NOW), "send_attempts": 0})
    _S["pos"].append(po)
    alert.update({"status": "SELECTED", "selected_option": option, "selected_qty": float(qty),
                  "selected_by": _actor(user), "selected_at": _iso(FAKE_NOW), "po_id": po_id})
    _log(_S, _actor(user), "OPTION_SELECTED", "alert", alert_id, alert["run_id"])
    _log(_S, _actor(user), "PO_CREATED", "purchase_order", po_id, alert["run_id"])
    return po_id


def _open_po(po_id, user, action, status, verb):
    po = _find_po(po_id)
    if po is None:
        _block(user, action, "INVALID_TRANSITION", f"Không tìm thấy PO {po_id}.")
    if po["closed_reason"] is not None or po["status"] != status:
        _block(user, action, "INVALID_TRANSITION", f"PO {po_id} không ở trạng thái {status} để {verb}.")
    return po


def approve_po(po_id: str, user) -> None:
    _require_manager(user, "approve_po")
    po = _open_po(po_id, user, "approve_po", "DRAFT", "duyệt")
    po.update({"status": "APPROVED", "approved_by": _actor(user), "approved_at": _iso(FAKE_NOW)})
    _log(_S, _actor(user), "PO_APPROVED", "purchase_order", po_id)


def reject_po(po_id: str, user, reason: str = "") -> None:
    _require_manager(user, "reject_po")
    po = _open_po(po_id, user, "reject_po", "DRAFT", "từ chối")
    po.update({"closed_reason": "REJECTED", "closed_by": _actor(user), "closed_at": _iso(FAKE_NOW)})
    _resolve_alert_of(po)
    _log(_S, _actor(user), "PO_REJECTED", "purchase_order", po_id, detail={"reason": reason})


def cancel_po(po_id: str, user) -> None:
    _require_manager(user, "cancel_po")
    po = _open_po(po_id, user, "cancel_po", "APPROVED", "hủy")
    po.update({"closed_reason": "CANCELLED", "closed_by": _actor(user), "closed_at": _iso(FAKE_NOW)})
    _resolve_alert_of(po)
    _log(_S, _actor(user), "PO_CANCELLED", "purchase_order", po_id)


def send_po(po_id: str, user) -> str:
    """Giống hàm thật: NCC thiếu contact_email thì gửi thất bại, PO giữ APPROVED, gửi lại được."""
    _require_manager(user, "send_po")
    po = _open_po(po_id, user, "send_po", "APPROVED", "gửi")
    sup = next(s for s in _SUPPLIERS if s["sup_id"] == po["sup_id"] and s["ingredient_id"] == po["ingredient_id"])
    po["send_attempts"] = (po["send_attempts"] or 0) + 1
    if not sup["contact_email"]:
        po["last_send_error"] = f"{sup['name']} chưa có contact_email cho nguyên liệu này."
        _log(_S, _actor(user), "PO_SEND_FAILED", "purchase_order", po_id)
        raise GuardrailError("SEND_FAILED", po["last_send_error"])
    expected = FAKE_NOW + timedelta(hours=sup["lead_time_hours"])
    po.update({"status": "SENT", "delivery_status": "PENDING", "sent_by": _actor(user),
               "sent_at": _iso(FAKE_NOW), "last_send_error": None,
               "expected_delivery_date": expected.date().isoformat()})
    _resolve_alert_of(po)
    _log(_S, _actor(user), "PO_SENT", "purchase_order", po_id)
    return f"outbox/{po_id}.csv"  # hàm giả không tạo file thật


def mark_received(po_id: str, qty: float, user, expiry_date: datetime | None = None) -> str:
    _require_manager(user, "mark_received")
    po = _find_po(po_id)
    if po is None or po["status"] != "SENT" or po["delivery_status"] not in ("PENDING", "DELAYED"):
        _block(user, "mark_received", "INVALID_TRANSITION", f"PO {po_id} không ở trạng thái chờ giao.")
    remaining = po["order_qty"] - po["received_qty"]
    if abs(qty - remaining) > 1e-6:
        _block(user, "mark_received", "QTY_INVALID",
               f"MVP chỉ nhận một lần đủ số lượng: cần nhận đúng {remaining:g}.")
    ing = _ING[po["ingredient_id"]]
    existing = [b for b in _S["batches"] if b["ingredient_id"] == po["ingredient_id"]]
    prefix = existing[0]["batch_id"].rsplit("-L", 1)[0] if existing else po["ingredient_id"]
    n = len(existing) + 1
    while any(b["batch_id"] == f"{prefix}-L{n}" for b in _S["batches"]):
        n += 1
    batch_id = f"{prefix}-L{n}"
    expiry = expiry_date or (FAKE_NOW + timedelta(days=ing["shelf_life_days"]))
    _S["batches"].append({"batch_id": batch_id, "ingredient_id": po["ingredient_id"], "qty": float(qty),
                          "expiry_date": _iso(expiry), "received_at": _iso(FAKE_NOW), "po_id": po_id})
    po.update({"delivery_status": "RECEIVED", "received_qty": po["received_qty"] + float(qty)})
    _log(_S, _actor(user), "BATCH_RECEIVED", "batch", batch_id)
    _log(_S, _actor(user), "DELIVERY_RECEIVED", "purchase_order", po_id)
    return batch_id


def dismiss_alert(alert_id: int, user) -> None:
    _require_manager(user, "dismiss_alert")
    alert = _find_alert(alert_id)
    if alert is None or alert["status"] != "OPEN":
        _block(user, "dismiss_alert", "ALERT_NOT_OPEN", f"Cảnh báo #{alert_id} không còn chờ xử lý.")
    alert["status"] = "DISMISSED"
    alert["resolved_at"] = _iso(FAKE_NOW)
    _log(_S, _actor(user), "ALERT_DISMISSED", "alert", alert_id, alert["run_id"])


# ---------------------------------------------------------------------------
# Giống services/audit_service.py và chat_service.py (Mai Anh, M04, M05)
# ---------------------------------------------------------------------------
def run_morning_audit(trigger: str, rerun: bool = False, user=None) -> dict:
    latest = get_latest_audit_run(today())
    if latest is not None and latest["status"] == "SUCCEEDED" and not rerun:
        return {"run_id": latest["run_id"], "status": "ALREADY_DONE", "alert_ids": [],
                "message": "Hôm nay đã rà soát rồi. Tick ô 'Chạy lại' nếu muốn chạy lần nữa."}
    time.sleep(1.0)  # giả lập thời gian chạy
    actor = "scheduler" if trigger == "SCHEDULED" else _actor(user)
    run_id, new_ids = _run_audit_into(_S, trigger, rerun, actor)
    return {"run_id": run_id, "status": "SUCCEEDED", "alert_ids": new_ids,
            "message": f"Rà soát xong: {len(_S['alerts'])} cảnh báo."}


def ask_chat(question: str, user) -> dict:
    text = (question or "").strip()
    if len(text) > 500:
        return {"answer": "Câu hỏi quá dài (tối đa 500 ký tự).", "refused": True}
    if any(w in text.lower() for w in ("tạo", "đặt", "duyệt", "gửi", "sửa")):
        _log(_S, _actor(user), "CHAT_REFUSED", detail={"question": text})
        return {"answer": "Khung chat chỉ để xem số liệu, không tạo hay sửa PO được. "
                          "Bạn hãy sang tab «Rà soát 08:00» để xử lý nhé.", "refused": True}
    _log(_S, _actor(user), "CHAT_ANSWERED", detail={"question": text})
    return {"answer": "(Hàm giả) Kem tươi còn 10 lít: KEM-L1 7 lít hết hạn 20:00 hôm nay, "
                      "KEM-L2 3 lít hết hạn sau 5 ngày.", "refused": False}
