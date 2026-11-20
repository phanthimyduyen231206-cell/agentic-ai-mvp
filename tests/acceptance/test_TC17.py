"""TC17 – Xuất kho theo FEFO (Phương Anh, P04).

Đặc tả Mục 13: xuất 80 quả trứng ở Tab 1 -> lô hạn sớm về 0, lô sau còn 70; log STOCK_ISSUED;
xuất quá tồn còn hạn bị chặn.
"""
from datetime import datetime

import pytest

from data import repository
from data.init_db import init_db
from services import inventory_service
from services.po_service import GuardrailError

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "Quản lý", "role": "MANAGER"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc17.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_TC17_xuat_80_trung_theo_fefo(db):
    taken = inventory_service.issue_stock_fefo("TRUNG", 80, MANAGER)

    assert taken == [{"batch_id": "TRUNG-L1", "taken": 60.0}, {"batch_id": "TRUNG-L2", "taken": 20.0}]
    assert repository.get_batch("TRUNG-L1")["qty"] == 0     # lô hạn sớm về 0
    assert repository.get_batch("TRUNG-L2")["qty"] == 70    # lô sau còn 70
    assert len(repository.get_audit_logs(event_type="STOCK_ISSUED")) == 1


def test_TC17_xuat_qua_ton_con_han_bi_chan(db):
    inventory_service.issue_stock_fefo("TRUNG", 80, MANAGER)     # còn 70 quả
    with pytest.raises(GuardrailError) as e:
        inventory_service.issue_stock_fefo("TRUNG", 71, MANAGER)
    assert e.value.code == "QTY_INVALID"
    assert repository.get_batch("TRUNG-L2")["qty"] == 70          # không bị trừ thêm
    assert len(repository.get_audit_logs(event_type="STOCK_ISSUED")) == 1
    assert len(repository.get_audit_logs(event_type="GUARDRAIL_BLOCKED")) == 1
