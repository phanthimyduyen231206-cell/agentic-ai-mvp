"""TC12 – Chỉ một NCC hợp lệ (Mascarpone, seed chuẩn).

Kỳ vọng (đặc tả Mục 13, data/expected.md): projected = −0,6 < 0,5 → thiếu 1,1 kg;
main_cause = PREORDER (1 kg); chỉ NCC Chính bán → 1 phương án (MOQ 1 → 2 kg, 600.000đ), có ghi lý do.
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
    path = init_db(tmp_path / "tc12.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc12_single_supplier_one_option_with_reason(db):
    mas = {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}["MASCARPONE"]
    assert mas["projected_stock"] == pytest.approx(-0.6)
    assert mas["shortage_qty"] == pytest.approx(1.1)
    assert mas["main_cause"] == "PREORDER"
    assert mas["causes"] == [{"code": "PREORDER", "qty": 1.0}, {"code": "DAILY_USAGE", "qty": 0.6}]
    (opt,) = mas["options"]
    assert (opt["option"], opt["sup_id"], opt["order_qty"], opt["total_cost"]) == \
        ("A", "NCC_CHINH", 2.0, 600000)
    assert mas["note"] and "Chỉ có 1 NCC hợp lệ" in mas["note"]
