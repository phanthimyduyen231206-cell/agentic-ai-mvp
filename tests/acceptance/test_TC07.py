"""TC07 – Quản lý chọn A ở cảnh báo Bơ lạt: chỉ tạo đúng 1 PO DRAFT theo NCC và số lượng của A."""
from data import repository
from services import po_service
from tests.po_helpers import BO_LAT_OPTIONS, MANAGER, db, make_alert, po_count  # noqa: F401


def test_tc07_chon_a_tao_dung_mot_po_draft(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    before = po_count()
    option_a = BO_LAT_OPTIONS[0]

    po_id = po_service.create_draft_po(alert_id, "A", option_a["order_qty"], MANAGER)

    assert po_count() == before + 1
    drafts = [p for p in repository.get_purchase_orders(status="DRAFT") if p["alert_id"] == alert_id]
    assert [p["po_id"] for p in drafts] == [po_id]
    po = drafts[0]
    assert po["sup_id"] == option_a["sup_id"] == "NCC_CHINH"
    assert po["order_qty"] == option_a["order_qty"] == 15
    assert po["total_cost"] == option_a["total_cost"] == 3750000
    assert repository.get_alert(alert_id)["status"] == "SELECTED"
