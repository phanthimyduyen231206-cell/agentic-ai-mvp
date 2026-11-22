"""4 tool đọc + tool registry cấp cho Gemini. Không có hàm ghi.

Chủ file: Quỳnh Anh (Q03). Chữ ký hàm: xem docs/contracts.md (Mục 6), logic: đặc tả Mục 4.

- Chỉ đọc dữ liệu qua data/repository.py và services/fefo.py; lead_time_days lấy từ services/calc.py
  để tool và calc luôn ra cùng một số. Không ghi DB, không gọi LLM.
- Mọi hàm nhận tham số dạng keyword (core_agent gọi `tools[name](**args)`) và trả `dict` JSON được
  (ngày giờ là chuỗi ISO có chữ T, số đã làm tròn).
- Lỗi đầu vào không raise mà trả `{"ok": False, "error_code": ..., "message": ...}` để Gemini đọc được.
- Khoảng tính `days`: ngày t = 0 … days − 1 (ngày 0 = hôm nay), giống fefo_projection và data/expected.md.
- `READ_TOOLS` chỉ chứa đúng 4 tool đọc; KHÔNG có create_draft_po, write_audit_log (đặc tả Mục 8).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from data import repository
from services import calc, clock
from services.fefo import fefo_projection

_EPS = 1e-9


class _ToolInputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Hàm phụ
# ---------------------------------------------------------------------------

def _r(x: Any) -> float:
    """Làm tròn để tránh sai số float; -0.0 → 0.0."""
    return round(float(x or 0), 6) + 0.0


def _iso(value: Any) -> str | None:
    """datetime/date/chuỗi → chuỗi ISO (datetime có chữ T, tới giây)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).isoformat(timespec="seconds") if len(text) > 10 else text
    except ValueError:
        return text


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time())
    return datetime.fromisoformat(str(value).strip()).replace(tzinfo=None)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error_code": code, "message": message}


def _parse_at(at: Any) -> datetime:
    if at is None or (isinstance(at, str) and not at.strip()):
        return clock.now()
    try:
        return _to_datetime(at)
    except (TypeError, ValueError):
        raise _ToolInputError("INVALID_ARGUMENT", f"'at' phải có dạng YYYY-MM-DDTHH:MM:SS, nhận {at!r}")


def _parse_days(days: Any) -> int:
    """Nhận int, hoặc số thực/chuỗi có giá trị nguyên (Gemini hay gửi 3.0); phải ≥ 0."""
    try:
        value = float(days)
    except (TypeError, ValueError):
        raise _ToolInputError("INVALID_ARGUMENT", f"'days' phải là số nguyên ≥ 0, nhận {days!r}")
    if isinstance(days, bool) or value < 0 or value != int(value):
        raise _ToolInputError("INVALID_ARGUMENT", f"'days' phải là số nguyên ≥ 0, nhận {days!r}")
    return int(value)


def _norm_id(ingredient_id: Any) -> str | None:
    if ingredient_id is None:
        return None
    text = str(ingredient_id).strip().upper()
    return text or None


def _get_ingredient(ingredient_id: str) -> dict:
    ing = repository.get_ingredient(ingredient_id)
    if ing is None:
        raise _ToolInputError(
            "INGREDIENT_NOT_FOUND",
            f"Không có nguyên liệu {ingredient_id!r}. Gọi check_inventory_fefo() không tham số để xem danh sách mã.",
        )
    return ing


def _window(days: int, day0: date) -> tuple[date, date]:
    """[ngày 0, ngày days − 1]. days = 0 → khoảng rỗng (last < first)."""
    return day0, day0 + timedelta(days=days - 1)


