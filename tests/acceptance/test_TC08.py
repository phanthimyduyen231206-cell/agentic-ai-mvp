"""TC08 – Quản lý từ chối PO DRAFT: PO đóng với closed_reason = REJECTED; lưu người và thời điểm."""
from data import repository
from services import po_service
from tests.po_helpers import BO_LAT_OPTIONS, MANAGER, NOW, db, logs, make_alert  # noqa: F401


def test_tc08_tu_choi_po_draft(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    po_id = po_service.create_draft_po(alert_id, "A", 15, MANAGER)

    po_service.reject_po(po_id, MANAGER, "Giá cao")

    po = repository.get_purchase_order(po_id)
    assert po["status"] == "DRAFT"                       # không có trạng thái REJECTED
    assert po["closed_reason"] == "REJECTED"
    assert po["closed_by"] == MANAGER["username"]
    assert po["closed_at"] == NOW.isoformat(timespec="seconds")
    assert repository.get_alert(alert_id)["status"] == "RESOLVED"
    log = logs("PO_REJECTED")
    assert len(log) == 1 and log[0]["entity_id"] == po_id and "Giá cao" in log[0]["detail_json"]
