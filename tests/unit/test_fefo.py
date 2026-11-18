"""Unit test cho services/fefo.py (Q01). Chạy: pytest tests/unit/test_fefo.py -v

Số liệu kỳ vọng lấy từ data/expected.md (H = 3, DEMO_DATE = 2026-11-20T08:00).
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services.fefo import fefo_projection  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
H = 3


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "test.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


@pytest.fixture()
def db_no_shortage(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "test.db", "no_shortage", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


# ---------- Đối chiếu bảng expected.md ----------

STANDARD = {  # ingredient_id: (projected, expired)
    "KEM_TUOI": (-2.0, 6.0),
    "BO_LAT": (-10.0, 0.0),
    "BOT_MI": (0.0, 0.0),
    "MASCARPONE": (-0.6, 0.0),
    "TRUNG": (50.0, 0.0),
    "DUONG": (16.5, 0.0),
    "SUA_TUOI": (8.25, 0.0),
    "MEN_NO": (1.5, 0.0),
    "BOT_CACAO": (1.6, 0.0),
    "SOCOLA": (3.5, 0.0),
    "VANI": (365.0, 0.0),
    "PHO_MAI_KEM": (4.5, 0.0),
}


@pytest.mark.parametrize("ingredient_id, projected, expired", [(k, *v) for k, v in STANDARD.items()])
def test_bo_chuan_khop_expected(db, ingredient_id, projected, expired):
    r = fefo_projection(ingredient_id, H, NOW)
    assert r["projected_stock"] == pytest.approx(projected)
    assert r["expired_qty"] == pytest.approx(expired)
    assert r["projected_stock"] == pytest.approx(r["remaining_qty"] - r["unmet_qty"])


@pytest.mark.parametrize("ingredient_id, projected", [
    ("KEM_TUOI", 4.0), ("BO_LAT", 10.0), ("BOT_MI", 40.0), ("MASCARPONE", 1.4),
])
def test_bo_du_hang_khop_expected(db_no_shortage, ingredient_id, projected):
    assert fefo_projection(ingredient_id, H, NOW)["projected_stock"] == pytest.approx(projected)


@pytest.mark.parametrize("ingredient_id", list(STANDARD))
def test_cung_ket_qua_voi_cong_thuc_muc_5_1(db, ingredient_id):
    """projected = current − preorder − daily × H + incoming − expired (đặc tả 5.2)."""
    ing = repo.get_ingredient(ingredient_id)
    current = sum(b["qty"] for b in repo.get_batches_fefo(ingredient_id, NOW))
    end = NOW.date() + timedelta(days=H - 1)
    preorder = sum(p["need_qty"] for p in repo.get_pre_order_needs(ingredient_id, NOW.date(), end))
    r = fefo_projection(ingredient_id, H, NOW)
    assert r["projected_stock"] == pytest.approx(
        current - preorder - ing["daily_usage"] * H + 0 - r["expired_qty"])


# ---------- Kem tươi: kế hoạch từng lô (contracts Mục 4) ----------

def test_kem_tuoi_ke_hoach_va_lo_het_han(db):
    r = fefo_projection("KEM_TUOI", H, NOW)
    assert r == {
        "remaining_qty": 0.0, "expired_qty": 6.0, "unmet_qty": 2.0, "projected_stock": -2.0,
        "plan": [{"day": "2026-11-20", "batch_id": "KEM-L1", "take_qty": 1.0},
                 {"day": "2026-11-21", "batch_id": "KEM-L2", "take_qty": 3.0}],
        "expired": [{"day": "2026-11-20", "batch_id": "KEM-L1", "qty": 6.0}],
    }


def test_sau_20h_lo_l1_khong_con_tinh(db):
    """Sau 20:00, KEM-L1 đã hết hạn nên get_batches_fefo không trả nữa."""
    r = fefo_projection("KEM_TUOI", H, datetime(2026, 11, 20, 20, 30))
    assert r["expired_qty"] == 0.0
    assert {p["batch_id"] for p in r["plan"]} == {"KEM-L2"}
    assert r["unmet_qty"] == pytest.approx(3.0)
    assert r["projected_stock"] == pytest.approx(-3.0)


def test_days_0_khong_tieu_thu(db):
    r = fefo_projection("KEM_TUOI", 0, NOW)
    assert r["remaining_qty"] == pytest.approx(10.0)
    assert r["unmet_qty"] == 0.0 and r["expired_qty"] == 0.0
    assert r["plan"] == [] and r["expired"] == []


def test_mac_dinh_lay_gio_tu_clock(db):
    assert fefo_projection("KEM_TUOI", H) == fefo_projection("KEM_TUOI", H, NOW)


# ---------- Hàng sắp về (PO SENT) ----------

def test_po_qua_han_bi_bo_qua(db):
    """PO-004: SENT + PENDING, dự kiến hôm qua → không tính incoming."""
    r = fefo_projection("BO_LAT", H, NOW)
    assert not any(p["batch_id"].startswith("PO-004") for p in r["plan"])
    assert r["projected_stock"] == pytest.approx(-10.0)


def test_po_pending_ve_trong_ky_duoc_cong(db):
    repo.update_purchase_order("PO-004", {"expected_delivery_date": (NOW.date() + timedelta(days=1)).isoformat()})
    r = fefo_projection("BO_LAT", H, NOW)
    assert r["projected_stock"] == pytest.approx(10.0)  # −10 + 20 kg
    assert any(p["batch_id"] == "PO-004-INCOMING" for p in r["plan"])


def test_po_delayed_khong_duoc_cong(db):
    repo.update_purchase_order("PO-004", {
        "delivery_status": "DELAYED",
        "expected_delivery_date": (NOW.date() + timedelta(days=1)).isoformat(),
    })
    assert fefo_projection("BO_LAT", H, NOW)["projected_stock"] == pytest.approx(-10.0)


def test_po_ve_sau_ky_tinh_khong_duoc_cong(db):
    repo.update_purchase_order("PO-004", {"expected_delivery_date": (NOW.date() + timedelta(days=H)).isoformat()})
    assert fefo_projection("BO_LAT", H, NOW)["projected_stock"] == pytest.approx(-10.0)


# ---------- An toàn ----------

def test_khong_ghi_du_lieu(db):
    before = repo.compute_snapshot_hash("KEM_TUOI")
    fefo_projection("KEM_TUOI", H, NOW)
    assert repo.compute_snapshot_hash("KEM_TUOI") == before
    assert [b["qty"] for b in repo.get_batches_fefo("KEM_TUOI", NOW)] == [7.0, 3.0]


def test_nguyen_lieu_khong_ton_tai(db):
    with pytest.raises(ValueError):
        fefo_projection("KHONG_CO", H, NOW)


@pytest.mark.parametrize("days", [-1, 1.5])
def test_days_khong_hop_le(db, days):
    with pytest.raises(ValueError):
        fefo_projection("KEM_TUOI", days, NOW)
