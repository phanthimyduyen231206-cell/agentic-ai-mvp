"""TC14 – PO giao trễ (Mai Anh).

Cách thực hiện: seed chuẩn (PO-004: SENT, hẹn giao hôm qua, chưa nhận).
Kết quả cần đạt: status vẫn SENT, delivery_status = DELAYED; cảnh báo có SUPPLIER_DELAY.
Phần SUPPLIER_DELAY cần services/calc.py (Q02); khi calc chưa có compute_alerts thì test đó được skip.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services import calc  # noqa: E402
from services.audit_service import run_morning_audit  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc14.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc14_po004_marked_delayed(db, monkeypatch):
    if not hasattr(calc, "compute_alerts"):
        monkeypatch.setattr(calc, "compute_alerts", lambda run_date: [], raising=False)
    assert repo.get_purchase_order("PO-004")["delivery_status"] == "PENDING"

    out = run_morning_audit("SCHEDULED", llm=FakeLLM())

    assert out["status"] == "SUCCEEDED"
    po = repo.get_purchase_order("PO-004")
    assert po["status"] == "SENT"  # trạng thái PO không đổi
    assert po["delivery_status"] == "DELAYED"  # chỉ trạng thái giao hàng đổi
    logs = repo.get_audit_logs(event_type="DELIVERY_DELAYED")
    assert [log["entity_id"] for log in logs] == ["PO-004"]
    assert logs[0]["run_id"] == out["run_id"]


def test_tc14_alert_has_supplier_delay(db):
    if not hasattr(calc, "compute_alerts"):
        pytest.skip("services/calc.compute_alerts chưa có (Q02)")
    out = run_morning_audit("SCHEDULED", llm=FakeLLM())
    assert out["status"] == "SUCCEEDED"
    bo = repo.get_alerts(run_id=out["run_id"], ingredient_id="BO_LAT")
    assert bo, "Không có cảnh báo Bơ lạt"
    codes = [c["code"] for c in json.loads(bo[0]["causes"])]
    assert "SUPPLIER_DELAY" in codes
