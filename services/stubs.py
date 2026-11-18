"""services/stubs.py - HÀM GIẢ cho giao diện (việc Y01).

Mục đích: để Yến làm giao diện mà không phải chờ backend. Mọi hàm ở đây có
CHỮ KÝ và JSON trả về giống docs/contracts.md. Dữ liệu là dữ liệu giả
(giả lập "hôm nay" = 2026-11-20 08:00 như DEMO_DATE trong .env).

Quy ước:
- Trạng thái được giữ trong biến _S (trong bộ nhớ). Tắt app là mất, bấm
  "Reset dữ liệu giả" ở thanh bên cũng đưa về ban đầu.
- Hàm giả CŨNG ném GuardrailError(code, message) giống hàm thật để giao diện
  tập xử lý lỗi.
- Các hàm ở nhóm "CHƯA CÓ TRONG CONTRACTS" là hàm giao diện cần nhưng
  contracts.md chưa ghi. Phải hỏi nhóm bổ sung trước khi làm việc Y03.

File này bị xóa ở việc Y09 (khi nối hàm thật).
"""
from __future__ import annotations

import copy
import math
import time
from datetime import date, datetime, timedelta

# "Bây giờ" giả. Hàm thật sẽ dùng services.clock.now().
FAKE_NOW = datetime(2026, 11, 20, 8, 0, 0)


