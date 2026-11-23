"""TC04 – Có hai NCC hợp lệ (Bơ lạt, seed chuẩn).

Kỳ vọng (đặc tả Mục 5.4, data/expected.md): thiếu 3 − (−10) = 13 kg;
A = NCC Chính (MOQ 5 → 15 kg, 3.750.000đ), B = NCC Hỏa tốc (MOQ 2 → 14 kg, 4.025.000đ, 2 giờ).
PO-004 được đặt DELAYED trước khi gọi calc (như bước 3 của run_morning_audit) → SUPPLIER_DELAY 20 kg là nguyên nhân chính.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services.calc import compute_alerts  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc04.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    repo.update_purchase_order("PO-004", {"delivery_status": "DELAYED"})
    return path


def _bo_lat():
    return {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}["BO_LAT"]


def test_tc04_shortage_and_causes(db):
    bo = _bo_lat()
    assert bo["projected_stock"] == pytest.approx(-10.0)
    assert bo["shortage_qty"] == pytest.approx(13.0)
    assert bo["main_cause"] == "SUPPLIER_DELAY"
    assert bo["causes"] == [{"code": "SUPPLIER_DELAY", "qty": 20.0}, {"code": "PREORDER", "qty": 12.0},
                            {"code": "DAILY_USAGE", "qty": 3.0}]


def test_tc04_two_options_rounded_to_moq(db):
    a, b = _bo_lat()["options"]
    assert (a["option"], a["sup_id"], a["order_qty"], a["total_cost"]) == ("A", "NCC_CHINH", 15.0, 3750000)
    assert (b["option"], b["sup_id"], b["order_qty"], b["total_cost"]) == ("B", "NCC_HOA_TOC", 14.0, 4025000)
    assert a["order_qty"] % 5 == 0 and b["order_qty"] % 2 == 0
    assert b["lead_time_hours"] < a["lead_time_hours"]
    assert b["cost_diff_pct_vs_A"] == pytest.approx(7.3)
    # Croissant giao 21/11: A về 08:00 ngày 21 (sau mốc 07:00) → không kịp; B về 10:00 hôm nay → kịp.
    assert a["meets_earliest_preorder"] is False and b["meets_earliest_preorder"] is True
    assert a["reason"] == "Rẻ nhất, về chậm hơn B 22 giờ"
    assert b["reason"] == "Nhanh nhất, đắt hơn A 7.3% (275.000đ)"
