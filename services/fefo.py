"""fefo_projection(): phân bổ nhu cầu theo lô FEFO, tính expired_qty, unmet_qty.

Chủ file: Quỳnh Anh (Q01). Chữ ký hàm: xem docs/contracts.md (Mục 4), thuật toán: đặc tả Mục 5.2.

Chỉ đọc dữ liệu, không ghi DB, không gọi LLM. Mô phỏng theo NGÀY (đặc tả 5.5):
nhu cầu trong ngày coi như dùng trước khi lô hết hạn vào cuối ngày.

Mỗi ngày t = 0 … days − 1 (ngày 0 = ngày của `at`):
1. Đầu ngày: thêm lô ảo cho PO SENT + delivery PENDING có expected_delivery_date = ngày t
   (hạn = ngày về + shelf_life_days). PO có ngày dự kiến trước ngày 0 (quá hạn, chưa được
   audit đổi sang DELAYED) hoặc sau kỳ tính thì bỏ qua.
2. Nhu cầu = daily_usage + need_qty của đơn đặt trước có delivery_date = ngày t.
3. Lấy nhu cầu lần lượt từ lô hạn sớm nhất (cùng hạn thì lô nhập trước); lô dùng được
   nếu ngày hết hạn ≥ ngày t.
4. Cuối ngày: phần còn lại của lô hết hạn trong ngày t cộng vào expired_qty.
5. Phần nhu cầu không còn lô để lấy cộng vào unmet_qty.
projected_stock = remaining_qty − unmet_qty.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from data import repository
from services import clock

_EPS = 1e-9


def _r(x: float) -> float:
    """Làm tròn để tránh sai số float kiểu 0.6000000000000001; -0.0 → 0.0."""
    return round(x, 6) + 0.0


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time())
    return datetime.fromisoformat(str(value).strip())


def _incoming_lots(ingredient_id, day0: date, last_day: date, shelf_life_days: int) -> list[dict]:
    """Lô ảo cho hàng của PO SENT, delivery PENDING, dự kiến về trong [day0, last_day]."""
    lots = []
    for po in repository.get_purchase_orders(status="SENT", ingredient_id=ingredient_id):
        if po.get("delivery_status") != "PENDING" or po.get("closed_reason"):
            continue
        if not po.get("expected_delivery_date"):
            continue
        arrival = date.fromisoformat(str(po["expected_delivery_date"])[:10])
        if arrival < day0 or arrival > last_day:
            continue  # quá hạn (chờ audit đổi DELAYED) hoặc ngoài kỳ tính
        qty = float(po["order_qty"] or 0) - float(po.get("received_qty") or 0)
        if qty <= _EPS:
            continue
        lots.append({
            "batch_id": f"{po['po_id']}-INCOMING",
            "po_id": po["po_id"],
            "qty": qty,
            "received_at": datetime.combine(arrival, time()),
            "expiry_date": datetime.combine(arrival + timedelta(days=int(shelf_life_days)), time()),
            "arrival_day": arrival,
        })
    return lots


def fefo_projection(ingredient_id, days: int, at: datetime | None = None) -> dict:
    """Mô phỏng FEFO `days` ngày từ ngày của `at` (mặc định clock.now()). Xem docstring đầu file."""
    if days is None or int(days) != days or days < 0:
        raise ValueError(f"days phải là số nguyên ≥ 0, nhận {days!r}")
    days = int(days)
    ingredient = repository.get_ingredient(ingredient_id)
    if ingredient is None:
        raise ValueError(f"Không có nguyên liệu {ingredient_id!r}")

    at = clock.now() if at is None else _to_datetime(at)
    day0 = at.date()
    last_day = day0 + timedelta(days=max(days - 1, 0))
    daily_usage = float(ingredient.get("daily_usage") or 0)

    # Lô còn hạn tại `at`, đã sắp FEFO (hàm dùng chung của repository).
    lots = [{
        "batch_id": b["batch_id"],
        "po_id": b.get("po_id"),
        "qty": float(b["qty"]),
        "received_at": _to_datetime(b["received_at"]),
        "expiry_date": _to_datetime(b["expiry_date"]),
        "arrival_day": None,
    } for b in repository.get_batches_fefo(ingredient_id, at)]

    incoming = _incoming_lots(ingredient_id, day0, last_day, ingredient["shelf_life_days"]) if days else []

    preorder_by_day: dict[date, float] = {}
    if days:
        for row in repository.get_pre_order_needs(ingredient_id, day0, last_day):
            d = date.fromisoformat(str(row["delivery_date"])[:10])
            preorder_by_day[d] = preorder_by_day.get(d, 0.0) + float(row["need_qty"] or 0)

    plan, expired = [], []
    expired_qty = unmet_qty = 0.0

    for t in range(days):
        day = day0 + timedelta(days=t)
        day_text = day.isoformat()

        # 1. Hàng về đầu ngày, xếp lại theo đúng thứ tự FEFO.
        arrived = [lot for lot in incoming if lot["arrival_day"] == day]
        if arrived:
            lots.extend(arrived)
            lots.sort(key=lambda b: (b["expiry_date"], b["received_at"], b["batch_id"]))

        # 2–3. Nhu cầu trong ngày, lấy từ lô hạn sớm nhất còn dùng được.
        need = daily_usage + preorder_by_day.get(day, 0.0)
        for lot in lots:
            if need <= _EPS:
                break
            if lot["qty"] <= _EPS or lot["expiry_date"].date() < day:
                continue
            take = min(lot["qty"], need)
            lot["qty"] -= take
            need -= take
            plan.append({"day": day_text, "batch_id": lot["batch_id"], "take_qty": _r(take)})

        # 5. Phần không lấy được.
        if need > _EPS:
            unmet_qty += need

        # 4. Cuối ngày: lô hết hạn trong ngày t.
        for lot in lots:
            if lot["expiry_date"].date() == day and lot["qty"] > _EPS:
                expired.append({"day": day_text, "batch_id": lot["batch_id"], "qty": _r(lot["qty"])})
                expired_qty += lot["qty"]
                lot["qty"] = 0.0

    remaining_qty = sum(lot["qty"] for lot in lots if lot["qty"] > _EPS)
    return {
        "remaining_qty": _r(remaining_qty),
        "expired_qty": _r(expired_qty),
        "unmet_qty": _r(unmet_qty),
        "projected_stock": _r(remaining_qty - unmet_qty),
        "plan": plan,
        "expired": expired,
    }
