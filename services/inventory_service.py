"""issue_stock_fefo(), receive_batch().

Chủ file: Phương Anh (P04). Chữ ký hàm: xem docs/contracts.md Mục 8.

- issue_stock_fefo: xuất kho theo FEFO (lô hết hạn sớm nhất trước), không dùng lô đã hết hạn,
  chặn xuất quá tồn còn hạn. Chỉ quản lý được xuất (ma trận quyền Mục 8). Ghi STOCK_ISSUED.
- receive_batch: tạo lô mới khi po_service.mark_received xác nhận nhận hàng. Ghi BATCH_RECEIVED.
  Không tự đổi trạng thái PO, không kiểm delivery_status (việc đó của mark_received).

Danh sách lô và thứ tự FEFO lấy DUY NHẤT từ repository.get_batches_fefo() (ranh giới Mục 14),
file này không tự viết truy vấn sắp lô.
"""
import math
from datetime import date, datetime, time, timedelta

from data import repository
from services import clock
from services.audit_log import write_audit_log
from services.po_service import GuardrailError

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Tiện ích nội bộ
# ---------------------------------------------------------------------------
def _get(user, key):
    if user is None:
        return None
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


def _actor(user) -> str:
    return str(_get(user, "username") or _get(user, "role") or "UNKNOWN")


def _to_qty(qty) -> float:
    """Đổi số lượng sang float; không phải số, vô hạn hoặc <= 0 thì chặn."""
    try:
        q = float(qty)
    except (TypeError, ValueError):
        raise GuardrailError("QTY_INVALID", "Số lượng không hợp lệ.")
    if not math.isfinite(q) or q <= 0:
        raise GuardrailError("QTY_INVALID", "Số lượng phải lớn hơn 0.")
    return q


def _clean(x: float) -> float:
    """Làm tròn để tránh sai số kiểu 0.30000000000000004 khi trừ số thập phân."""
    return round(x, 6) + 0.0


def _expiry_text(expiry_date, received_at: datetime, shelf_life_days) -> str:
    """Hạn dùng của lô mới, dạng ISO có chữ T.

    - Không nhập hạn: ngày nhận + shelf_life_days, lúc 23:59 (giống dữ liệu mẫu P02).
    - Nhập ngày (date hoặc 'YYYY-MM-DD'): ngày đó lúc 23:59.
    - Nhập ngày giờ: giữ nguyên.
    """
    end_of_day = time(23, 59)
    if expiry_date is None:
        days = int(shelf_life_days or 0)
        value = datetime.combine(received_at.date() + timedelta(days=days), end_of_day)
    elif isinstance(expiry_date, datetime):
        value = expiry_date.replace(tzinfo=None, microsecond=0)
    elif isinstance(expiry_date, date):
        value = datetime.combine(expiry_date, end_of_day)
    else:
        text = str(expiry_date).strip()
        try:
            value = (datetime.combine(date.fromisoformat(text), end_of_day) if len(text) == 10
                     else datetime.fromisoformat(text).replace(tzinfo=None, microsecond=0))
        except ValueError:
            raise GuardrailError("QTY_INVALID", f"Hạn dùng không hợp lệ: {expiry_date!r}.")
    return value.isoformat(timespec="seconds")


def _blocked(user, action: str, err: GuardrailError, entity_type, entity_id, extra=None) -> None:
    """Ghi GUARDRAIL_BLOCKED bằng kết nối riêng (giao dịch chính đã rollback)."""
    detail = {"action": action, "code": err.code, "message": err.message}
    if extra:
        detail.update(extra)
    write_audit_log(_actor(user), "GUARDRAIL_BLOCKED", entity_type, entity_id, detail=detail)


# ---------------------------------------------------------------------------
# 1. Xuất kho theo FEFO
# ---------------------------------------------------------------------------
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]:
    """Xuất `qty` của một nguyên liệu, trừ lần lượt từ lô hết hạn sớm nhất.

    Trả [{"batch_id", "taken"}] theo thứ tự đã lấy. Bị chặn (không phải quản lý, số lượng sai,
    không có nguyên liệu, xuất quá tồn còn hạn) thì không trừ gì, ghi GUARDRAIL_BLOCKED và
    raise GuardrailError.
    """
    conn = repository.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")   # khóa ghi: hai lần bấm cùng lúc không cùng trừ một lô
        if _get(user, "role") != "MANAGER":
            raise GuardrailError("NOT_MANAGER", "Chỉ quản lý (MANAGER) được xuất kho.")
        q = _to_qty(qty)
        if repository.get_ingredient(ingredient_id, conn=conn) is None:
            raise GuardrailError("INGREDIENT_NOT_FOUND", f"Không tìm thấy nguyên liệu {ingredient_id}.")

        now = clock.now()
        batches = repository.get_batches_fefo(ingredient_id, now, conn=conn)
        available = _clean(sum(float(b["qty"]) for b in batches))
        if q > available + _EPS:
            raise GuardrailError(
                "QTY_INVALID",
                f"Không đủ hàng còn hạn: cần xuất {q:g}, chỉ còn {available:g} (không tính lô đã hết hạn).")

        taken_list = []
        need = q
        for b in batches:
            if need <= _EPS:
                break
            take = _clean(min(float(b["qty"]), need))
            repository.update_batch_qty(b["batch_id"], _clean(float(b["qty"]) - take), conn=conn)
            taken_list.append({"batch_id": b["batch_id"], "taken": take})
            need = _clean(need - take)

        write_audit_log(_actor(user), "STOCK_ISSUED", "ingredient", ingredient_id, conn=conn,
                        detail={"qty": q, "batches": taken_list, "issued_at": now.isoformat(timespec="seconds")})
        conn.commit()
        return taken_list
    except GuardrailError as err:
        conn.rollback()
        conn.close()
        conn = None
        _blocked(user, "issue_stock_fefo", err, "ingredient", ingredient_id,
                 {"qty": qty if isinstance(qty, (int, float)) else str(qty)})
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# 2. Nhập lô khi nhận hàng
# ---------------------------------------------------------------------------
def receive_batch(po_id: str, qty: float, expiry_date=None) -> str:
    """Tạo lô mới cho hàng của PO `po_id`; trả batch_id mới (vd "BO-L2").

    Chỉ do po_service.mark_received gọi (đã kiểm quyền và trạng thái PO trước đó).
    Hạn mặc định = ngày nhận + shelf_life_days nếu không nhập hạn in trên bao bì.
    """
    q = _to_qty(qty)
    conn = repository.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        po = repository.get_purchase_order(po_id, conn=conn)
        if po is None:
            raise GuardrailError("INVALID_TRANSITION", f"Không tìm thấy PO {po_id}.")
        ingredient = repository.get_ingredient(po["ingredient_id"], conn=conn)
        if ingredient is None:
            raise GuardrailError("INGREDIENT_NOT_FOUND", f"Không tìm thấy nguyên liệu {po['ingredient_id']}.")

        now = clock.now()
        expiry = _expiry_text(expiry_date, now, ingredient.get("shelf_life_days"))
        batch_id = repository.insert_batch(po["ingredient_id"], q, expiry, now, po_id=po_id, conn=conn)

        write_audit_log("SYSTEM", "BATCH_RECEIVED", "batch", batch_id, conn=conn,
                        detail={"po_id": po_id, "ingredient_id": po["ingredient_id"], "qty": q,
                                "expiry_date": expiry, "received_at": now.isoformat(timespec="seconds")})
        conn.commit()
        return batch_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
