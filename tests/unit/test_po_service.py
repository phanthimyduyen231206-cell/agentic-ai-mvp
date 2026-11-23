"""Unit test services/po_service.py (D05): state machine Mục 6 và guardrail Mục 8."""
import csv

import pytest

from data import repository
from services import po_service
from services.po_service import GuardrailError
from tests.po_helpers import (  # noqa: F401  (db là fixture)
    BO_LAT_OPTIONS, BOT_MI_OPTIONS, MANAGER, STAFF, db, logs, make_alert, po_count,
)


def _blocked(code, fn, *args):
    with pytest.raises(GuardrailError) as exc:
        fn(*args)
    assert exc.value.code == code
    return exc.value


def _draft(db_alert=None):
    alert_id = db_alert or make_alert("BO_LAT", BO_LAT_OPTIONS)
    return alert_id, po_service.create_draft_po(alert_id, "A", 15, MANAGER)


def _approved():
    alert_id, po_id = _draft()
    po_service.approve_po(po_id, MANAGER)
    return alert_id, po_id


# ---------------------------------------------------------------- create_draft_po
def test_create_draft_po_ghi_dung_po_va_canh_bao(db):
    alert_id, po_id = _draft()
    assert po_id == "PO-005"   # seed đã có PO-001…PO-004
    po = repository.get_purchase_order(po_id)
    assert (po["status"], po["sup_id"], po["order_qty"], po["unit_price"], po["total_cost"]) == \
        ("DRAFT", "NCC_CHINH", 15, 250000, 3750000)
    assert po["alert_id"] == alert_id and po["closed_reason"] is None and po["delivery_status"] is None
    alert = repository.get_alert(alert_id)
    assert (alert["status"], alert["selected_option"], alert["selected_qty"], alert["po_id"]) == \
        ("SELECTED", "A", 15, po_id)
    assert len(logs("PO_CREATED")) == 1 and len(logs("OPTION_SELECTED")) == 1


@pytest.mark.parametrize("qty", [0, -5, "abc", float("nan")])
def test_qty_khong_hop_le(db, qty):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    _blocked("QTY_INVALID", po_service.create_draft_po, alert_id, "A", qty, MANAGER)
    assert po_count() == 4


@pytest.mark.parametrize("qty", [13, 3, 16])
def test_moq_phai_la_boi_so_va_khong_nho_hon(db, qty):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)   # NCC Chính: MOQ 5
    _blocked("MOQ_VIOLATION", po_service.create_draft_po, alert_id, "A", qty, MANAGER)


def test_moq_cho_phep_boi_so_lon_hon(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    assert po_service.create_draft_po(alert_id, "A", 20, MANAGER) == "PO-005"


@pytest.mark.parametrize("option, expected", [("A", 15), ("B", 14)])
def test_qty_none_lay_so_luong_de_xuat_cua_phuong_an(db, option, expected):
    """DU1: không truyền số lượng thì dùng order_qty của phương án đã chọn."""
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    po_id = po_service.create_draft_po(alert_id, option, None, MANAGER)
    assert repository.get_purchase_order(po_id)["order_qty"] == expected


def test_qty_none_van_kiem_moq(db):
    sai_moq = [dict(BO_LAT_OPTIONS[0], order_qty=13.0)]   # NCC Chính MOQ 5, 13 không phải bội số
    alert_id = make_alert("BO_LAT", sai_moq)
    _blocked("MOQ_VIOLATION", po_service.create_draft_po, alert_id, "A", None, MANAGER)


def test_phuong_an_khong_co_trong_canh_bao(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS[:1])   # chỉ có A
    _blocked("SUPPLIER_INVALID", po_service.create_draft_po, alert_id, "B", 14, MANAGER)
    _blocked("SUPPLIER_INVALID", po_service.create_draft_po, alert_id, "C", 15, MANAGER)


def test_ncc_khong_ban_nguyen_lieu_hoac_ngung_hoat_dong(db):
    wrong = [dict(BO_LAT_OPTIONS[0], sup_id="NCC_ANH_DUONG")]   # Ánh Dương không bán Bơ lạt
    alert_id = make_alert("BO_LAT", wrong)
    _blocked("SUPPLIER_INVALID", po_service.create_draft_po, alert_id, "A", 15, MANAGER)

    conn = repository.get_connection()
    conn.execute("UPDATE suppliers SET active = 0 WHERE sup_id = 'NCC_CHINH' AND ingredient_id = 'BO_LAT'")
    conn.commit()
    conn.close()
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    _blocked("SUPPLIER_INVALID", po_service.create_draft_po, alert_id, "A", 15, MANAGER)


def test_canh_bao_khong_ton_tai_hoac_khong_open(db):
    _blocked("ALERT_NOT_OPEN", po_service.create_draft_po, 999, "A", 15, MANAGER)
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS, status="DISMISSED")
    _blocked("ALERT_NOT_OPEN", po_service.create_draft_po, alert_id, "A", 15, MANAGER)


