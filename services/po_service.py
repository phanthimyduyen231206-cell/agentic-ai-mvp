"""Vòng đời PO (state machine Mục 6) và guardrail trong hàm ghi (Mục 8).

Chủ file: Duyên (D05). Chữ ký hàm: docs/contracts.md Mục 9.

- PO chỉ có 3 trạng thái DRAFT, APPROVED, SENT. Từ chối / hủy = đóng PO bằng closed_reason
  (REJECTED / CANCELLED) kèm closed_by, closed_at. PO đã đóng không chuyển bước được nữa.
- Mỗi hàm ghi tự kiểm tra vai trò, trạng thái và dữ liệu. Sai thì không ghi gì, raise GuardrailError
  và ghi GUARDRAIL_BLOCKED vào audit_logs.
- Gemini không gọi được các hàm này (không có trong tool registry). app.py gọi qua services/ui_api.py.

Bảng chuyển trạng thái được phép (Mục 6):
    (chưa có) -> DRAFT          create_draft_po   cảnh báo OPEN + guardrail Mục 8
    DRAFT     -> APPROVED       approve_po        MANAGER, PO chưa đóng
    DRAFT     -> đóng REJECTED  reject_po         MANAGER
    APPROVED  -> SENT           send_po           MANAGER, tạo file PO thành công
    APPROVED  -> APPROVED       send_po thất bại  tăng send_attempts, lưu last_send_error
    APPROVED  -> đóng CANCELLED cancel_po         MANAGER
    delivery PENDING/DELAYED -> RECEIVED  mark_received  MANAGER, tạo lô mới
Cảnh báo: OPEN -> SELECTED (tạo PO) -> RESOLVED (PO SENT hoặc đóng); OPEN -> DISMISSED (bỏ qua).
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from datetime import timedelta
from pathlib import Path

from data import repository
from services import clock
from services.audit_log import write_audit_log

ROOT = Path(__file__).resolve().parents[1]
OUTBOX_DIR = ROOT / "outbox"   # test đổi sang thư mục tạm bằng monkeypatch

_EPS = 1e-6

ERROR_CODES = (
    "QTY_INVALID", "INGREDIENT_NOT_FOUND", "SUPPLIER_INVALID", "MOQ_VIOLATION", "ALERT_NOT_OPEN",
    "DUPLICATE_PO", "NOT_MANAGER", "INVALID_TRANSITION", "SNAPSHOT_CHANGED", "SEND_FAILED",
)


class GuardrailError(Exception):
    """Thao tác ghi bị chặn. `code` thuộc ERROR_CODES, `message` là câu tiếng Việt cho giao diện."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Tiện ích nội bộ
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")


def _get(user, key):
    if user is None:
        return None
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


def _actor(user) -> str:
    return str(_get(user, "username") or _get(user, "role") or "UNKNOWN")


def _require_manager(user) -> None:
    """Vai trò lấy từ phiên đăng nhập (user do app.py tạo khi đăng nhập), không từ tham số khác."""
    if _get(user, "role") != "MANAGER":
        raise GuardrailError("NOT_MANAGER", "Chỉ quản lý (MANAGER) được thực hiện thao tác này.")


def _block(user, action: str, err: GuardrailError, entity_type=None, entity_id=None, extra=None) -> None:
    """Ghi GUARDRAIL_BLOCKED bằng kết nối riêng (giao dịch chính đã rollback)."""
    detail = {"action": action, "code": err.code, "message": err.message}
    if extra:
        detail.update(extra)
    write_audit_log(_actor(user), "GUARDRAIL_BLOCKED", entity_type, entity_id, detail=detail)


def _begin(conn: sqlite3.Connection) -> None:
    """Khóa ghi ngay từ đầu để hai lần bấm cùng lúc không cùng vượt qua bước kiểm tra."""
    conn.execute("BEGIN IMMEDIATE")


def _open_po_or_fail(conn, po_id):
    po = repository.get_purchase_order(po_id, conn=conn)
    if po is None:
        raise GuardrailError("INVALID_TRANSITION", f"Không tìm thấy PO {po_id}.")
    if po["closed_reason"] is not None:
        raise GuardrailError("INVALID_TRANSITION",
                             f"PO {po_id} đã đóng ({po['closed_reason']}), không chuyển bước được nữa.")
    return po


