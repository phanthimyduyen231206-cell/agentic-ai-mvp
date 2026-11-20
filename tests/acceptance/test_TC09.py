"""TC09 – Gọi create_draft_po 2 lần cho cùng cảnh báo: chỉ có 1 PO; lần 2 bị chặn, log GUARDRAIL_BLOCKED."""
import pytest

from data import repository
from services import po_service
from services.po_service import GuardrailError
from tests.po_helpers import BO_LAT_OPTIONS, MANAGER, db, logs, make_alert  # noqa: F401


def test_tc09_tao_po_hai_lan(db):
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    first = po_service.create_draft_po(alert_id, "A", 15, MANAGER)

    with pytest.raises(GuardrailError) as exc:
        po_service.create_draft_po(alert_id, "A", 15, MANAGER)
    assert exc.value.code == "DUPLICATE_PO"

    pos = [p for p in repository.get_purchase_orders() if p["alert_id"] == alert_id]
    assert [p["po_id"] for p in pos] == [first]
    blocked = logs("GUARDRAIL_BLOCKED")
    assert len(blocked) == 1 and '"DUPLICATE_PO"' in blocked[0]["detail_json"]


def test_tc09_index_db_chan_po_mo_thu_hai(db):
    """Lớp cuối: ux_po_alert chặn ở tầng DB dù có ai ghi thẳng vào bảng."""
    import sqlite3
    alert_id = make_alert("BO_LAT", BO_LAT_OPTIONS)
    po_service.create_draft_po(alert_id, "A", 15, MANAGER)
    with pytest.raises(sqlite3.IntegrityError):
        repository.insert_purchase_order({
            "po_id": "PO-099", "alert_id": alert_id, "sup_id": "NCC_HOA_TOC", "ingredient_id": "BO_LAT",
            "order_qty": 14, "unit_price": 287500, "total_cost": 4025000, "status": "DRAFT",
            "created_by": "x", "created_at": "2026-11-20T08:00:00",
        })
