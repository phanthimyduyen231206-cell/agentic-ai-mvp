"""Unit test cho data/init_db.py (P01 + P02). Chạy: pytest tests/unit/test_init_db.py -v"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.init_db import init_db  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)  # cố định "bây giờ" để test luôn ra cùng kết quả


@pytest.fixture
def conn(tmp_path):
    path = init_db(tmp_path / "test.db", "standard", now=NOW)
    c = sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


# ---------- P01: schema ----------
def test_co_du_9_bang(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert tables == {"ingredients", "inventory_batches", "recipes", "pre_orders", "suppliers",
                      "purchase_orders", "audit_runs", "alerts", "audit_logs"}


def test_chay_lai_nhieu_lan_khong_loi(tmp_path):
    for _ in range(3):
        init_db(tmp_path / "x.db", "standard", now=NOW)


def test_chi_mot_phien_running(conn):
    sql = "INSERT INTO audit_runs (run_date, \"trigger\", status, started_at) VALUES ('2026-11-20','MANUAL','RUNNING','x')"
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)  # ux_run_running chặn phiên RUNNING thứ 2


def test_po_status_sai_bi_chan(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE purchase_orders SET status = 'REJECTED' WHERE po_id = 'PO-004'")


def test_closed_reason_sai_bi_chan(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE purchase_orders SET closed_reason = 'DELETED' WHERE po_id = 'PO-004'")


def test_mot_canh_bao_chi_mot_po_dang_mo(conn):
    conn.execute("INSERT INTO audit_runs (run_date, \"trigger\", status, started_at) VALUES ('2026-11-20','MANUAL','SUCCEEDED','x')")
    conn.execute("INSERT INTO alerts (run_id, ingredient_id, status, created_at) VALUES (1, 'BO_LAT', 'OPEN', 'x')")
    sql = ("INSERT INTO purchase_orders (po_id, alert_id, sup_id, ingredient_id, order_qty, unit_price, "
           "total_cost, status, created_by, created_at) VALUES (?, 1, 'NCC_CHINH', 'BO_LAT', 15, 250000, 3750000, 'DRAFT', 'x', 'x')")
    conn.execute(sql, ("PO-T1",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("PO-T2",))  # ux_po_alert chặn PO thứ 2 cho cùng cảnh báo
    # Đóng PO thứ nhất (từ chối) thì được tạo PO mới
    conn.execute("UPDATE purchase_orders SET closed_reason = 'REJECTED' WHERE po_id = 'PO-T1'")
    conn.execute(sql, ("PO-T2",))


def test_event_type_la_bi_chan(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO audit_logs (created_at, actor, event_type) VALUES ('x', 'x', 'HACK')")


def test_qty_am_bi_chan(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE inventory_batches SET qty = -1 WHERE batch_id = 'TRUNG-L1'")


# ---------- P02: dữ liệu mẫu ----------
def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_so_luong_du_lieu(conn):
    assert count(conn, "ingredients") == 12
    assert conn.execute("SELECT COUNT(DISTINCT cake_name) FROM recipes").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(DISTINCT sup_id) FROM suppliers").fetchone()[0] == 3
    assert 6 <= count(conn, "pre_orders") <= 8


def test_kem_tuoi_hai_lo_7_va_3(conn):
    rows = conn.execute("SELECT batch_id, qty, expiry_date FROM inventory_batches "
                        "WHERE ingredient_id='KEM_TUOI' ORDER BY expiry_date").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("KEM-L1", 7), ("KEM-L2", 3)]
    assert rows[0][2] == "2026-11-20 20:00:00"  # hết hạn 20:00 tối nay
    assert rows[1][2].startswith("2026-11-25")  # còn hạn 5 ngày


def test_bo_lat_po_004_qua_han(conn):
    po = conn.execute("SELECT status, delivery_status, expected_delivery_date, order_qty, sup_id "
                      "FROM purchase_orders WHERE po_id='PO-004'").fetchone()
    assert po == ("SENT", "PENDING", "2026-11-19", 20, "NCC_CHINH")  # rà soát sẽ đổi thành DELAYED
    assert conn.execute("SELECT SUM(qty) FROM inventory_batches WHERE ingredient_id='BO_LAT'").fetchone()[0] == 5


def test_trung_hai_lo_60_va_90(conn):
    rows = conn.execute("SELECT qty, expiry_date FROM inventory_batches "
                        "WHERE ingredient_id='TRUNG' ORDER BY expiry_date").fetchall()
    assert [r[0] for r in rows] == [60, 90]
    assert rows[0][1].startswith("2026-11-23") and rows[1][1].startswith("2026-11-30")


def test_mascarpone_chi_mot_ncc(conn):
    assert conn.execute("SELECT COUNT(*) FROM suppliers WHERE ingredient_id='MASCARPONE'").fetchone()[0] == 1


def test_ncc_hoa_toc_co_mot_dong_trong_email(conn):
    rows = conn.execute("SELECT ingredient_id FROM suppliers "
                        "WHERE sup_id='NCC_HOA_TOC' AND contact_email IS NULL").fetchall()
    assert len(rows) == 1


def test_khong_co_po_dang_mo(conn):
    # Bộ dữ liệu ban đầu không có PO DRAFT/APPROVED, để không sinh mã OPEN_PO ngoài ý muốn
    assert conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('DRAFT','APPROVED')").fetchone()[0] == 0


def test_scenario_no_shortage(tmp_path):
    c = sqlite3.connect(init_db(tmp_path / "ns.db", "no_shortage", now=NOW))
    assert c.execute("SELECT COUNT(*) FROM purchase_orders WHERE po_id='PO-004'").fetchone()[0] == 0
    kem = c.execute("SELECT MIN(expiry_date) FROM inventory_batches WHERE ingredient_id='KEM_TUOI'").fetchone()[0]
    assert kem > "2026-11-23"  # không lô kem nào hết hạn trong 3 ngày
    c.close()