def _require_status(po, status: str, action_text: str) -> None:
    if po["status"] != status:
        raise GuardrailError("INVALID_TRANSITION",
                             f"Chỉ {action_text} PO đang {status}; PO {po['po_id']} đang {po['status']}.")


def _next_po_id(conn) -> str:
    max_n = 0
    for (pid,) in conn.execute("SELECT po_id FROM purchase_orders"):
        head, _, tail = str(pid).rpartition("-")
        if head == "PO" and tail.isdigit():
            max_n = max(max_n, int(tail))
    return f"PO-{max_n + 1:03d}"


def _resolve_alert(conn, alert_id, now: str) -> None:
    if alert_id is None:
        return
    alert = repository.get_alert(alert_id, conn=conn)
    if alert and alert["status"] in ("OPEN", "SELECTED"):
        repository.update_alert(alert_id, {"status": "RESOLVED", "resolved_at": now}, conn=conn)


def _supplier_row(conn, sup_id, ingredient_id):
    for s in repository.get_suppliers(ingredient_id=ingredient_id, conn=conn):
        if s["sup_id"] == sup_id:
            return s
    return None


def _run_write(user, action: str, entity_type, entity_id, work):
    """Chạy `work(conn)` trong một giao dịch. GuardrailError -> rollback, ghi GUARDRAIL_BLOCKED, raise lại."""
    conn = repository.get_connection()
    try:
        _begin(conn)
        result = work(conn)
        conn.commit()
        return result
    except GuardrailError as err:
        conn.rollback()
        conn.close()
        conn = None
        _block(user, action, err, entity_type, entity_id)
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# 1. Tạo PO nháp sau khi quản lý chọn A/B
# ---------------------------------------------------------------------------
def create_draft_po(alert_id: int, option: str, qty: float, user) -> str:
    """Kiểm tra guardrail Mục 8 rồi tạo PO DRAFT; trả po_id mới (vd "PO-005").

    Thứ tự kiểm tra: quyền -> số lượng -> cảnh báo còn OPEN / PO trùng -> nguyên liệu -> NCC
    trong options -> MOQ -> snapshot_hash. Lỗi nào cũng không ghi gì vào PO / cảnh báo.
    """
    def work(conn):
        _require_manager(user)

        try:
            q = float(qty)
        except (TypeError, ValueError):
            raise GuardrailError("QTY_INVALID", "Số lượng đặt không hợp lệ.")
        if not math.isfinite(q) or q <= 0:
            raise GuardrailError("QTY_INVALID", "Số lượng đặt phải lớn hơn 0.")

        alert = repository.get_alert(alert_id, conn=conn)
        if alert is None:
            raise GuardrailError("ALERT_NOT_OPEN", f"Không tìm thấy cảnh báo #{alert_id}.")
        if alert["status"] != "OPEN":
            existing = repository.get_purchase_order_by_alert(alert_id, conn=conn)
            if existing is not None:
                raise GuardrailError("DUPLICATE_PO",
                                     f"Cảnh báo #{alert_id} đã có PO {existing['po_id']}, không tạo thêm.")
            raise GuardrailError("ALERT_NOT_OPEN",
                                 f"Cảnh báo #{alert_id} không còn chờ xử lý (đang {alert['status']}).")

        ingredient_id = alert["ingredient_id"]
        if repository.get_ingredient(ingredient_id, conn=conn) is None:
            raise GuardrailError("INGREDIENT_NOT_FOUND", f"Không có nguyên liệu {ingredient_id}.")

        open_pos = [p for p in repository.get_purchase_orders(ingredient_id=ingredient_id, conn=conn)
                    if p["closed_reason"] is None and p["status"] in ("DRAFT", "APPROVED")]
        if open_pos:
            raise GuardrailError("DUPLICATE_PO",
                                 f"Nguyên liệu này đang có PO {open_pos[0]['po_id']} chưa xử lý xong.")

        try:
            options = json.loads(alert["options_json"] or "[]")
        except (TypeError, ValueError):
            options = []
        chosen = next((o for o in options if o.get("option") == option), None)
        if option not in ("A", "B") or chosen is None:
            raise GuardrailError("SUPPLIER_INVALID", f"Phương án {option!r} không có trong cảnh báo này.")
        sup_id = chosen.get("sup_id")
        supplier = _supplier_row(conn, sup_id, ingredient_id)
        if supplier is None or int(supplier["active"]) != 1:
            raise GuardrailError("SUPPLIER_INVALID",
                                 f"NCC {sup_id} không bán nguyên liệu này hoặc đã ngừng hoạt động.")

        moq = float(supplier["moq"])
        if moq <= 0:
            raise GuardrailError("SUPPLIER_INVALID", f"NCC {sup_id} chưa có MOQ hợp lệ.")
        multiples = q / moq
        if q + _EPS < moq or abs(multiples - round(multiples)) > _EPS:
            raise GuardrailError("MOQ_VIOLATION",
                                 f"Số lượng {q:g} phải ≥ MOQ {moq:g} và là bội số của {moq:g}.")

        current_hash = repository.compute_snapshot_hash(ingredient_id, conn=conn)
        if not alert["snapshot_hash"] or alert["snapshot_hash"] != current_hash:
            raise GuardrailError("SNAPSHOT_CHANGED",
                                 "Dữ liệu kho đã thay đổi sau lần rà soát. Hãy chạy lại rà soát rồi chọn lại.")

        now = _now_iso()
        po_id = _next_po_id(conn)
        price = float(supplier["price"])
        try:
            repository.insert_purchase_order({
                "po_id": po_id, "alert_id": alert_id, "sup_id": sup_id, "ingredient_id": ingredient_id,
                "order_qty": q, "unit_price": price, "total_cost": round(q * price, 2),
                "status": "DRAFT", "created_by": _actor(user), "created_at": now,
            }, conn=conn)
        except sqlite3.IntegrityError:  # ux_po_alert chặn ở tầng DB
            raise GuardrailError("DUPLICATE_PO", f"Cảnh báo #{alert_id} đã có PO đang mở.")
        repository.update_alert(alert_id, {
            "status": "SELECTED", "selected_option": option, "selected_qty": q,
            "selected_by": _actor(user), "selected_at": now, "po_id": po_id,
        }, conn=conn)
        write_audit_log(_actor(user), "OPTION_SELECTED", "alert", alert_id, run_id=alert["run_id"],
                        detail={"option": option, "sup_id": sup_id, "qty": q}, conn=conn)
        write_audit_log(_actor(user), "PO_CREATED", "purchase_order", po_id, run_id=alert["run_id"],
                        detail={"alert_id": alert_id, "option": option, "sup_id": sup_id,
                                "ingredient_id": ingredient_id, "qty": q, "unit_price": price,
                                "total_cost": round(q * price, 2)}, conn=conn)
        return po_id

    return _run_write(user, "create_draft_po", "alert", alert_id, work)