def test_nguyen_lieu_da_co_po_dang_mo(db):
    _draft()
    other = make_alert("BO_LAT", BO_LAT_OPTIONS)   # cảnh báo khác, cùng nguyên liệu
    _blocked("DUPLICATE_PO", po_service.create_draft_po, other, "B", 14, MANAGER)
    assert po_count() == 5


def test_staff_khong_tao_duoc_va_co_log(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    _blocked("NOT_MANAGER", po_service.create_draft_po, alert_id, "A", 15, STAFF)
    _blocked("NOT_MANAGER", po_service.create_draft_po, alert_id, "A", 15, {"role": "manager"})
    _blocked("NOT_MANAGER", po_service.create_draft_po, alert_id, "A", 15, None)
    assert po_count() == 4
    assert repository.get_alert(alert_id)["status"] == "OPEN"
    blocked = logs("GUARDRAIL_BLOCKED")
    assert len(blocked) == 3 and '"NOT_MANAGER"' in blocked[0]["detail_json"]


def test_thieu_snapshot_hash_thi_chan(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    repository.update_alert(alert_id, {"snapshot_hash": None})
    _blocked("SNAPSHOT_CHANGED", po_service.create_draft_po, alert_id, "A", 15, MANAGER)


# ---------------------------------------------------------------- duyệt, từ chối, hủy
def test_approve_po(db):
    _, po_id = _approved()
    po = repository.get_purchase_order(po_id)
    assert (po["status"], po["approved_by"]) == ("APPROVED", "Duyên") and po["approved_at"]
    assert len(logs("PO_APPROVED")) == 1


def test_cancel_po_dong_po_va_resolve_canh_bao(db):
    alert_id, po_id = _approved()
    po_service.cancel_po(po_id, MANAGER)
    po = repository.get_purchase_order(po_id)
    assert (po["status"], po["closed_reason"], po["closed_by"]) == ("APPROVED", "CANCELLED", "Duyên")
    alert = repository.get_alert(alert_id)
    assert alert["status"] == "RESOLVED" and alert["resolved_at"]
    assert len(logs("PO_CANCELLED")) == 1


def test_staff_khong_duyet_tu_choi_huy_gui_duoc(db):
    _, po_id = _draft()
    for fn in (po_service.approve_po, po_service.reject_po, po_service.cancel_po, po_service.send_po):
        _blocked("NOT_MANAGER", fn, po_id, STAFF)
    assert repository.get_purchase_order(po_id)["status"] == "DRAFT"


# Mọi chuyển bước KHÔNG có trong bảng Mục 6 đều bị chặn
@pytest.mark.parametrize("state, action", [
    ("DRAFT", "cancel"), ("DRAFT", "send"), ("DRAFT", "receive"),
    ("APPROVED", "approve"), ("APPROVED", "reject"), ("APPROVED", "receive"),
    ("SENT", "approve"), ("SENT", "reject"), ("SENT", "cancel"), ("SENT", "send"),
    ("REJECTED", "approve"), ("REJECTED", "reject"), ("REJECTED", "cancel"), ("REJECTED", "send"),
    ("CANCELLED", "approve"), ("CANCELLED", "cancel"), ("CANCELLED", "send"),
])
def test_chuyen_buoc_ngoai_bang_bi_chan(db, state, action):
    _, po_id = _draft()
    if state in ("APPROVED", "SENT", "CANCELLED"):
        po_service.approve_po(po_id, MANAGER)
    if state == "SENT":
        po_service.send_po(po_id, MANAGER)
    if state == "REJECTED":
        po_service.reject_po(po_id, MANAGER)
    if state == "CANCELLED":
        po_service.cancel_po(po_id, MANAGER)
    before = repository.get_purchase_order(po_id)
    calls = {
        "approve": lambda: po_service.approve_po(po_id, MANAGER),
        "reject": lambda: po_service.reject_po(po_id, MANAGER),
        "cancel": lambda: po_service.cancel_po(po_id, MANAGER),
        "send": lambda: po_service.send_po(po_id, MANAGER),
        "receive": lambda: po_service.mark_received(po_id, 15, MANAGER),
    }
    _blocked("INVALID_TRANSITION", calls[action])
    assert repository.get_purchase_order(po_id) == before


def test_po_khong_ton_tai(db):
    _blocked("INVALID_TRANSITION", po_service.approve_po, "PO-999", MANAGER)


# ---------------------------------------------------------------- gửi PO
def test_send_po_xuat_csv_va_chuyen_sent(db, tmp_path):
    alert_id, po_id = _approved()
    path = po_service.send_po(po_id, MANAGER)
    file = tmp_path / "outbox" / "PO-005.csv"
    assert file.exists() and path.endswith("PO-005.csv")
    with open(file, encoding="utf-8-sig", newline="") as f:
        content = dict(row for row in csv.reader(f))
    assert content["Mã PO"] == "PO-005" and content["Số lượng"] == "15"
    po = repository.get_purchase_order(po_id)
    assert (po["status"], po["delivery_status"], po["sent_by"], po["send_attempts"]) == \
        ("SENT", "PENDING", "Duyên", 1)
    assert po["expected_delivery_date"] == "2026-11-21"   # NCC Chính giao 24h; cột chỉ có ngày
    assert repository.get_alert(alert_id)["status"] == "RESOLVED"
    assert len(logs("PO_SENT")) == 1


# ---------------------------------------------------------------- nhận hàng
@pytest.fixture()
def fake_receive(monkeypatch):
    """receive_batch là việc của Phương Anh (P04); ở đây chỉ kiểm phần của po_service."""
    from services import inventory_service
    calls = []

    def receive_batch(po_id, qty, expiry_date=None, conn=None):
        calls.append((po_id, qty, expiry_date))
        return "BOLAT-L9"

    monkeypatch.setattr(inventory_service, "receive_batch", receive_batch, raising=False)
    return calls


def _sent():
    _, po_id = _approved()
    po_service.send_po(po_id, MANAGER)
    return po_id


def test_mark_received(db, fake_receive):
    po_id = _sent()
    assert po_service.mark_received(po_id, 15, MANAGER) == "BOLAT-L9"
    assert fake_receive == [(po_id, 15.0, None)]
    po = repository.get_purchase_order(po_id)
    assert (po["delivery_status"], po["received_qty"], po["status"]) == ("RECEIVED", 15, "SENT")
    assert len(logs("DELIVERY_RECEIVED")) == 1
    _blocked("INVALID_TRANSITION", po_service.mark_received, po_id, 15, MANAGER)   # không nhận 2 lần


def test_mark_received_po_giao_tre(db, fake_receive):
    repository.update_purchase_order("PO-004", {"delivery_status": "DELAYED"})   # seed: Bơ lạt 20 kg
    assert po_service.mark_received("PO-004", 20, MANAGER) == "BOLAT-L9"
    assert repository.get_purchase_order("PO-004")["delivery_status"] == "RECEIVED"


def test_mark_received_phai_nhan_du_va_la_quan_ly(db, fake_receive):
    po_id = _sent()
    _blocked("QTY_INVALID", po_service.mark_received, po_id, 10, MANAGER)
    _blocked("QTY_INVALID", po_service.mark_received, po_id, 0, MANAGER)
    _blocked("NOT_MANAGER", po_service.mark_received, po_id, 15, STAFF)
    assert fake_receive == []   # không tạo lô khi bị chặn


def test_nhan_hang_bam_hai_lan_cung_luc_chi_tao_mot_lo(db, monkeypatch):
    import threading
    import time
    from services import inventory_service
    po_id = _sent()
    made = []

    def slow_receive(po_id, qty, expiry_date=None, conn=None):
        time.sleep(0.2)
        made.append(po_id)
        return f"BOLAT-L{len(made) + 8}"

    monkeypatch.setattr(inventory_service, "receive_batch", slow_receive, raising=False)
    results = []

    def click():
        try:
            results.append(po_service.mark_received(po_id, 15, MANAGER))
        except GuardrailError as err:
            results.append(err.code)

    threads = [threading.Thread(target=click) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1
    assert sorted(results) == ["BOLAT-L9", "INVALID_TRANSITION", "INVALID_TRANSITION"]


def test_tao_lo_loi_thi_po_tro_lai_cho_giao(db, monkeypatch):
    from services import inventory_service
    po_id = _sent()

    def broken(po_id, qty, expiry_date=None, conn=None):
        raise RuntimeError("DB lỗi")

    monkeypatch.setattr(inventory_service, "receive_batch", broken, raising=False)
    with pytest.raises(RuntimeError):
        po_service.mark_received(po_id, 15, MANAGER)
    po = repository.get_purchase_order(po_id)
    assert (po["delivery_status"], po["received_qty"]) == ("PENDING", 0)
    assert logs("DELIVERY_RECEIVED") == []


def _batch_ids():
    return {b["batch_id"] for b in repository.get_batches("BO_LAT")}


def test_nhan_hang_that_tao_lo_va_ghi_ca_hai_log(db):
    """DU2: dùng receive_batch thật (PA1), cả 4 bước trong một giao dịch."""
    po_id = _sent()
    before = _batch_ids()
    batch_id = po_service.mark_received(po_id, 15, MANAGER)
    assert _batch_ids() - before == {batch_id}
    assert repository.get_batch(batch_id)["po_id"] == po_id
    po = repository.get_purchase_order(po_id)
    assert (po["delivery_status"], po["received_qty"]) == ("RECEIVED", 15)
    assert len(logs("BATCH_RECEIVED")) == 1 and len(logs("DELIVERY_RECEIVED")) == 1


def test_loi_sau_khi_tao_lo_thi_rollback_ca_lo_va_po(db, monkeypatch):
    """DU2: lô đã chèn nhưng ghi log DELIVERY_RECEIVED lỗi -> không còn lô mồ côi, PO vẫn chờ giao."""
    po_id = _sent()
    before = _batch_ids()
    real_log = po_service.write_audit_log

    def log_fails(actor, event_type, *args, **kwargs):
        if event_type == "DELIVERY_RECEIVED":
            raise RuntimeError("ghi log lỗi")
        return real_log(actor, event_type, *args, **kwargs)

    monkeypatch.setattr(po_service, "write_audit_log", log_fails)
    with pytest.raises(RuntimeError):
        po_service.mark_received(po_id, 15, MANAGER)
    assert _batch_ids() == before
    po = repository.get_purchase_order(po_id)
    assert (po["delivery_status"], po["received_qty"]) == ("PENDING", 0)
    assert logs("BATCH_RECEIVED") == [] and logs("DELIVERY_RECEIVED") == []
    monkeypatch.setattr(po_service, "write_audit_log", real_log)
    assert po_service.mark_received(po_id, 15, MANAGER)   # sửa xong thì nhận lại được


# ---------------------------------------------------------------- bỏ qua cảnh báo
def test_dismiss_alert(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    po_service.dismiss_alert(alert_id, MANAGER)
    alert = repository.get_alert(alert_id)
    assert alert["status"] == "DISMISSED" and alert["resolved_at"]
    assert len(logs("ALERT_DISMISSED")) == 1
    _blocked("ALERT_NOT_OPEN", po_service.dismiss_alert, alert_id, MANAGER)
    _blocked("ALERT_NOT_OPEN", po_service.create_draft_po, alert_id, "A", 15, MANAGER)


def test_dismiss_alert_chi_quan_ly(db):
    alert_id = make_alert("BOT_MI", BOT_MI_OPTIONS)
    _blocked("NOT_MANAGER", po_service.dismiss_alert, alert_id, STAFF)
    assert repository.get_alert(alert_id)["status"] == "OPEN"