class GuardrailError(Exception):
    """Giống GuardrailError(code, message) của services/po_service.py."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def now() -> datetime:
    return FAKE_NOW


def today() -> date:
    return FAKE_NOW.date()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Dữ liệu cố định
# ---------------------------------------------------------------------------
_INGREDIENTS = [
    # id, name, unit, min_stock, shelf_life_days, daily_usage
    ("BOT_MI", "Bột mì", "kg", 10.0, 120, 12.0),
    ("DUONG", "Đường cát", "kg", 5.0, 365, 3.0),
    ("TRUNG_GA", "Trứng gà", "quả", 50.0, 14, 20.0),
    ("BO_LAT", "Bơ lạt Anchor", "kg", 3.0, 60, 1.0),
    ("KEM_TUOI", "Kem tươi Whipping Cream", "lít", 2.0, 7, 1.0),
    ("MASCARPONE", "Phô mai Mascarpone", "kg", 2.0, 21, 0.5),
    ("SUA_TUOI", "Sữa tươi", "lít", 5.0, 7, 2.0),
    ("SOCOLA_DEN", "Socola đen", "kg", 3.0, 365, 0.5),
    ("BOT_CACAO", "Bột cacao", "kg", 1.0, 365, 0.2),
    ("MEN_NO", "Men nở", "kg", 0.5, 180, 0.1),
    ("VANI", "Tinh chất vani", "ml", 100.0, 365, 20.0),
    ("MUOI", "Muối", "kg", 1.0, 730, 0.1),
]

_SUPPLIERS = {
    "KEM_TUOI": [
        ("NCC_CHINH", "NCC Chính", 90000, 24, 2.0, 0.95, "chinh@ncc.vn"),
        ("NCC_HOA_TOC", "NCC Hỏa tốc", 103500, 2, 2.0, 0.98, "hoatoc@ncc.vn"),
    ],
    "BO_LAT": [
        ("NCC_CHINH", "NCC Chính", 220000, 24, 5.0, 0.95, "chinh@ncc.vn"),
        ("NCC_HOA_TOC", "NCC Hỏa tốc", 253000, 2, 5.0, 0.98, "hoatoc@ncc.vn"),
        ("NCC_ANH_DUONG", "NCC Ánh Dương", 190000, 48, 5.0, 0.90, "anhduong@ncc.vn"),
    ],
    "BOT_MI": [
        ("NCC_CHINH", "NCC Chính", 16000, 24, 10.0, 0.95, "chinh@ncc.vn"),
        ("NCC_HOA_TOC", "NCC Hỏa tốc", 18400, 2, 10.0, 0.98, "hoatoc@ncc.vn"),
        ("NCC_ANH_DUONG", "NCC Ánh Dương", 14000, 48, 10.0, 0.90, "anhduong@ncc.vn"),
    ],
    "MASCARPONE": [
        ("NCC_CHINH", "NCC Chính", 280000, 24, 2.0, 0.95, "chinh@ncc.vn"),
    ],
}

# Kết quả fefo_projection cố định cho 4 nguyên liệu trong kịch bản demo.
_PROJECTIONS = {
    "KEM_TUOI": {
        "remaining_qty": 0.0, "expired_qty": 6.0, "unmet_qty": 2.0, "projected_stock": -2.0,
        "plan": [
            {"day": "2026-11-20", "batch_id": "KEM-L1", "take_qty": 1.0},
            {"day": "2026-11-21", "batch_id": "KEM-L2", "take_qty": 3.0},
        ],
        "expired": [{"day": "2026-11-20", "batch_id": "KEM-L1", "qty": 6.0}],
    },
    "BO_LAT": {
        "remaining_qty": 0.0, "expired_qty": 0.0, "unmet_qty": 10.0, "projected_stock": -10.0,
        "plan": [{"day": "2026-11-20", "batch_id": "BO-L1", "take_qty": 1.0},
                 {"day": "2026-11-21", "batch_id": "BO-L1", "take_qty": 4.0}],
        "expired": [],
    },
    "BOT_MI": {
        "remaining_qty": 0.0, "expired_qty": 0.0, "unmet_qty": 0.0, "projected_stock": 0.0,
        "plan": [{"day": "2026-11-20", "batch_id": "BOT-L1", "take_qty": 12.0}],
        "expired": [],
    },
    "MASCARPONE": {
        "remaining_qty": 0.5, "expired_qty": 0.0, "unmet_qty": 0.0, "projected_stock": 0.5,
        "plan": [{"day": "2026-11-20", "batch_id": "MAS-L1", "take_qty": 0.5}],
        "expired": [],
    },
}


def _batch(batch_id, ingredient_id, qty, expiry, received):
    return {"batch_id": batch_id, "ingredient_id": ingredient_id, "qty": float(qty),
            "expiry_date": expiry, "received_at": received, "po_id": None}


def _po(po_id, ingredient_id, sup_id, sup_name, qty, price, status, delivery_status,
        expected, received_qty=0.0, alert_id=None):
    return {
        "po_id": po_id, "alert_id": alert_id, "sup_id": sup_id, "sup_name": sup_name,
        "ingredient_id": ingredient_id, "order_qty": float(qty), "unit_price": price,
        "total_cost": price * qty, "status": status, "delivery_status": delivery_status,
        "expected_delivery_date": expected, "received_qty": float(received_qty),
        "created_by": "manager", "created_at": "2026-11-10T09:00:00",
        "approved_by": "manager", "approved_at": "2026-11-10T09:05:00",
        "sent_by": "manager", "sent_at": "2026-11-10T09:10:00",
        "send_attempts": 1, "last_send_error": None,
        "closed_reason": None, "closed_by": None, "closed_at": None,
    }


def _option(letter, sup_id, sup_name, price, qty, lead_hours, meets, diff_pct, reason):
    arrival = FAKE_NOW + timedelta(hours=lead_hours)
    return {
        "option": letter, "sup_id": sup_id, "sup_name": sup_name, "unit_price": price,
        "order_qty": float(qty), "total_cost": price * qty, "lead_time_hours": lead_hours,
        "lead_time_days": math.ceil(lead_hours / 24), "arrival_at": _iso(arrival),
        "meets_earliest_preorder": meets, "cost_diff_pct_vs_A": diff_pct, "reason": reason,
    }


def _alert(alert_id, ingredient_id, projected, shortage, causes, main_cause, options, note,
           explanation, tradeoff_a, tradeoff_b):
    ing = next(i for i in _INGREDIENTS if i[0] == ingredient_id)
    return {
        "alert_id": alert_id, "run_id": 1, "status": "OPEN",
        "ingredient_id": ingredient_id, "ingredient_name": ing[1], "unit": ing[2],
        "projected_stock": projected, "min_stock": ing[3], "shortage_qty": shortage,
        "causes": causes, "main_cause": main_cause, "open_po_id": None,
        "options": options, "note": note, "snapshot_hash": f"stub-hash-{ingredient_id}",
        "explanation": explanation, "tradeoff_a": tradeoff_a, "tradeoff_b": tradeoff_b,
        "selected_option": None, "selected_qty": None, "selected_by": None,
        "selected_at": None, "po_id": None,
        "created_at": "2026-11-20T08:00:05", "resolved_at": None,
    }


def _make_alerts():
    return [
        _alert(
            1, "KEM_TUOI", -2.0, 4.0,
            [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0},
             {"code": "DAILY_USAGE", "qty": 3.0}],
            "EXPIRING_LOT",
            [_option("A", "NCC_CHINH", "NCC Chính", 90000, 4.0, 24, True, 0,
                     "Tổng chi phí thấp nhất trong các NCC hợp lệ"),
             _option("B", "NCC_HOA_TOC", "NCC Hỏa tốc", 103500, 4.0, 2, True, 15.0,
                     "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay")],
            None,
            "Kem tươi dự kiến thiếu vì 6 lít thuộc lô KEM-L1 sẽ hết hạn lúc 20:00 tối nay "
            "trước khi kịp dùng. Ngoài ra còn 3 lít cho đơn đặt trước ngày mai và 3 lít bán lẻ "
            "trong 3 ngày tới.",
            "Phương án A (NCC Chính) rẻ nhất, 360.000đ, nhưng hàng về lúc 08:00 ngày mai.",
            "Phương án B (NCC Hỏa tốc) đắt hơn 15%, 414.000đ, nhưng hàng về lúc 10:00 sáng nay.",
        ),
        _alert(
            2, "BO_LAT", -10.0, 13.0,
            [{"code": "SUPPLIER_DELAY", "qty": 20.0}, {"code": "PREORDER", "qty": 12.0},
             {"code": "DAILY_USAGE", "qty": 3.0}],
            "SUPPLIER_DELAY",
            [_option("A", "NCC_ANH_DUONG", "NCC Ánh Dương", 190000, 15.0, 48, False, 0,
                     "Tổng chi phí thấp nhất trong các NCC hợp lệ"),
             _option("B", "NCC_HOA_TOC", "NCC Hỏa tốc", 253000, 15.0, 2, True, 33.2,
                     "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay")],
            None,
            "PO-004 (20 kg từ NCC Chính) đã quá hạn giao nên chưa được tính vào tồn. Tồn hiện "
            "5 kg, sáng mai cần 12 kg cho croissant và bán lẻ 1 kg/ngày, nên dự kiến thiếu 10 kg.",
            "Phương án A (NCC Ánh Dương) rẻ nhất, 2.850.000đ, nhưng hàng về ngày 22/11, "
            "sau ngày giao đơn sớm nhất.",
            "Phương án B (NCC Hỏa tốc) đắt hơn 33,2%, 3.795.000đ, hàng về 10:00 sáng nay, "
            "kịp đơn ngày mai.",
        ),
        _alert(
            3, "BOT_MI", 0.0, 10.0,
            [{"code": "DAILY_USAGE", "qty": 36.0}, {"code": "PREORDER", "qty": 4.0}],
            "DAILY_USAGE",
            [_option("A", "NCC_ANH_DUONG", "NCC Ánh Dương", 14000, 10.0, 48, False, 0,
                     "Tổng chi phí thấp nhất trong các NCC hợp lệ"),
             _option("B", "NCC_HOA_TOC", "NCC Hỏa tốc", 18400, 10.0, 2, True, 31.4,
                     "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay")],
            None,
            "Bột mì thiếu chủ yếu do bán lẻ 12 kg/ngày (36 kg trong 3 ngày), cộng 4 kg cho đơn "
            "đặt trước, nên tồn dự kiến về 0 kg, thấp hơn mức tối thiểu 10 kg.",
            "Phương án A (NCC Ánh Dương) rẻ nhất, 140.000đ, nhưng hàng về ngày 22/11.",
            "Phương án B (NCC Hỏa tốc) đắt hơn 31,4%, 184.000đ, hàng về 10:00 sáng nay.",
        ),
        _alert(
            4, "MASCARPONE", 0.5, 1.5,
            [{"code": "PREORDER", "qty": 2.0}, {"code": "DAILY_USAGE", "qty": 1.5}],
            "PREORDER",
            [_option("A", "NCC_CHINH", "NCC Chính", 280000, 2.0, 24, True, 0,
                     "Chỉ có 1 NCC hợp lệ")],
            "Chỉ có 1 NCC hợp lệ (NCC Chính) nên chỉ có một phương án.",
            "Mascarpone thiếu do 2 kg cho đơn Tiramisu và 1,5 kg bán lẻ trong 3 ngày; tồn dự "
            "kiến 0,5 kg, thấp hơn mức tối thiểu 2 kg.",
            "Chỉ có NCC Chính bán Mascarpone nên không có phương án để so sánh.",
            None,
        ),
    ]


def _fresh_state(scenario: str):
    batches = [
        _batch("KEM-L1", "KEM_TUOI", 7, "2026-11-20T20:00:00", "2026-11-17T08:00:00"),
        _batch("KEM-L2", "KEM_TUOI", 3, "2026-11-25T08:00:00", "2026-11-19T08:00:00"),
        _batch("BOT-L1", "BOT_MI", 40, "2027-03-20T23:59:59", "2026-11-10T08:00:00"),
        _batch("TRUNG-L1", "TRUNG_GA", 60, "2026-11-23T23:59:59", "2026-11-13T08:00:00"),
        _batch("TRUNG-L2", "TRUNG_GA", 90, "2026-11-30T23:59:59", "2026-11-18T08:00:00"),
        _batch("BO-L1", "BO_LAT", 5, "2026-12-20T23:59:59", "2026-11-12T08:00:00"),
        _batch("MAS-L1", "MASCARPONE", 4, "2026-11-28T23:59:59", "2026-11-18T08:00:00"),
        _batch("DUONG-L1", "DUONG", 30, "2027-11-01T23:59:59", "2026-10-01T08:00:00"),
        _batch("SUA-L1", "SUA_TUOI", 20, "2026-11-26T23:59:59", "2026-11-19T08:00:00"),
        _batch("SOCOLA-L1", "SOCOLA_DEN", 12, "2027-06-01T23:59:59", "2026-10-15T08:00:00"),
        _batch("CACAO-L1", "BOT_CACAO", 4, "2027-06-01T23:59:59", "2026-10-15T08:00:00"),
        _batch("MEN-L1", "MEN_NO", 2, "2027-04-01T23:59:59", "2026-10-20T08:00:00"),
        _batch("VANI-L1", "VANI", 500, "2027-08-01T23:59:59", "2026-10-20T08:00:00"),
        _batch("MUOI-L1", "MUOI", 5, "2028-01-01T23:59:59", "2026-09-01T08:00:00"),
    ]
    pos = [
        _po("PO-001", "BOT_MI", "NCC_CHINH", "NCC Chính", 50, 16000, "SENT", "RECEIVED",
            "2026-11-12", received_qty=50),
        _po("PO-002", "TRUNG_GA", "NCC_CHINH", "NCC Chính", 120, 3500, "SENT", "RECEIVED",
            "2026-11-15", received_qty=120),
        _po("PO-003", "SUA_TUOI", "NCC_CHINH", "NCC Chính", 20, 30000, "SENT", "PENDING",
            "2026-11-21"),
        # Kịch bản "đơn nhập trễ": hạn giao là hôm qua (19/11) nên DELAYED.
        _po("PO-004", "BO_LAT", "NCC_CHINH", "NCC Chính", 20, 220000, "SENT", "DELAYED",
            "2026-11-19"),
    ]
    preorders = [
        {"order_id": "DH01", "cake_name": "Bánh kem", "quantity": 15, "delivery_date": "2026-11-21"},
        {"order_id": "DH02", "cake_name": "Croissant bơ", "quantity": 40, "delivery_date": "2026-11-21"},
        {"order_id": "DH03", "cake_name": "Bánh mì ngọt", "quantity": 20, "delivery_date": "2026-11-21"},
        {"order_id": "DH04", "cake_name": "Tiramisu", "quantity": 8, "delivery_date": "2026-11-22"},
        {"order_id": "DH05", "cake_name": "Bánh su kem", "quantity": 30, "delivery_date": "2026-11-22"},
        {"order_id": "DH06", "cake_name": "Bánh kem", "quantity": 5, "delivery_date": "2026-11-24"},
    ]
    state = {"batches": batches, "pos": pos, "preorders": preorders, "alerts": [], "runs": []}
    if scenario == "with_alerts":
        state["alerts"] = _make_alerts()
        state["runs"] = [{
            "run_id": 1, "run_date": "2026-11-20", "trigger": "SCHEDULED", "is_rerun": 0,
            "status": "SUCCEEDED", "started_at": "2026-11-20T08:00:02",
            "finished_at": "2026-11-20T08:00:09", "alert_count": 4, "error": None,
        }]
    return state


_S: dict = _fresh_state("with_alerts")


def reset(scenario: str = "with_alerts") -> None:
    """Đưa dữ liệu giả về ban đầu. scenario: 'with_alerts' (đã rà soát 08:00, có 4 cảnh báo)
    hoặc 'no_run' (hôm nay chưa rà soát)."""
    _S.clear()
    _S.update(_fresh_state(scenario))


# ---------------------------------------------------------------------------
# Tiện ích nội bộ
# ---------------------------------------------------------------------------
def _require_manager(user) -> None:
    if not user or user.get("role") != "MANAGER":
        raise GuardrailError("NOT_MANAGER", "Chỉ quản lý (MANAGER) mới được thực hiện thao tác này.")


def _find_po(po_id: str) -> dict:
    for po in _S["pos"]:
        if po["po_id"] == po_id:
            return po
    raise GuardrailError("INVALID_TRANSITION", f"Không tìm thấy {po_id}.")


def _find_alert(alert_id: int) -> dict | None:
    for a in _S["alerts"]:
        if a["alert_id"] == alert_id:
            return a
    return None


def _resolve_alert_of(po: dict) -> None:
    alert = _find_alert(po["alert_id"]) if po.get("alert_id") is not None else None
    if alert is not None:
        alert["status"] = "RESOLVED"
        alert["resolved_at"] = _iso(FAKE_NOW)


def _supplier_rows(ingredient_id: str) -> list[dict]:
    rows = _SUPPLIERS.get(ingredient_id)
    if rows is None:  # nguyên liệu không thuộc kịch bản: chỉ có NCC Chính
        rows = [("NCC_CHINH", "NCC Chính", 10000, 24, 1.0, 0.95, "chinh@ncc.vn")]
    return [{"sup_id": s[0], "name": s[1], "price": s[2], "lead_time_hours": s[3], "moq": s[4],
             "reliability": s[5], "contact_email": s[6], "active": 1} for s in rows]


# ---------------------------------------------------------------------------
# Nhóm 1 - giống contracts Mục 3 (repository)
# ---------------------------------------------------------------------------
def list_ingredients() -> list[dict]:
    return [{"id": i[0], "name": i[1], "unit": i[2], "min_stock": i[3],
             "shelf_life_days": i[4], "daily_usage": i[5]} for i in _INGREDIENTS]


def get_ingredient(ingredient_id):
    return next((i for i in list_ingredients() if i["id"] == ingredient_id), None)


def get_batches_fefo(ingredient_id, at: datetime) -> list[dict]:
    """Chỉ lô còn hạn tại `at`, qty > 0, sắp theo expiry_date rồi received_at."""
    lots = [b for b in _S["batches"]
            if b["ingredient_id"] == ingredient_id and b["qty"] > 0
            and datetime.fromisoformat(b["expiry_date"]) > at]
    lots.sort(key=lambda b: (b["expiry_date"], b["received_at"]))
    return copy.deepcopy(lots)


def get_suppliers(ingredient_id) -> list[dict]:
    return _supplier_rows(ingredient_id)


# ---------------------------------------------------------------------------
# Nhóm 2 - giống contracts Mục 4 (fefo)
# ---------------------------------------------------------------------------
def fefo_projection(ingredient_id, days: int, at: datetime | None = None) -> dict:
    """4 nguyên liệu kịch bản trả số cố định; nguyên liệu khác tính đơn giản."""
    if ingredient_id in _PROJECTIONS:
        return copy.deepcopy(_PROJECTIONS[ingredient_id])
    ing = get_ingredient(ingredient_id)
    lots = get_batches_fefo(ingredient_id, at or FAKE_NOW)
    on_hand = sum(b["qty"] for b in lots)
    projected = on_hand - ing["daily_usage"] * days
    return {"remaining_qty": max(0.0, projected), "expired_qty": 0.0,
            "unmet_qty": max(0.0, -projected), "projected_stock": projected,
            "plan": [], "expired": []}


# ---------------------------------------------------------------------------
# Nhóm 3 - giống contracts Mục 8, 9, 10
# ---------------------------------------------------------------------------
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]:
    if get_ingredient(ingredient_id) is None:
        raise GuardrailError("INGREDIENT_NOT_FOUND", "Không tìm thấy nguyên liệu.")
    if qty <= 0:
        raise GuardrailError("QTY_INVALID", "Số lượng xuất phải lớn hơn 0.")
    lots = [b for b in _S["batches"]
            if b["ingredient_id"] == ingredient_id and b["qty"] > 0
            and datetime.fromisoformat(b["expiry_date"]) > FAKE_NOW]
    lots.sort(key=lambda b: (b["expiry_date"], b["received_at"]))
    available = sum(b["qty"] for b in lots)
    if qty > available:
        raise GuardrailError(
            "QTY_INVALID", f"Xuất quá tồn còn hạn: yêu cầu {qty:g}, chỉ còn {available:g}.")
    taken, remaining = [], qty
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot["qty"], remaining)
        lot["qty"] = round(lot["qty"] - take, 6)
        remaining = round(remaining - take, 6)
        taken.append({"batch_id": lot["batch_id"], "taken": take})
    return taken


def mark_received(po_id: str, qty: float, user, expiry_date: datetime | None = None) -> str:
    _require_manager(user)
    po = _find_po(po_id)
    if po["status"] != "SENT" or po["delivery_status"] not in ("PENDING", "DELAYED"):
        raise GuardrailError("INVALID_TRANSITION", f"{po_id} không ở trạng thái chờ nhận hàng.")
    if qty <= 0:
        raise GuardrailError("QTY_INVALID", "Số lượng nhận phải lớn hơn 0.")
    ing = get_ingredient(po["ingredient_id"])
    existing = [b for b in _S["batches"] if b["ingredient_id"] == po["ingredient_id"]]
    prefix = existing[0]["batch_id"].rsplit("-L", 1)[0] if existing else po["ingredient_id"][:3]
    n = len(existing) + 1
    while any(b["batch_id"] == f"{prefix}-L{n}" for b in _S["batches"]):
        n += 1
    batch_id = f"{prefix}-L{n}"
    expiry = expiry_date or (FAKE_NOW + timedelta(days=ing["shelf_life_days"]))
    batch = _batch(batch_id, po["ingredient_id"], qty, _iso(expiry), _iso(FAKE_NOW))
    batch["po_id"] = po_id
    _S["batches"].append(batch)
    po["delivery_status"] = "RECEIVED"
    po["received_qty"] = float(qty)
    return batch_id


def create_draft_po(alert_id: int, option: str, qty: float, user) -> str:
    _require_manager(user)
    alert = _find_alert(alert_id)
    if alert is None:
        raise GuardrailError("ALERT_NOT_OPEN", "Không tìm thấy cảnh báo.")
    if alert["status"] != "OPEN":
        if alert.get("po_id"):
            raise GuardrailError("DUPLICATE_PO", f"Cảnh báo này đã có PO {alert['po_id']}.")
        raise GuardrailError("ALERT_NOT_OPEN", "Cảnh báo không còn ở trạng thái OPEN.")
    if qty <= 0:
        raise GuardrailError("QTY_INVALID", "Số lượng mua phải lớn hơn 0.")
    opt = next((o for o in alert["options"] if o["option"] == option), None)
    if opt is None:
        raise GuardrailError("SUPPLIER_INVALID", f"Phương án {option} không nằm trong cảnh báo.")
    moq = next(s["moq"] for s in _supplier_rows(alert["ingredient_id"]) if s["sup_id"] == opt["sup_id"])
    if qty < moq or abs(qty / moq - round(qty / moq)) > 1e-9:
        raise GuardrailError("MOQ_VIOLATION", f"Số lượng phải là bội số của MOQ ({moq:g}).")
    for p in _S["pos"]:
        if (p["ingredient_id"] == alert["ingredient_id"] and p["closed_reason"] is None
                and p["status"] in ("DRAFT", "APPROVED")):
            raise GuardrailError("DUPLICATE_PO", f"Nguyên liệu này đã có PO đang mở ({p['po_id']}).")
    po_id = f"PO-{len(_S['pos']) + 1:03d}"
    po = _po(po_id, alert["ingredient_id"], opt["sup_id"], opt["sup_name"], qty,
             opt["unit_price"], "DRAFT", None, opt["arrival_at"][:10], alert_id=alert_id)
    po.update({"created_by": user["username"], "created_at": _iso(FAKE_NOW),
               "approved_by": None, "approved_at": None, "sent_by": None, "sent_at": None,
               "send_attempts": 0})
    _S["pos"].append(po)
    alert.update({"status": "SELECTED", "selected_option": option, "selected_qty": float(qty),
                  "selected_by": user["username"], "selected_at": _iso(FAKE_NOW), "po_id": po_id})
    return po_id


def approve_po(po_id: str, user) -> None:
    _require_manager(user)
    po = _find_po(po_id)
    if po["status"] != "DRAFT" or po["closed_reason"] is not None:
        raise GuardrailError("INVALID_TRANSITION", f"{po_id} không ở trạng thái DRAFT để duyệt.")
    po.update({"status": "APPROVED", "approved_by": user["username"], "approved_at": _iso(FAKE_NOW)})


def reject_po(po_id: str, user, reason: str = "") -> None:
    _require_manager(user)
    po = _find_po(po_id)
    if po["status"] != "DRAFT" or po["closed_reason"] is not None:
        raise GuardrailError("INVALID_TRANSITION", f"{po_id} không ở trạng thái DRAFT để từ chối.")
    po.update({"closed_reason": "REJECTED", "closed_by": user["username"], "closed_at": _iso(FAKE_NOW)})
    _resolve_alert_of(po)


def cancel_po(po_id: str, user) -> None:
    _require_manager(user)
    po = _find_po(po_id)
    if po["status"] != "APPROVED" or po["closed_reason"] is not None:
        raise GuardrailError("INVALID_TRANSITION", f"{po_id} không ở trạng thái APPROVED để hủy.")
    po.update({"closed_reason": "CANCELLED", "closed_by": user["username"], "closed_at": _iso(FAKE_NOW)})
    _resolve_alert_of(po)


def send_po(po_id: str, user) -> str:
    """Giả lập: PO gửi cho NCC Hỏa tốc thì LẦN ĐẦU thất bại (thiếu email) để thử nút Gửi lại."""
    _require_manager(user)
    po = _find_po(po_id)
    if po["status"] != "APPROVED" or po["closed_reason"] is not None:
        raise GuardrailError("INVALID_TRANSITION", f"{po_id} không ở trạng thái APPROVED để gửi.")
    po["send_attempts"] += 1
    if po["sup_id"] == "NCC_HOA_TOC" and po["send_attempts"] == 1:
        po["last_send_error"] = "NCC Hỏa tốc thiếu contact_email (giả lập)."
        raise GuardrailError("SEND_FAILED", po["last_send_error"])
    po.update({"status": "SENT", "delivery_status": "PENDING", "sent_by": user["username"],
               "sent_at": _iso(FAKE_NOW), "last_send_error": None})
    _resolve_alert_of(po)
    return f"outbox/{po_id}.csv"  # hàm thật mới tạo file CSV thật


def run_morning_audit(trigger: str, rerun: bool = False, user=None) -> dict:
    today_run = get_today_run()
    if today_run is not None and not rerun:
        return {"run_id": today_run["run_id"], "status": "ALREADY_DONE", "alert_ids": [],
                "message": "Hôm nay đã rà soát rồi. Tick ô 'Chạy lại' nếu muốn chạy lần nữa."}
    time.sleep(1.0)  # giả lập thời gian chạy
    new_ids = []
    if not _S["alerts"]:
        _S["alerts"] = _make_alerts()
        new_ids = [a["alert_id"] for a in _S["alerts"]]
    run_id = len(_S["runs"]) + 1
    _S["runs"].append({
        "run_id": run_id, "run_date": _iso(FAKE_NOW)[:10], "trigger": trigger,
        "is_rerun": 1 if rerun else 0, "status": "SUCCEEDED",
        "started_at": _iso(FAKE_NOW + timedelta(minutes=5 * run_id)),
        "finished_at": _iso(FAKE_NOW + timedelta(minutes=5 * run_id, seconds=7)),
        "alert_count": len(_S["alerts"]), "error": None,
    })
    return {"run_id": run_id, "status": "SUCCEEDED", "alert_ids": new_ids,
            "message": f"Rà soát xong: {len(_S['alerts'])} cảnh báo."}


# ---------------------------------------------------------------------------
# CHƯA CÓ TRONG CONTRACTS - giao diện cần, phải hỏi nhóm bổ sung
# ---------------------------------------------------------------------------
def list_preorders() -> list[dict]:
    """Toàn bộ đơn đặt trước: order_id, cake_name, quantity, delivery_date."""
    return copy.deepcopy(_S["preorders"])


def list_pos() -> list[dict]:
    """Toàn bộ PO (kể cả đã đóng/đã nhận)."""
    return copy.deepcopy(_S["pos"])


def list_alerts() -> list[dict]:
    """Toàn bộ cảnh báo, kèm explanation/tradeoff_a/tradeoff_b và lựa chọn của quản lý."""
    return copy.deepcopy(_S["alerts"])


def get_today_run() -> dict | None:
    """Phiên rà soát mới nhất của hôm nay (hoặc None)."""
    runs = [r for r in _S["runs"] if r["run_date"] == _iso(FAKE_NOW)[:10]]
    return copy.deepcopy(runs[-1]) if runs else None


def dismiss_alert(alert_id: int, user) -> None:
    """Quản lý bấm 'Bỏ qua': OPEN -> DISMISSED (log ALERT_DISMISSED ở hàm thật)."""
    _require_manager(user)
    alert = _find_alert(alert_id)
    if alert is None or alert["status"] != "OPEN":
        raise GuardrailError("ALERT_NOT_OPEN", "Cảnh báo không còn ở trạng thái OPEN.")
    alert["status"] = "DISMISSED"
    alert["resolved_at"] = _iso(FAKE_NOW)
