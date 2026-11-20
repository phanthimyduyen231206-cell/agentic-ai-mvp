"""TC15 – Dữ liệu đổi khi chờ chọn: sửa tồn kho rồi mới chọn A/B -> từ chối tạo PO, yêu cầu chạy lại rà soát."""
import pytest

from data import repository
from services import po_service
from services.po_service import GuardrailError
from tests.po_helpers import BO_LAT_OPTIONS, MANAGER, db, logs, make_alert, po_count  # noqa: F401


def test_tc15_sua_ton_kho_sau_khi_ra_soat(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)   # snapshot_hash lúc rà soát
    before = po_count()

    batch = repository.get_batches("BO_LAT")[0]
    repository.update_batch_qty(batch["batch_id"], batch["qty"] + 10)   # có người sửa tồn kho

    with pytest.raises(GuardrailError) as exc:
        po_service.create_draft_po(alert_id, "A", 15, MANAGER)
    assert exc.value.code == "SNAPSHOT_CHANGED"
    assert "chạy lại rà soát" in exc.value.message

    assert po_count() == before
    assert repository.get_alert(alert_id)["status"] == "OPEN"
    blocked = logs("GUARDRAIL_BLOCKED")
    assert len(blocked) == 1 and '"SNAPSHOT_CHANGED"' in blocked[0]["detail_json"]
