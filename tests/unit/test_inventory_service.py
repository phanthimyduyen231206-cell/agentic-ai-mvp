"""tests/unit/test_inventory_service.py - Unit test cho services/inventory_service.py (P04).

Chạy:  pytest tests/unit/test_inventory_service.py -v
DB mẫu tạo trong thư mục tạm, giờ demo 20/11/2026 08:00 (không đụng data/bakery.db thật).
"""
import json
from datetime import date, datetime

import pytest

from data import repository
from data.init_db import init_db
from services import inventory_service as inv
from services.po_service import GuardrailError

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "Phương Anh", "role": "MANAGER"}
STAFF = {"username": "Nhân viên", "role": "STAFF"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "inv.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def qty_of(batch_id):
    return repository.get_batch(batch_id)["qty"]


def logs(event_type):
    return repository.get_audit_logs(event_type=event_type)


# ---------------- issue_stock_fefo: xuất đúng FEFO ----------------

def test_issue_takes_earliest_expiry_first(db):
    taken = inv.issue_stock_fefo("TRUNG", 40, MANAGER)
    assert taken == [{"batch_id": "TRUNG-L1", "taken": 40.0}]
    assert qty_of("TRUNG-L1") == 20
    assert qty_of("TRUNG-L2") == 90


def test_issue_spans_two_batches(db):
    taken = inv.issue_stock_fefo("TRUNG", 80, MANAGER)
    assert taken == [{"batch_id": "TRUNG-L1", "taken": 60.0}, {"batch_id": "TRUNG-L2", "taken": 20.0}]
    assert qty_of("TRUNG-L1") == 0
    assert qty_of("TRUNG-L2") == 70


def test_issue_exactly_all_available(db):
    inv.issue_stock_fefo("TRUNG", 150, MANAGER)
    assert qty_of("TRUNG-L1") == 0 and qty_of("TRUNG-L2") == 0


def test_issue_decimal_qty_no_float_noise(db):
    inv.issue_stock_fefo("KEM_TUOI", 0.3, MANAGER)
    assert qty_of("KEM-L1") == 6.7


def test_issue_skips_expired_batch(db, monkeypatch):
    # 21:00 tối 20/11: lô KEM-L1 (hết hạn 20:00) đã hết hạn, chỉ còn KEM-L2 3 lít
    monkeypatch.setenv("DEMO_DATE", "2026-11-20T21:00")
    taken = inv.issue_stock_fefo("KEM_TUOI", 2, MANAGER)
    assert taken == [{"batch_id": "KEM-L2", "taken": 2.0}]
    assert qty_of("KEM-L1") == 7          # lô hết hạn không bị đụng
    assert qty_of("KEM-L2") == 1


def test_issue_writes_stock_issued_log(db):
    inv.issue_stock_fefo("TRUNG", 80, MANAGER)
    rows = logs("STOCK_ISSUED")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == "TRUNG"
    assert rows[0]["actor"] == "Phương Anh"
    detail = json.loads(rows[0]["detail_json"])
    assert detail["qty"] == 80
    assert [b["batch_id"] for b in detail["batches"]] == ["TRUNG-L1", "TRUNG-L2"]


# ---------------- issue_stock_fefo: bị chặn ----------------

def _assert_blocked(code, ingredient_id, qty, user=MANAGER):
    before = {b["batch_id"]: b["qty"] for b in repository.get_batches(ingredient_id)} if \
        repository.get_ingredient(ingredient_id) else {}
    with pytest.raises(GuardrailError) as e:
        inv.issue_stock_fefo(ingredient_id, qty, user)
    assert e.value.code == code
    after = {b["batch_id"]: b["qty"] for b in repository.get_batches(ingredient_id)} if before else {}
    assert after == before                      # không trừ gì
    assert logs("STOCK_ISSUED") == []
    assert logs("GUARDRAIL_BLOCKED")[0]["entity_id"] == ingredient_id


def test_issue_more_than_available_blocked(db):
    _assert_blocked("QTY_INVALID", "TRUNG", 151)


def test_issue_expired_stock_not_counted(db, monkeypatch):
    # 21:00 tối 20/11: sổ sách Kem tươi 10 lít nhưng chỉ 3 lít còn hạn -> xuất 4 lít bị chặn
    monkeypatch.setenv("DEMO_DATE", "2026-11-20T21:00")
    _assert_blocked("QTY_INVALID", "KEM_TUOI", 4)


@pytest.mark.parametrize("bad", [0, -5, "abc", None, float("nan"), float("inf")])
def test_issue_invalid_qty_blocked(db, bad):
    _assert_blocked("QTY_INVALID", "TRUNG", bad)


def test_issue_unknown_ingredient_blocked(db):
    _assert_blocked("INGREDIENT_NOT_FOUND", "KHONG_CO", 1)


def test_issue_staff_blocked(db):
    _assert_blocked("NOT_MANAGER", "TRUNG", 10, user=STAFF)


# ---------------- receive_batch ----------------

def test_receive_batch_default_expiry_from_shelf_life(db):
    shelf = repository.get_ingredient("BO_LAT")["shelf_life_days"]
    batch_id = inv.receive_batch("PO-004", 20, None)
    b = repository.get_batch(batch_id)
    assert b["ingredient_id"] == "BO_LAT"
    assert b["qty"] == 20
    assert b["po_id"] == "PO-004"
    assert b["received_at"] == "2026-11-20T08:00:00"
    expected = datetime.combine(date(2026, 11, 20), datetime.min.time()).toordinal() + shelf
    assert b["expiry_date"] == f"{date.fromordinal(expected).isoformat()}T23:59:00"


def test_receive_batch_with_printed_expiry(db):
    batch_id = inv.receive_batch("PO-004", 20, date(2026, 12, 31))
    assert repository.get_batch(batch_id)["expiry_date"] == "2026-12-31T23:59:00"
    batch_id2 = inv.receive_batch("PO-004", 1, datetime(2027, 1, 5, 18, 30))
    assert repository.get_batch(batch_id2)["expiry_date"] == "2027-01-05T18:30:00"


def test_receive_batch_new_id_follows_pattern(db):
    batch_id = inv.receive_batch("PO-004", 20)
    assert batch_id == "BO-L2"          # Bơ lạt đang có BO-L1


def test_receive_batch_writes_log_and_does_not_touch_po(db):
    po_before = repository.get_purchase_order("PO-004")
    batch_id = inv.receive_batch("PO-004", 20)
    rows = logs("BATCH_RECEIVED")
    assert len(rows) == 1 and rows[0]["entity_id"] == batch_id
    assert json.loads(rows[0]["detail_json"])["po_id"] == "PO-004"
    assert repository.get_purchase_order("PO-004") == po_before   # không đổi trạng thái PO


def test_new_batch_usable_by_fefo(db):
    batch_id = inv.receive_batch("PO-004", 20)
    ids = [b["batch_id"] for b in repository.get_batches_fefo("BO_LAT", NOW)]
    assert batch_id in ids


def test_receive_batch_unknown_po(db):
    with pytest.raises(GuardrailError) as e:
        inv.receive_batch("PO-999", 5)
    assert e.value.code == "INVALID_TRANSITION"


@pytest.mark.parametrize("bad", [0, -1, "x"])
def test_receive_batch_invalid_qty(db, bad):
    with pytest.raises(GuardrailError) as e:
        inv.receive_batch("PO-004", bad)
    assert e.value.code == "QTY_INVALID"
    assert logs("BATCH_RECEIVED") == []


# ---------------- Nối với po_service.mark_received (D05) ----------------

def test_mark_received_creates_batch_via_receive_batch(db):
    from services import po_service
    batch_id = po_service.mark_received("PO-004", 20, MANAGER)
    assert repository.get_batch(batch_id)["qty"] == 20
    assert repository.get_purchase_order("PO-004")["delivery_status"] == "RECEIVED"
    assert len(logs("BATCH_RECEIVED")) == 1
    assert len(logs("DELIVERY_RECEIVED")) == 1


# ---------------- receive_batch(conn=...): nhận hàng một giao dịch (PA1) ----------------

def test_receive_batch_with_conn_rollback_leaves_nothing(db):
    """Người gọi truyền conn rồi rollback: không còn lô mới, không còn log BATCH_RECEIVED."""
    before = [b["batch_id"] for b in repository.get_batches("BO_LAT")]
    conn = repository.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        batch_id = inv.receive_batch("PO-004", 20, conn=conn)
        # trong cùng giao dịch thì thấy lô mới
        assert repository.get_batch(batch_id, conn=conn)["qty"] == 20
        conn.rollback()
    finally:
        conn.close()
    assert [b["batch_id"] for b in repository.get_batches("BO_LAT")] == before
    assert logs("BATCH_RECEIVED") == []


def test_receive_batch_with_conn_does_not_commit_or_close(db):
    """Có conn thì hàm không tự commit, không đóng kết nối; người gọi commit mới lưu."""
    conn = repository.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        batch_id = inv.receive_batch("PO-004", 20, conn=conn)
        assert conn.in_transaction                        # chưa commit
        assert repository.get_batch(batch_id) is None     # kết nối khác chưa thấy
        conn.commit()                                     # conn vẫn dùng được (chưa bị đóng)
    finally:
        conn.close()
    assert repository.get_batch(batch_id)["qty"] == 20
    assert len(logs("BATCH_RECEIVED")) == 1


def test_receive_batch_with_conn_error_left_to_caller(db):
    """Lỗi bên trong: hàm raise, không tự rollback; người gọi rollback thì sạch."""
    conn = repository.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        repository.update_purchase_order("PO-004", {"delivery_status": "RECEIVED"}, conn=conn)
        with pytest.raises(GuardrailError):
            inv.receive_batch("PO-999", 5, conn=conn)
        assert conn.in_transaction                        # việc của người gọi vẫn còn đó
        conn.rollback()
    finally:
        conn.close()
    assert repository.get_purchase_order("PO-004")["delivery_status"] != "RECEIVED"
