"""TC16 – Hết hạn trước khi dùng (FEFO), kịch bản Kem tươi trên seed chuẩn.

Kỳ vọng (đặc tả Mục 9 / data/expected.md): expired_qty = 6; projected = −2; main_cause = EXPIRING_LOT.
Phần main_cause cần services/calc.py (Q02); khi calc chưa có compute_alerts thì test đó được skip.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.init_db import init_db  # noqa: E402
from services.fefo import fefo_projection  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc16.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc16_fefo_kem_tuoi(db):
    r = fefo_projection("KEM_TUOI", 3)
    assert r["expired_qty"] == pytest.approx(6.0)
    assert r["projected_stock"] == pytest.approx(-2.0)
    assert r["expired"] == [{"day": "2026-11-20", "batch_id": "KEM-L1", "qty": 6.0}]


def test_tc16_main_cause_expiring_lot(db):
    from services import calc
    if not hasattr(calc, "compute_alerts"):
        pytest.skip("services/calc.compute_alerts chưa có (Q02)")
    alerts = {a["ingredient_id"]: a for a in calc.compute_alerts(NOW.date())}
    kem = alerts["KEM_TUOI"]
    assert kem["projected_stock"] == pytest.approx(-2.0)
    assert kem["main_cause"] == "EXPIRING_LOT"
    assert {"code": "EXPIRING_LOT", "qty": 6.0} in kem["causes"]