# ---------------------------------------------------------------------------
# 2. Duyệt / từ chối / hủy
# ---------------------------------------------------------------------------
def approve_po(po_id: str, user) -> None:
    """DRAFT -> APPROVED. Chỉ MANAGER, PO chưa đóng."""
    def work(conn):
        _require_manager(user)
        po = _open_po_or_fail(conn, po_id)
        _require_status(po, "DRAFT", "duyệt")
        now = _now_iso()
        repository.update_purchase_order(po_id, {"status": "APPROVED", "approved_by": _actor(user),
                                                 "approved_at": now}, conn=conn)
        write_audit_log(_actor(user), "PO_APPROVED", "purchase_order", po_id, conn=conn)

    _run_write(user, "approve_po", "purchase_order", po_id, work)


def reject_po(po_id: str, user, reason: str = "") -> None:
    """Đóng PO DRAFT với closed_reason = REJECTED; cảnh báo -> RESOLVED."""
    def work(conn):
        _require_manager(user)
        po = _open_po_or_fail(conn, po_id)
        _require_status(po, "DRAFT", "từ chối")
        now = _now_iso()
        repository.update_purchase_order(po_id, {"closed_reason": "REJECTED", "closed_by": _actor(user),
                                                 "closed_at": now}, conn=conn)
        _resolve_alert(conn, po["alert_id"], now)
        write_audit_log(_actor(user), "PO_REJECTED", "purchase_order", po_id,
                        detail={"reason": reason or ""}, conn=conn)

    _run_write(user, "reject_po", "purchase_order", po_id, work)


