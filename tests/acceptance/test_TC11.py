"""TC11 – Gửi PO tới NCC thiếu contact_email: PO vẫn APPROVED, có last_send_error; gửi lại được."""
import pytest

from data import repository
from services import po_service
from services.po_service import GuardrailError
from tests.po_helpers import BOT_MI_OPTIONS, MANAGER, db, logs, make_alert  # noqa: F401


def test_tc11_gui_that_bai_roi_gui_lai(db, tmp_path):
    # Seed: NCC Hỏa tốc để trống contact_email của Bột mì
    alert_id = make_alert("BOT_MI", BOT_MI_OPTIONS)
    po_id = po_service.create_draft_po(alert_id, "B", 10, MANAGER)
    po_service.approve_po(po_id, MANAGER)

    with pytest.raises(GuardrailError) as exc:
        po_service.send_po(po_id, MANAGER)
    assert exc.value.code == "SEND_FAILED"

    po = repository.get_purchase_order(po_id)
    assert po["status"] == "APPROVED" and po["closed_reason"] is None
    assert po["send_attempts"] == 1 and "contact_email" in po["last_send_error"]
    assert not (tmp_path / "outbox" / f"{po_id}.csv").exists()
    assert len(logs("PO_SEND_FAILED")) == 1
    assert repository.get_alert(alert_id)["status"] == "SELECTED"

    # Quản lý bổ sung email NCC rồi bấm "Gửi lại"
    conn = repository.get_connection()
    conn.execute("UPDATE suppliers SET contact_email = 'hoatoc@example.com' "
                 "WHERE sup_id = 'NCC_HOA_TOC' AND ingredient_id = 'BOT_MI'")
    conn.commit()
    conn.close()

    path = po_service.send_po(po_id, MANAGER)
    po = repository.get_purchase_order(po_id)
    assert po["status"] == "SENT" and po["send_attempts"] == 2 and po["last_send_error"] is None
    assert (tmp_path / "outbox" / f"{po_id}.csv").exists() and path.endswith(f"{po_id}.csv")
    assert repository.get_alert(alert_id)["status"] == "RESOLVED"