def _safe(fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except _ToolInputError as e:
        return _error(e.code, e.message)


# ---------------------------------------------------------------------------
# 1. check_inventory_fefo
# ---------------------------------------------------------------------------

def _inventory_one(ing: dict, at: datetime, days: int) -> dict:
    day0, last_day = _window(days, at.date())
    batches = []
    on_hand = expiring = 0.0
    # Lô còn hạn tại `at`, đã sắp FEFO bởi hàm dùng chung của repository.
    for b in repository.get_batches_fefo(ing["id"], at):
        qty = float(b["qty"])
        expiry = _to_datetime(b["expiry_date"])
        within = days > 0 and expiry.date() <= last_day
        on_hand += qty
        if within:
            expiring += qty
        batches.append({
            "batch_id": b["batch_id"],
            "qty": _r(qty),
            "unit": ing["unit"],
            "expiry_date": _iso(expiry),
            "received_at": _iso(b.get("received_at")),
            "days_left": round((expiry - at).total_seconds() / 86400, 2),
            "expires_within_days": within,
        })

    result = {
        "ok": True,
        "ingredient_id": ing["id"],
        "ingredient_name": ing["name"],
        "unit": ing["unit"],
        "at": _iso(at),
        "days": days,
        "min_stock": _r(ing.get("min_stock")),
        "on_hand_qty": _r(on_hand),
        "expiring_qty": _r(expiring),
        "batches": batches,
    }
    if days > 0:
        # Phân bổ nhu cầu theo FEFO do fefo.py làm (đặc tả 5.2): lô nào hết hạn trước khi kịp dùng.
        proj = fefo_projection(ing["id"], days, at)
        result["fefo_projection"] = {
            "expired_qty": proj["expired_qty"],
            "unmet_qty": proj["unmet_qty"],
            "remaining_qty": proj["remaining_qty"],
            "projected_stock": proj["projected_stock"],
            "expired": proj["expired"],
            "plan": proj["plan"],
        }
    return result


def check_inventory_fefo(ingredient_id: str | None = None, at: Any = None, days: Any = 0) -> dict:
    """Lô còn hạn tại `at` theo thứ tự FEFO; on_hand_qty, expiring_qty (lô hết hạn trong `days` ngày).

    Không truyền ingredient_id → danh sách mọi nguyên liệu kèm tồn còn hạn (để tra mã).
    """
    def run() -> dict:
        at_dt = _parse_at(at)
        n_days = _parse_days(days)
        ing_id = _norm_id(ingredient_id)
        if ing_id is not None:
            return _inventory_one(_get_ingredient(ing_id), at_dt, n_days)
        items = []
        for ing in repository.get_ingredients():
            one = _inventory_one(ing, at_dt, n_days)
            items.append({
                "ingredient_id": one["ingredient_id"],
                "ingredient_name": one["ingredient_name"],
                "unit": one["unit"],
                "min_stock": one["min_stock"],
                "on_hand_qty": one["on_hand_qty"],
                "expiring_qty": one["expiring_qty"],
                "batch_count": len(one["batches"]),
            })
        return {"ok": True, "at": _iso(at_dt), "days": n_days, "items": items}

    return _safe(run)


# ---------------------------------------------------------------------------
# 2. calc_preorder_demand
# ---------------------------------------------------------------------------

def _preorder_one(ing: dict, days: int, day0: date) -> dict:
    first, last = _window(days, day0)
    rows = repository.get_pre_order_needs(ing["id"], first, last) if days > 0 else []
    orders = [{
        "order_id": r["order_id"],
        "cake_name": r["cake_name"],
        "quantity": r["quantity"],
        "qty_per_cake": _r(r["qty_per_cake"]),
        "need_qty": _r(r["need_qty"]),
        "delivery_date": _iso(r["delivery_date"]),
    } for r in rows]
    preorder = sum(float(r["need_qty"] or 0) for r in rows)
    return {
        "ingredient_id": ing["id"],
        "ingredient_name": ing["name"],
        "unit": ing["unit"],
        "preorder_demand": _r(preorder),
        "daily_usage": _r(ing.get("daily_usage")),
        "retail_demand": _r(float(ing.get("daily_usage") or 0) * days),
        "earliest_preorder_date": orders[0]["delivery_date"] if orders else None,
        "orders": orders,
    }


def calc_preorder_demand(ingredient_id: str | None = None, days: Any = 3) -> dict:
    """preorder_demand = Σ quantity × qty_per_cake của đơn giao trong ngày 0 … days − 1;
    retail_demand = daily_usage × days; kèm ngày giao sớm nhất."""
    def run() -> dict:
        n_days = _parse_days(days)
        day0 = clock.today()
        first, last = _window(n_days, day0)
        base = {"ok": True, "days": n_days, "from_date": first.isoformat(),
                "to_date": last.isoformat() if n_days > 0 else None}
        ing_id = _norm_id(ingredient_id)
        if ing_id is not None:
            return base | _preorder_one(_get_ingredient(ing_id), n_days, day0)
        return base | {"items": [_preorder_one(ing, n_days, day0) for ing in repository.get_ingredients()]}

    return _safe(run)


# ---------------------------------------------------------------------------
# 3. check_delayed_po
# ---------------------------------------------------------------------------

def _po_view(po: dict) -> dict:
    remaining = float(po.get("order_qty") or 0) - float(po.get("received_qty") or 0)
    return {
        "po_id": po["po_id"],
        "ingredient_id": po["ingredient_id"],
        "sup_id": po["sup_id"],
        "order_qty": _r(po.get("order_qty")),
        "received_qty": _r(po.get("received_qty")),
        "remaining_qty": _r(max(remaining, 0.0)),
        "status": po["status"],
        "delivery_status": po.get("delivery_status"),
        "expected_delivery_date": _iso(po.get("expected_delivery_date")),
    }


def check_delayed_po(ingredient_id: str | None = None, days: Any = 3) -> dict:
    """PO đã SENT: incoming_stock (delivery PENDING, về trong ngày 0 … days − 1) và delayed_qty
    (delivery_status = DELAYED đang lưu trong DB); PO DRAFT/APPROVED chưa đóng để chống trùng.

    PO SENT + PENDING đã quá expected_delivery_date nhưng chưa được rà soát đánh dấu DELAYED
    chỉ liệt kê ở `overdue_not_yet_flagged`, không cộng vào delayed_qty, không ghi gì vào DB.
    """
    def run() -> dict:
        n_days = _parse_days(days)
        ing_id = _norm_id(ingredient_id)
        if ing_id is not None:
            _get_ingredient(ing_id)
        day0 = clock.today()
        first, last = _window(n_days, day0)

        incoming, delayed, overdue = [], [], []
        for po in repository.get_purchase_orders(status="SENT", ingredient_id=ing_id):
            if po.get("closed_reason"):
                continue
            view = _po_view(po)
            if view["remaining_qty"] <= _EPS:
                continue
            status = po.get("delivery_status")
            if status == "DELAYED":
                delayed.append(view)
            elif status == "PENDING" and po.get("expected_delivery_date"):
                arrival = date.fromisoformat(str(po["expected_delivery_date"])[:10])
                if arrival < day0:
                    overdue.append(view)
                elif n_days > 0 and first <= arrival <= last:
                    incoming.append(view)

        open_unsent = [_po_view(po) for po in repository.get_open_purchase_orders(ing_id)
                       if po.get("status") in ("DRAFT", "APPROVED")]

        result = {
            "ok": True,
            "days": n_days,
            "from_date": first.isoformat(),
            "to_date": last.isoformat() if n_days > 0 else None,
            "incoming_pos": incoming,
            "delayed_pos": delayed,
            "overdue_not_yet_flagged": overdue,
            "open_unsent_pos": open_unsent,
        }
        if ing_id is not None:
            result["ingredient_id"] = ing_id
            result["incoming_stock"] = _r(sum(p["remaining_qty"] for p in incoming))
            result["delayed_qty"] = _r(sum(p["remaining_qty"] for p in delayed))
        else:
            # Không cộng chung các nguyên liệu khác đơn vị; tổng theo từng nguyên liệu.
            totals: dict[str, dict] = {}
            for key, pos in (("incoming_stock", incoming), ("delayed_qty", delayed)):
                for p in pos:
                    t = totals.setdefault(p["ingredient_id"], {"ingredient_id": p["ingredient_id"],
                                                               "incoming_stock": 0.0, "delayed_qty": 0.0})
                    t[key] = _r(t[key] + p["remaining_qty"])
            result["by_ingredient"] = [totals[k] for k in sorted(totals)]
        return result

    return _safe(run)


# ---------------------------------------------------------------------------
# 4. compare_suppliers
# ---------------------------------------------------------------------------

def compare_suppliers(ingredient_id: str) -> dict:
    """Danh sách NCC bán nguyên liệu: đơn giá, lead_time_hours, lead_time_days (= calc.lead_time_days),
    moq, reliability, active, has_contact_email (không trả địa chỉ email).
    Không xếp hạng (việc chọn A/B do calc.py làm)."""
    def run() -> dict:
        ing_id = _norm_id(ingredient_id)
        if ing_id is None:
            raise _ToolInputError("INVALID_ARGUMENT", "compare_suppliers cần ingredient_id.")
        ing = _get_ingredient(ing_id)
        suppliers = [{
            "sup_id": s["sup_id"],
            "name": s["name"],
            "price": _r(s["price"]),
            "lead_time_hours": _r(s["lead_time_hours"]),
            "lead_time_days": calc.lead_time_days(s["lead_time_hours"]),
            "moq": _r(s["moq"]),
            "reliability": _r(s["reliability"]),
            "active": bool(s["active"]),
            "has_contact_email": bool(str(s.get("contact_email") or "").strip()),
        } for s in repository.get_suppliers(ing_id)]
        return {"ok": True, "ingredient_id": ing["id"], "ingredient_name": ing["name"],
                "unit": ing["unit"], "suppliers": suppliers}

    return _safe(run)


# ---------------------------------------------------------------------------
# Tool registry (đặc tả Mục 8 – guardrail lớp 1: chỉ 4 tool đọc)
# ---------------------------------------------------------------------------

READ_TOOLS: dict[str, Callable[..., dict]] = {
    "check_inventory_fefo": check_inventory_fefo,
    "calc_preorder_demand": calc_preorder_demand,
    "check_delayed_po": check_delayed_po,
    "compare_suppliers": compare_suppliers,
}

_ID_DESC = "Mã nguyên liệu viết hoa không dấu, vd KEM_TUOI, BOT_MI."

TOOL_DECLARATIONS: dict[str, dict] = {
    "check_inventory_fefo": {
        "description": ("Tồn kho còn hạn theo từng lô, xếp theo hạn dùng (FEFO), kèm on_hand_qty và "
                        "expiring_qty. days > 0 để xem lô nào hết hạn trước khi kịp dùng. "
                        "Không truyền ingredient_id để xem danh sách mọi nguyên liệu."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ingredient_id": {"type": "STRING", "description": _ID_DESC + " Bỏ trống để xem tất cả."},
                "at": {"type": "STRING", "description": "Thời điểm xem tồn, dạng YYYY-MM-DDTHH:MM:SS. Bỏ trống = bây giờ."},
                "days": {"type": "INTEGER", "description": "Số ngày tới để xét lô sắp hết hạn, mặc định 0."},
            },
            "required": [],
        },
    },
    "calc_preorder_demand": {
        "description": ("Lượng nguyên liệu cần cho các đơn đặt trước giao trong số ngày tới (preorder_demand), "
                        "nhu cầu bán lẻ daily_usage × days (retail_demand) và ngày giao sớm nhất."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ingredient_id": {"type": "STRING", "description": _ID_DESC + " Bỏ trống để xem tất cả."},
                "days": {"type": "INTEGER", "description": "Số ngày tính từ hôm nay, mặc định 3."},
            },
            "required": [],
        },
    },
    "check_delayed_po": {
        "description": ("PO đã gửi: hàng sắp về (incoming_stock), hàng giao trễ (delayed_qty), PO quá hạn chưa "
                        "được đánh dấu trễ, và PO DRAFT/APPROVED chưa gửi."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ingredient_id": {"type": "STRING", "description": _ID_DESC + " Bỏ trống để xem tất cả."},
                "days": {"type": "INTEGER", "description": "Số ngày tính từ hôm nay cho hàng sắp về, mặc định 3."},
            },
            "required": [],
        },
    },
    "compare_suppliers": {
        "description": ("Danh sách nhà cung cấp bán nguyên liệu: đơn giá, thời gian giao (giờ, ngày), MOQ, "
                        "độ tin cậy, còn hoạt động không. Không xếp hạng."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ingredient_id": {"type": "STRING", "description": _ID_DESC},
            },
            "required": ["ingredient_id"],
        },
    },
}


def get_gemini_function_declarations() -> list:
    """Dựng `google.genai.types.FunctionDeclaration` cho 4 tool đọc (import thư viện khi cần)."""
    from google.genai import types

    declarations = []
    for name in READ_TOOLS:
        spec = TOOL_DECLARATIONS[name]
        params = spec["parameters"]
        declarations.append(types.FunctionDeclaration(
            name=name,
            description=spec["description"],
            parameters=types.Schema(
                type=params["type"],
                properties={k: types.Schema(type=v["type"], description=v["description"])
                            for k, v in params["properties"].items()},
                required=list(params["required"]),
            ),
        ))
    return declarations
