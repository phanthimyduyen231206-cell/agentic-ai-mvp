"""compute_alerts(): projected_stock, mã nguyên nhân, chọn A/B, làm tròn MOQ. Không gọi LLM.

Chủ file: Quỳnh Anh (Q02). Chữ ký hàm và JSON trả về: docs/contracts.md (Mục 5).
Quy tắc nghiệp vụ: đặc tả Mục 5.1 – 5.4.

Với mỗi nguyên liệu (H = 3 ngày, ngày 0 = ngày rà soát):
1. projected_stock(H) = fefo_projection(id, H)["projected_stock"]; chỉ cảnh báo khi < min_stock.
   shortage_qty = min_stock − projected_stock(H).
2. Mã nguyên nhân (chỉ mã có lượng > 0, xếp theo lượng giảm dần):
   PREORDER = Σ need_qty của đơn giao trong ngày 0 … H−1; DAILY_USAGE = daily_usage × H;
   EXPIRING_LOT = expired_qty theo FEFO; SUPPLIER_DELAY = lượng còn thiếu của PO SENT + DELAYED.
   OPEN_PO (qty = None) nếu có PO DRAFT/APPROVED chưa đóng; mã này không tham gia xếp hạng.
   main_cause = mã có lượng lớn nhất (hòa thì theo thứ tự _CAUSE_PRIORITY).
3. Có PO mở → open_po_id, options = [], không đề xuất A/B mới.
4. NCC hợp lệ: bán đúng nguyên liệu, active = 1, moq > 0, có contact_email.
   Với mỗi NCC s: D = max(H, lead_time_days(s)), shortage(s) = max(0, min_stock − projected_stock(D)),
   order_qty = ceil(shortage(s) / moq) × moq.
5. A = tổng chi phí thấp nhất (hòa → lead time ngắn hơn); B = lead time ngắn nhất (hòa → chi phí thấp hơn).
   Còn hòa nữa thì chọn NCC có reliability cao hơn, cuối cùng theo sup_id.
   A trùng B hoặc chỉ 1 NCC hợp lệ → 1 phương án + note; không có NCC hợp lệ → note "Cần xử lý thủ công".
   Có A và B: reason A = "Rẻ nhất, về chậm hơn B X giờ", reason B = "Nhanh nhất, đắt hơn A Y% (Zđ)".
6. meets_earliest_preorder: arrival_at <= ngày giao đơn đặt trước sớm nhất lúc 07:00 (PREORDER_CUTOFF);
   không có đơn đặt trước → true.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from data import repository
from services import clock
from services.fefo import fefo_projection

H = 3  # khoảng tính khi rà soát (đặc tả 5.3)
_EPS = 1e-9
_CAUSE_PRIORITY = ("PREORDER", "DAILY_USAGE", "EXPIRING_LOT", "SUPPLIER_DELAY")
NOTE_MANUAL = "Cần xử lý thủ công"
PREORDER_CUTOFF = time(7, 0)  # hàng phải về trước 07:00 ngày giao đơn đặt trước mới kịp làm bánh


def _r(x: float) -> float:
    """Làm tròn để tránh sai số float (0.6000000001); -0.0 → 0.0."""
    return round(float(x), 6) + 0.0


def _run_time(run_date: date) -> datetime:
    """Mốc tính: giờ hiện tại nếu rà soát hôm nay, nếu không thì 08:00 của run_date."""
    if isinstance(run_date, datetime):
        run_date = run_date.date()
    now = clock.now()
    if run_date == now.date():
        return now
    return datetime.combine(run_date, time(8, 0))


def lead_time_days(lead_time_hours) -> int:
    """Số ngày lịch = ceil(giờ / 24); giao trong ngày (vd 2 giờ) tính là 1 ngày."""
    return max(1, math.ceil(float(lead_time_hours) / 24))


def round_up_moq(qty: float, moq: float) -> float:
    """Làm tròn lên bội số của MOQ. Ví dụ cần 23, MOQ 10 → 30."""
    if qty <= _EPS:
        return 0.0
    packs = math.ceil(round(qty / moq, 9))
    return _r(packs * moq)


# ---------------------------------------------------------------------------
# Nguyên nhân
# ---------------------------------------------------------------------------

def _preorder_needs(ingredient_id, day0: date) -> list[dict]:
    last_day = day0 + timedelta(days=H - 1)
    return [row for row in repository.get_pre_order_needs(ingredient_id, day0, last_day)
            if float(row.get("need_qty") or 0) > _EPS]


def _delayed_qty(ingredient_id) -> float:
    total = 0.0
    for po in repository.get_purchase_orders(status="SENT", ingredient_id=ingredient_id):
        if po.get("delivery_status") != "DELAYED" or po.get("closed_reason"):
            continue
        total += max(0.0, float(po["order_qty"] or 0) - float(po.get("received_qty") or 0))
    return total


def _open_pos(ingredient_id) -> list[dict]:
    """PO chưa gửi (DRAFT/APPROVED) và chưa đóng → mã OPEN_PO."""
    return [po for po in repository.get_open_purchase_orders(ingredient_id)
            if po.get("status") in ("DRAFT", "APPROVED")]


def build_causes(preorder_qty: float, daily_qty: float, expired_qty: float,
                 delayed_qty: float, has_open_po: bool) -> tuple[list[dict], str | None]:
    """Trả về (causes, main_cause). causes xếp theo lượng giảm dần, OPEN_PO cuối cùng."""
    amounts = {
        "PREORDER": preorder_qty,
        "DAILY_USAGE": daily_qty,
        "EXPIRING_LOT": expired_qty,
        "SUPPLIER_DELAY": delayed_qty,
    }
    ranked = sorted(
        (code for code in _CAUSE_PRIORITY if amounts[code] > _EPS),
        key=lambda code: (-amounts[code], _CAUSE_PRIORITY.index(code)),
    )
    causes = [{"code": code, "qty": _r(amounts[code])} for code in ranked]
    main_cause = ranked[0] if ranked else None
    if has_open_po:
        causes.append({"code": "OPEN_PO", "qty": None})
    return causes, main_cause


# ---------------------------------------------------------------------------
# Nhà cung cấp và phương án A/B
# ---------------------------------------------------------------------------

def valid_suppliers(ingredient_id) -> list[dict]:
    """NCC hợp lệ (đặc tả 5.4): bán đúng nguyên liệu, active = 1, moq > 0, có contact_email."""
    result = []
    for s in repository.get_suppliers(ingredient_id=ingredient_id, active_only=True):
        if s.get("ingredient_id") != ingredient_id or int(s.get("active") or 0) != 1:
            continue
        if float(s.get("moq") or 0) <= 0 or not str(s.get("contact_email") or "").strip():
            continue
        result.append(s)
    return result


def quote_suppliers(ingredient: dict, at: datetime, projections: dict[int, float] | None = None) -> list[dict]:
    """Tính order_qty, tổng tiền, thời điểm về cho từng NCC hợp lệ (chưa chọn A/B).

    projections: cache {số ngày D: projected_stock(D)} để không gọi FEFO lặp lại.
    """
    ingredient_id = ingredient["id"]
    min_stock = float(ingredient["min_stock"])
    projections = {} if projections is None else projections
    quotes = []
    for s in valid_suppliers(ingredient_id):
        hours = int(s["lead_time_hours"])
        days = max(H, lead_time_days(hours))
        if days not in projections:
            projections[days] = fefo_projection(ingredient_id, days, at)["projected_stock"]
        shortage = max(0.0, min_stock - projections[days])
        order_qty = round_up_moq(shortage, float(s["moq"]))
        price = float(s["price"])
        quotes.append({
            "sup_id": s["sup_id"],
            "sup_name": s["name"],
            "unit_price": price,
            "order_qty": order_qty,
            "total_cost": _r(price * order_qty),
            "lead_time_hours": hours,
            "lead_time_days": lead_time_days(hours),
            "arrival_at": at + timedelta(hours=hours),
            "reliability": float(s.get("reliability") or 0),  # chỉ dùng để phá hòa, không đưa vào options
        })
    return quotes


def _cost_diff_pct(total_cost: float, cost_a: float) -> float:
    return 0.0 if cost_a <= 0 else round((total_cost - cost_a) / cost_a * 100, 1) + 0.0


def _fmt_money(value: float) -> str:
    """Giống fmt_money của app: 275000 → "275.000đ"."""
    return f"{value:,.0f}đ".replace(",", ".")


def _meets_earliest_preorder(arrival: datetime, earliest_preorder: date | None) -> bool:
    if earliest_preorder is None:
        return True
    return arrival <= datetime.combine(earliest_preorder, PREORDER_CUTOFF)


def _option(letter: str, q: dict, cost_a: float, earliest_preorder: date | None, reason: str) -> dict:
    """Một phương án theo contracts Mục 5. cost_diff_pct_vs_A của A luôn là 0."""
    arrival: datetime = q["arrival_at"]
    diff = 0.0 if letter == "A" else _cost_diff_pct(q["total_cost"], cost_a)
    return {
        "option": letter,
        "sup_id": q["sup_id"],
        "sup_name": q["sup_name"],
        "unit_price": q["unit_price"],
        "order_qty": q["order_qty"],
        "total_cost": q["total_cost"],
        "lead_time_hours": q["lead_time_hours"],
        "lead_time_days": q["lead_time_days"],
        "arrival_at": arrival.isoformat(timespec="seconds"),
        "meets_earliest_preorder": _meets_earliest_preorder(arrival, earliest_preorder),
        "cost_diff_pct_vs_A": diff + 0.0,
        "reason": reason,
    }


def choose_options(quotes: list[dict], at: datetime, earliest_preorder: date | None) -> tuple[list[dict], str | None]:
    """Chọn A/B theo đặc tả 5.4. Trả về (options, note).

    Hòa sau các quy tắc của 5.4 thì chọn reliability cao hơn, rồi theo sup_id. `at` giữ trong chữ ký
    để không đổi cách gọi; arrival_at đã được quote_suppliers tính sẵn.
    """
    if not quotes:
        return [], NOTE_MANUAL
    best_a = min(quotes, key=lambda q: (q["total_cost"], q["lead_time_hours"],
                                        -q.get("reliability", 0.0), q["sup_id"]))
    best_b = min(quotes, key=lambda q: (q["lead_time_hours"], q["total_cost"],
                                        -q.get("reliability", 0.0), q["sup_id"]))
    cost_a = best_a["total_cost"]

    if len(quotes) == 1:
        opt = _option("A", best_a, cost_a, earliest_preorder, "Chỉ có 1 NCC hợp lệ")
        return [opt], f"Chỉ có 1 NCC hợp lệ ({best_a['sup_name']}) nên chỉ có một phương án."
    if best_a["sup_id"] == best_b["sup_id"]:
        opt = _option("A", best_a, cost_a, earliest_preorder,
                      "Vừa rẻ nhất vừa nhanh nhất trong các NCC hợp lệ")
        return [opt], f"{best_a['sup_name']} vừa rẻ nhất vừa nhanh nhất nên chỉ có một phương án."
    slower_hours = best_a["lead_time_hours"] - best_b["lead_time_hours"]
    pct = _cost_diff_pct(best_b["total_cost"], cost_a)
    reason_a = f"Rẻ nhất, về chậm hơn B {slower_hours:g} giờ"
    reason_b = f"Nhanh nhất, đắt hơn A {pct:g}% ({_fmt_money(best_b['total_cost'] - cost_a)})"
    return [
        _option("A", best_a, cost_a, earliest_preorder, reason_a),
        _option("B", best_b, cost_a, earliest_preorder, reason_b),
    ], None


# ---------------------------------------------------------------------------
# Hàm chính
# ---------------------------------------------------------------------------

def compute_alert(ingredient: dict, at: datetime) -> dict | None:
    """AlertDraft cho 1 nguyên liệu, hoặc None nếu không thiếu hàng."""
    ingredient_id = ingredient["id"]
    min_stock = float(ingredient["min_stock"])
    projection = fefo_projection(ingredient_id, H, at)
    projected = projection["projected_stock"]
    if projected >= min_stock - _EPS:
        return None

    day0 = at.date()
    needs = _preorder_needs(ingredient_id, day0)
    preorder_qty = sum(float(row["need_qty"]) for row in needs)
    earliest_preorder = min((date.fromisoformat(str(row["delivery_date"])[:10]) for row in needs),
                            default=None)
    daily_qty = float(ingredient.get("daily_usage") or 0) * H
    open_pos = _open_pos(ingredient_id)

    causes, main_cause = build_causes(preorder_qty, daily_qty, projection["expired_qty"],
                                      _delayed_qty(ingredient_id), bool(open_pos))

    if open_pos:
        po = open_pos[0]  # danh sách đã sắp theo po_id
        open_po_id = po["po_id"]
        options = []
        note = (f"Đã có {open_po_id} ({po['status']}) đang mở cho nguyên liệu này, "
                f"cần xử lý PO đó trước khi đặt thêm.")
    else:
        open_po_id = None
        quotes = quote_suppliers(ingredient, at, {H: projected})
        options, note = choose_options(quotes, at, earliest_preorder)

    return {
        "ingredient_id": ingredient_id,
        "ingredient_name": ingredient["name"],
        "unit": ingredient["unit"],
        "projected_stock": _r(projected),
        "min_stock": _r(min_stock),
        "shortage_qty": _r(min_stock - projected),
        "causes": causes,
        "main_cause": main_cause,
        "open_po_id": open_po_id,
        "options": options,
        "note": note,
        "snapshot_hash": repository.compute_snapshot_hash(ingredient_id),
    }


def compute_alerts(run_date: date) -> list[dict]:
    """Danh sách AlertDraft (contracts Mục 5): chỉ nguyên liệu có projected_stock(H=3) < min_stock."""
    at = _run_time(run_date)
    alerts = []
    for ingredient in repository.get_ingredients():
        alert = compute_alert(ingredient, at)
        if alert is not None:
            alerts.append(alert)
    return alerts
