"""tests/unit/test_repository.py - Unit test cho data/repository.py (P03).

Chay:  pytest tests/unit/test_repository.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402


def _now():
    """Lay 'bay gio' theo clock.py cua Duyen neu da co, neu chua thi dung gio may."""
    try:
        from services import clock
        return clock.now()
    except Exception:
        return datetime.now()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Tao DB mau thang trong thu muc tam cua test (khong dung toi data/bakery.db that),
    roi tro repository vao file tam do."""
    from data.init_db import init_db

    dst = init_db(tmp_path / "test.db", now=_now())
    monkeypatch.setenv("BAKERY_DB_PATH", str(dst))
    return dst


def _egg():
    egg = repo.get_ingredient_by_name("Trứng")
    assert egg is not None, "Khong tim thay nguyen lieu ten co chu 'Trứng'"
    return egg


# ---------------- Doc co ban ----------------

def test_get_ingredients_has_12(db):
    assert len(repo.get_ingredients()) == 12


def test_get_ingredient_by_id(db):
    egg = _egg()
    again = repo.get_ingredient(egg["id"])
    assert again["name"] == egg["name"]


def test_get_suppliers_and_pos(db):
    assert len(repo.get_suppliers()) > 0
    assert isinstance(repo.get_purchase_orders(), list)
    assert isinstance(repo.get_pre_orders(), list)
    assert isinstance(repo.get_alerts(), list)
    assert isinstance(repo.get_audit_runs(), list)
    assert isinstance(repo.get_audit_logs(limit=5), list)


# ---------------- get_batches_fefo ----------------

def test_get_batches_fefo_egg_order(db):
    """Trung: 2 lo 60 (het han sau 3 ngay) roi 90 (het han sau 10 ngay)."""
    batches = repo.get_batches_fefo(_egg()["id"], _now())
    qtys = [b["qty"] for b in batches]
    assert qtys == [60, 90]
    assert batches[0]["expiry_date"] <= batches[1]["expiry_date"]


def test_get_batches_fefo_skips_expired_and_empty(db):
    egg_id = _egg()["id"]
    now = _now()
    expired_id = repo.insert_batch(egg_id, 30, now - timedelta(days=1), now - timedelta(days=5))
    empty_id = repo.insert_batch(egg_id, 0, now + timedelta(days=1), now - timedelta(days=1))
    ids = [b["batch_id"] for b in repo.get_batches_fefo(egg_id, now)]
    assert expired_id not in ids
    assert empty_id not in ids


def test_get_batches_fefo_tie_break_received_at(db):
    """Cung expiry_date thi lo nhan truoc (received_at som hon) dung truoc."""
    egg_id = _egg()["id"]
    now = _now()
    exp = now + timedelta(days=1)
    late = repo.insert_batch(egg_id, 5, exp, now - timedelta(hours=1))
    early = repo.insert_batch(egg_id, 5, exp, now - timedelta(hours=5))
    ids = [b["batch_id"] for b in repo.get_batches_fefo(egg_id, now)]
    assert ids.index(early) < ids.index(late)


# ---------------- compute_snapshot_hash ----------------

def test_snapshot_hash_stable(db):
    egg_id = _egg()["id"]
    assert repo.compute_snapshot_hash(egg_id) == repo.compute_snapshot_hash(egg_id)


def test_snapshot_hash_changes_when_batch_changes(db):
    egg_id = _egg()["id"]
    before = repo.compute_snapshot_hash(egg_id)
    first = repo.get_batches_fefo(egg_id, _now())[0]
    repo.update_batch_qty(first["batch_id"], first["qty"] - 1)
    assert repo.compute_snapshot_hash(egg_id) != before


def test_snapshot_hash_differs_between_ingredients(db):
    ings = repo.get_ingredients()
    assert repo.compute_snapshot_hash(ings[0]["id"]) != repo.compute_snapshot_hash(ings[1]["id"])


# ---------------- Ghi co ban ----------------

def test_update_rejects_unknown_column(db):
    first = repo.get_batches(_egg()["id"])[0]
    conn = repo.get_connection()
    try:
        with pytest.raises(ValueError):
            repo._update(conn, "inventory_batches", "batch_id",
                         first["batch_id"], {"cot_khong_ton_tai": 1})
    finally:
        conn.close()


def test_insert_batch_saves_iso_time_with_T(db):
    """contracts.md: ngay gio luu dang 'YYYY-MM-DDTHH:MM:SS'."""
    egg_id = _egg()["id"]
    bid = repo.insert_batch(egg_id, 5, datetime(2026, 12, 1, 8, 0), datetime(2026, 11, 18, 9, 0))
    saved = repo.get_batch(bid)
    assert saved["expiry_date"] == "2026-12-01T08:00:00"
    assert saved["received_at"] == "2026-11-18T09:00:00"


def test_shared_connection_rollback(db):
    """Truyen conn chung: rollback thi khong luu."""
    egg_id = _egg()["id"]
    first = repo.get_batches_fefo(egg_id, _now())[0]
    conn = repo.get_connection()
    repo.update_batch_qty(first["batch_id"], 0, conn=conn)
    conn.rollback()
    conn.close()
    assert repo.get_batch(first["batch_id"])["qty"] == first["qty"]
