"""TC02 – Thiếu do đơn đặt trước (Kem tươi, seed chuẩn).

Kỳ vọng (đặc tả Mục 13, contracts Mục 5, data/expected.md): cảnh báo Kem tươi có mã PREORDER (3 lít);
projected = −2, shortage = 4; A = NCC Chính 4 lít 360.000đ, B = NCC Hỏa tốc 4 lít 414.000đ (+15%).
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.init_db import init_db  # noqa: E402
from services.calc import compute_alerts  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc02.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc02_preorder_cause(db):
    alerts = {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}
    kem = alerts["KEM_TUOI"]
    assert {"code": "PREORDER", "qty": 3.0} in kem["causes"]
    assert kem["causes"] == [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0},
                             {"code": "DAILY_USAGE", "qty": 3.0}]
    assert kem["projected_stock"] == pytest.approx(-2.0)
    assert kem["min_stock"] == pytest.approx(2.0)
    assert kem["shortage_qty"] == pytest.approx(4.0)
    assert kem["open_po_id"] is None and kem["note"] is None


def test_tc02_options_match_contract(db):
    kem = {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}["KEM_TUOI"]
    a, b = kem["options"]
    assert (a["option"], a["sup_id"], a["order_qty"], a["total_cost"]) == ("A", "NCC_CHINH", 4.0, 360000)
    assert a["arrival_at"] == "2026-11-21T08:00:00" and a["meets_earliest_preorder"] is True
    assert a["cost_diff_pct_vs_A"] == 0
    assert (b["option"], b["sup_id"], b["order_qty"], b["total_cost"]) == ("B", "NCC_HOA_TOC", 4.0, 414000)
    assert b["arrival_at"] == "2026-11-20T10:00:00" and b["meets_earliest_preorder"] is True
    assert b["cost_diff_pct_vs_A"] == pytest.approx(15.0)
    assert b["lead_time_hours"] == 2 and b["lead_time_days"] == 1
