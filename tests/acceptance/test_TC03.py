"""TC03 – Thiếu do bán lẻ hằng ngày (Bột mì, seed chuẩn).

Kỳ vọng (đặc tả Mục 13, data/expected.md): main_cause = DAILY_USAGE (36 kg), PREORDER 4 kg;
projected = 0 < min_stock 10 → thiếu 10 kg. NCC Hỏa tốc trống email nên không hợp lệ;
NCC Chính vừa rẻ nhất vừa nhanh nhất → 1 phương án (10 kg, 200.000đ) kèm ghi chú (đặc tả 5.4).
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
    path = init_db(tmp_path / "tc03.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc03_main_cause_daily_usage(db):
    bot = {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}["BOT_MI"]
    assert bot["main_cause"] == "DAILY_USAGE"
    assert bot["causes"] == [{"code": "DAILY_USAGE", "qty": 36.0}, {"code": "PREORDER", "qty": 4.0}]
    assert bot["projected_stock"] == pytest.approx(0.0)
    assert bot["shortage_qty"] == pytest.approx(10.0)


def test_tc03_single_option_cheapest_and_fastest(db):
    bot = {a["ingredient_id"]: a for a in compute_alerts(NOW.date())}["BOT_MI"]
    (opt,) = bot["options"]
    assert (opt["option"], opt["sup_id"], opt["order_qty"], opt["total_cost"]) == \
        ("A", "NCC_CHINH", 10.0, 200000)
    assert "NCC_HOA_TOC" not in [o["sup_id"] for o in bot["options"]]
    assert "vừa rẻ nhất vừa nhanh nhất" in bot["note"]