def cancel_po(po_id: str, user) -> None:
    """Đóng PO APPROVED (chưa gửi) với closed_reason = CANCELLED; cảnh báo -> RESOLVED."""
    def work(conn):
        _require_manager(user)
        po = _open_po_or_fail(conn, po_id)
        _require_status(po, "APPROVED", "hủy")
        now = _now_iso()
        repository.update_purchase_order(po_id, {"closed_reason": "CANCELLED", "closed_by": _actor(user),
                                                 "closed_at": now}, conn=conn)
        _resolve_alert(conn, po["alert_id"], now)
        write_audit_log(_actor(user), "PO_CANCELLED", "purchase_order", po_id, conn=conn)

    _run_write(user, "cancel_po", "purchase_order", po_id, work)


# ---------------------------------------------------------------------------
# 3. Gửi PO (MVP: xuất file CSV vào outbox/)
# ---------------------------------------------------------------------------
def _write_po_file(po, supplier, ingredient, expected_at: str, user) -> Path:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTBOX_DIR / f"{po['po_id']}.csv"
    rows = [
        ("Mã PO", po["po_id"]),
        ("Ngày gửi", _now_iso()),
        ("Nhà cung cấp", f"{supplier['name']} ({supplier['sup_id']})"),
        ("Email NCC", supplier["contact_email"]),
        ("Nguyên liệu", f"{ingredient['name']} ({ingredient['id']})"),
        ("Đơn vị", ingredient["unit"]),
        ("Số lượng", f"{float(po['order_qty']):g}"),
        ("Đơn giá (VND)", f"{float(po['unit_price']):.0f}"),
        ("Thành tiền (VND)", f"{float(po['total_cost']):.0f}"),
        ("Dự kiến giao", expected_at),
        ("Người duyệt", po["approved_by"] or ""),
        ("Người gửi", _actor(user)),
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig để Excel đọc đúng tiếng Việt
        writer = csv.writer(f)
        writer.writerow(("Mục", "Giá trị"))
        writer.writerows(rows)
    return path


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def send_po(po_id: str, user) -> str:
    """APPROVED -> SENT khi tạo được file PO; trả đường dẫn file CSV trong outbox/.

    Thất bại (NCC thiếu contact_email, lỗi ghi file): PO vẫn APPROVED, tăng send_attempts,
    lưu last_send_error, ghi PO_SEND_FAILED, rồi raise GuardrailError("SEND_FAILED"). Gửi lại được.
    PO đã SENT thì không gửi lại (INVALID_TRANSITION).
    """
    conn = repository.get_connection()
    path = None
    try:
        _begin(conn)
        try:
            _require_manager(user)
            po = _open_po_or_fail(conn, po_id)
            _require_status(po, "APPROVED", "gửi")
        except GuardrailError as err:
            conn.rollback()
            conn.close()
            conn = None
            _block(user, "send_po", err, "purchase_order", po_id)
            raise

        supplier = _supplier_row(conn, po["sup_id"], po["ingredient_id"])
        ingredient = repository.get_ingredient(po["ingredient_id"], conn=conn)
        now_dt = clock.now()
        error = None
        if supplier is None:
            error = f"Không tìm thấy NCC {po['sup_id']} cho nguyên liệu này."
        elif not (supplier["contact_email"] or "").strip():
            error = f"NCC {supplier['name']} chưa có email liên hệ (contact_email trống)."
        else:
            expected_at = (now_dt + timedelta(hours=int(supplier["lead_time_hours"]))).isoformat(
                timespec="seconds")
            try:
                path = _write_po_file(po, supplier, ingredient, expected_at, user)
            except OSError as exc:
                error = f"Không ghi được file PO: {exc}"

        if error:
            attempts = int(po["send_attempts"] or 0) + 1
            repository.update_purchase_order(po_id, {"send_attempts": attempts, "last_send_error": error},
                                              conn=conn)
            write_audit_log(_actor(user), "PO_SEND_FAILED", "purchase_order", po_id,
                            detail={"attempt": attempts, "error": error}, conn=conn)
            conn.commit()
            raise GuardrailError("SEND_FAILED", error)

        now = now_dt.isoformat(timespec="seconds")
        repository.update_purchase_order(po_id, {
            "status": "SENT", "sent_by": _actor(user), "sent_at": now,
            "send_attempts": int(po["send_attempts"] or 0) + 1, "last_send_error": None,
            "delivery_status": "PENDING", "expected_delivery_date": expected_at,
        }, conn=conn)
        _resolve_alert(conn, po["alert_id"], now)
        write_audit_log(_actor(user), "PO_SENT", "purchase_order", po_id,
                        detail={"file": _display_path(path), "to": supplier["contact_email"],
                                "expected_delivery_date": expected_at}, conn=conn)
        conn.commit()
        return _display_path(path)
    except GuardrailError:
        raise
    except Exception:
        if conn is not None:
            conn.rollback()
        if path is not None and path.exists():
            path.unlink()   # DB lỗi thì không để lại file PO "ma"
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# 4. Nhận hàng
# ---------------------------------------------------------------------------
def mark_received(po_id: str, qty: float, user, expiry_date=None) -> str:
    """Xác nhận đã nhận đủ hàng của PO SENT (delivery PENDING/DELAYED) -> RECEIVED; trả batch_id mới.

    MVP nhận một lần đủ số lượng đặt (Mục 5.5). Lô mới do inventory_service.receive_batch (P04) tạo;
    receive_batch ghi BATCH_RECEIVED, hàm này ghi DELIVERY_RECEIVED.

    Chống nhận trùng: trong một giao dịch BEGIN IMMEDIATE, kiểm tra rồi đánh dấu PO RECEIVED ngay
    (lần bấm thứ hai sẽ thấy RECEIVED và bị chặn). Sau đó mới tạo lô; tạo lô lỗi thì trả PO về như cũ.
    """
    from services import inventory_service   # import lúc gọi để không phụ thuộc vòng

    def claim(conn):
        _require_manager(user)
        po = _open_po_or_fail(conn, po_id)
        _require_status(po, "SENT", "nhận hàng cho")
        if po["delivery_status"] not in ("PENDING", "DELAYED"):
            raise GuardrailError("INVALID_TRANSITION",
                                 f"PO {po_id} không ở trạng thái chờ giao (đang {po['delivery_status']}).")
        try:
            q = float(qty)
        except (TypeError, ValueError):
            raise GuardrailError("QTY_INVALID", "Số lượng nhận không hợp lệ.")
        remaining = float(po["order_qty"]) - float(po["received_qty"] or 0)
        if not math.isfinite(q) or q <= 0 or abs(q - remaining) > _EPS:
            raise GuardrailError("QTY_INVALID",
                                 f"MVP chỉ nhận một lần đủ số lượng: cần nhận đúng {remaining:g}.")
        repository.update_purchase_order(po_id, {
            "received_qty": float(po["received_qty"] or 0) + q, "delivery_status": "RECEIVED",
        }, conn=conn)
        return po, q

    po, q = _run_write(user, "mark_received", "purchase_order", po_id, claim)

    try:
        batch_id = inventory_service.receive_batch(po_id, q, expiry_date)
    except Exception:
        repository.update_purchase_order(po_id, {   # tạo lô lỗi: PO trở lại chờ giao, nhận lại được
            "received_qty": po["received_qty"], "delivery_status": po["delivery_status"],
        })
        raise

    write_audit_log(_actor(user), "DELIVERY_RECEIVED", "purchase_order", po_id,
                    detail={"qty": q, "batch_id": batch_id, "received_at": _now_iso()})
    return batch_id


# ---------------------------------------------------------------------------
# 5. Bỏ qua cảnh báo
# ---------------------------------------------------------------------------
def dismiss_alert(alert_id: int, user) -> None:
    """Quản lý bấm "Bỏ qua": cảnh báo OPEN -> DISMISSED, ghi resolved_at, log ALERT_DISMISSED."""
    def work(conn):
        _require_manager(user)
        alert = repository.get_alert(alert_id, conn=conn)
        if alert is None or alert["status"] != "OPEN":
            status = alert["status"] if alert else "không tồn tại"
            raise GuardrailError("ALERT_NOT_OPEN", f"Cảnh báo #{alert_id} không còn chờ xử lý ({status}).")
        repository.update_alert(alert_id, {"status": "DISMISSED", "resolved_at": _now_iso()}, conn=conn)
        write_audit_log(_actor(user), "ALERT_DISMISSED", "alert", alert_id, run_id=alert["run_id"], conn=conn)

    _run_write(user, "dismiss_alert", "alert", alert_id, work)
