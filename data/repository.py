"""data/repository.py - Cac ham doc/ghi SQLite cho bakery.db (viec P03, Phuong Anh).

Nguyen tac:
- File nay CHI doc/ghi du lieu, KHONG chua logic nghiep vu
  (khong tinh projected_stock, khong kiem guardrail, khong chon A/B).
- Moi ham nhan tham so `conn` (tuy chon). Neu khong truyen, ham tu mo ket noi,
  tu commit va tu dong. Neu truyen `conn` (vd audit_service dung BEGIN IMMEDIATE,
  inventory_service xuat nhieu lo trong 1 giao dich) thi ham KHONG commit,
  nguoi goi tu commit/rollback.
- Ket qua tra ve la dict (1 dong) hoac list[dict] (nhieu dong).
- Duong dan DB: bien moi truong BAKERY_DB_PATH, mac dinh data/bakery.db.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "bakery.db"


# ---------------------------------------------------------------------------
# Ket noi
# ---------------------------------------------------------------------------

def get_db_path() -> str:
    return os.environ.get("BAKERY_DB_PATH", str(DEFAULT_DB_PATH))


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Mo ket noi moi toi bakery.db; dong tra ve dang dict-like (sqlite3.Row)."""
    conn = sqlite3.connect(db_path or get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _use(conn: Optional[sqlite3.Connection]) -> Iterator[sqlite3.Connection]:
    """Dung conn cua nguoi goi neu co; neu khong thi tu mo, tu commit, tu dong."""
    if conn is not None:
        yield conn
        return
    own = get_connection()
    try:
        yield own
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur: sqlite3.Cursor) -> Optional[dict]:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def _to_db_time(value: Any) -> str:
    """Doi date/datetime/str thanh chuoi ISO 'YYYY-MM-DDTHH:MM:SS' theo contracts.md
    (gio Viet Nam, khong ghi mui gio)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat() + "T00:00:00"
    text = str(value).strip().replace(" ", "T", 1)
    if len(text) == 10:  # chi co ngay
        text += "T00:00:00"
    return text[:19]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _pk_column(conn: sqlite3.Connection, table: str) -> Optional[str]:
    for r in conn.execute(f"PRAGMA table_info({table})"):
        if r["pk"] == 1:
            return r["name"]
    return None


def _insert(conn: sqlite3.Connection, table: str, data: dict):
    """Them 1 dong; tra gia tri KHOA CHINH cua dong vua them
    (so tu tang hoac ma chu nhu 'KEM-L1', tuy cach dat trong init_db)."""
    cols = _table_columns(conn, table)
    unknown = set(data) - cols
    if unknown:
        raise ValueError(f"Bang {table} khong co cot: {sorted(unknown)}")
    keys = list(data)
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
    cur = conn.execute(sql, [data[k] for k in keys])
    pk = _pk_column(conn, table)
    if pk is None:
        return cur.lastrowid
    if data.get(pk) is not None:
        return data[pk]
    row = conn.execute(f"SELECT {pk} FROM {table} WHERE rowid = ?", (cur.lastrowid,)).fetchone()
    return row[0] if row is not None else cur.lastrowid


def _next_batch_id(conn: sqlite3.Connection, ingredient_id) -> str:
    """Sinh ma lo moi theo kieu du lieu mau: lay tien to cua lo cu (vd 'TRUNG-L2' -> 'TRUNG'),
    tang so thu tu -> 'TRUNG-L3'. Chua co lo nao thi dung '<ingredient_id>-L1'."""
    existing = [r[0] for r in conn.execute(
        "SELECT batch_id FROM inventory_batches WHERE ingredient_id = ?", (ingredient_id,))
        if r[0] is not None]
    prefix = str(ingredient_id)
    max_n = 0
    for bid in existing:
        text = str(bid)
        if "-L" in text:
            head, _, tail = text.rpartition("-L")
            if tail.isdigit():
                prefix = head
                max_n = max(max_n, int(tail))
    n = max_n + 1
    taken = {str(b) for b in existing}
    while f"{prefix}-L{n}" in taken or conn.execute(
            "SELECT 1 FROM inventory_batches WHERE batch_id = ?", (f"{prefix}-L{n}",)).fetchone():
        n += 1
    return f"{prefix}-L{n}"


def _batch_id_is_integer(conn: sqlite3.Connection) -> bool:
    for r in conn.execute("PRAGMA table_info(inventory_batches)"):
        if r["name"] == "batch_id":
            return "INT" in (r["type"] or "").upper()
    return True


def _update(conn: sqlite3.Connection, table: str, pk_col: str, pk_val: Any, fields: dict) -> int:
    if not fields:
        return 0
    cols = _table_columns(conn, table)
    unknown = set(fields) - cols
    if unknown:
        raise ValueError(f"Bang {table} khong co cot: {sorted(unknown)}")
    if pk_col in fields:
        raise ValueError(f"Khong duoc sua khoa chinh {pk_col}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE {table} SET {sets} WHERE {pk_col} = ?", [*fields.values(), pk_val]
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# 1. ingredients
# ---------------------------------------------------------------------------

def get_ingredients(conn=None) -> list[dict]:
    with _use(conn) as c:
        return _rows(c.execute("SELECT * FROM ingredients ORDER BY id"))


def get_ingredient(ingredient_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)))


def get_ingredient_by_name(name: str, conn=None) -> Optional[dict]:
    """Tim theo ten (khong phan biet hoa thuong, cho phep go 1 phan ten)."""
    with _use(conn) as c:
        r = _row(c.execute("SELECT * FROM ingredients WHERE name = ?", (name,)))
        if r is None:
            r = _row(c.execute(
                "SELECT * FROM ingredients WHERE name LIKE ? ORDER BY id LIMIT 1",
                (f"%{name}%",),
            ))
        return r


# ---------------------------------------------------------------------------
# 2. inventory_batches
# ---------------------------------------------------------------------------

def get_batches(ingredient_id, conn=None) -> list[dict]:
    """Tat ca lo cua 1 nguyen lieu (ke ca lo het han, lo qty = 0), sap theo FEFO."""
    with _use(conn) as c:
        return _rows(c.execute(
            """SELECT * FROM inventory_batches
               WHERE ingredient_id = ?
               ORDER BY datetime(expiry_date), datetime(received_at), batch_id""",
            (ingredient_id,),
        ))


def get_batch(batch_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM inventory_batches WHERE batch_id = ?", (batch_id,)))


def get_batches_fefo(ingredient_id, at, conn=None) -> list[dict]:
    """HAM DUNG CHUNG FEFO (fefo.py va inventory_service.py cung goi).

    Tra cac lo CON HAN tai thoi diem `at` (expiry_date > at) va con hang (qty > 0),
    sap theo expiry_date tang dan, roi received_at tang dan.
    `at`: datetime, date hoac chuoi 'YYYY-MM-DD[ HH:MM:SS]'.
    """
    at_text = _to_db_time(at)
    with _use(conn) as c:
        return _rows(c.execute(
            """SELECT * FROM inventory_batches
               WHERE ingredient_id = ?
                 AND qty > 0
                 AND datetime(expiry_date) > datetime(?)
               ORDER BY datetime(expiry_date), datetime(received_at), batch_id""",
            (ingredient_id, at_text),
        ))


def insert_batch(ingredient_id, qty, expiry_date, received_at, po_id=None,
                 batch_id=None, conn=None):
    """Tao lo moi; tra batch_id. Neu khong truyen batch_id va cot batch_id la chu,
    ham tu sinh ma tiep theo (vd 'TRUNG-L3')."""
    with _use(conn) as c:
        data = {
            "ingredient_id": ingredient_id,
            "qty": qty,
            "expiry_date": _to_db_time(expiry_date),
            "received_at": _to_db_time(received_at),
            "po_id": po_id,
        }
        if batch_id is None and not _batch_id_is_integer(c):
            batch_id = _next_batch_id(c, ingredient_id)
        if batch_id is not None:
            data["batch_id"] = batch_id
        return _insert(c, "inventory_batches", data)


def update_batch_qty(batch_id, new_qty, conn=None) -> int:
    """Dat lai so luong cua 1 lo; tra so dong bi sua (0 neu khong co lo)."""
    with _use(conn) as c:
        return _update(c, "inventory_batches", "batch_id", batch_id, {"qty": new_qty})


# ---------------------------------------------------------------------------
# 3. recipes  &  4. pre_orders
# ---------------------------------------------------------------------------

def get_recipes(cake_name=None, conn=None) -> list[dict]:
    with _use(conn) as c:
        if cake_name is None:
            return _rows(c.execute("SELECT * FROM recipes ORDER BY cake_name, ingredient_id"))
        return _rows(c.execute(
            "SELECT * FROM recipes WHERE cake_name = ? ORDER BY ingredient_id", (cake_name,)
        ))


def get_recipes_by_ingredient(ingredient_id, conn=None) -> list[dict]:
    with _use(conn) as c:
        return _rows(c.execute(
            "SELECT * FROM recipes WHERE ingredient_id = ? ORDER BY cake_name", (ingredient_id,)
        ))


def get_pre_orders(from_date=None, to_date=None, conn=None) -> list[dict]:
    """Don dat truoc, loc theo delivery_date trong [from_date, to_date] neu co."""
    sql = "SELECT * FROM pre_orders WHERE 1 = 1"
    params: list = []
    if from_date is not None:
        sql += " AND date(delivery_date) >= date(?)"
        params.append(_to_db_time(from_date)[:10])
    if to_date is not None:
        sql += " AND date(delivery_date) <= date(?)"
        params.append(_to_db_time(to_date)[:10])
    sql += " ORDER BY delivery_date, order_id"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


def get_pre_order_needs(ingredient_id, from_date=None, to_date=None, conn=None) -> list[dict]:
    """Moi dong: 1 don dat truoc can nguyen lieu nay, kem qty_per_cake va need_qty
    (= quantity * qty_per_cake). Chi la phep nhan theo dinh luong, chua tinh nghiep vu."""
    sql = """SELECT p.*, r.ingredient_id, r.qty_per_cake,
                    p.quantity * r.qty_per_cake AS need_qty
             FROM pre_orders p
             JOIN recipes r ON r.cake_name = p.cake_name
             WHERE r.ingredient_id = ?"""
    params: list = [ingredient_id]
    if from_date is not None:
        sql += " AND date(p.delivery_date) >= date(?)"
        params.append(_to_db_time(from_date)[:10])
    if to_date is not None:
        sql += " AND date(p.delivery_date) <= date(?)"
        params.append(_to_db_time(to_date)[:10])
    sql += " ORDER BY p.delivery_date, p.order_id"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


# ---------------------------------------------------------------------------
# 5. suppliers
# ---------------------------------------------------------------------------

def get_suppliers(ingredient_id=None, active_only=False, conn=None) -> list[dict]:
    sql = "SELECT * FROM suppliers WHERE 1 = 1"
    params: list = []
    if ingredient_id is not None:
        sql += " AND ingredient_id = ?"
        params.append(ingredient_id)
    if active_only:
        sql += " AND active = 1"
    sql += " ORDER BY sup_id"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


def get_supplier(sup_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM suppliers WHERE sup_id = ?", (sup_id,)))


# ---------------------------------------------------------------------------
# 6. purchase_orders
# ---------------------------------------------------------------------------

def get_purchase_orders(status=None, ingredient_id=None, open_only=False, conn=None) -> list[dict]:
    """open_only=True: PO chua dong (closed_reason IS NULL) va chua nhan hang."""
    sql = "SELECT * FROM purchase_orders WHERE 1 = 1"
    params: list = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if ingredient_id is not None:
        sql += " AND ingredient_id = ?"
        params.append(ingredient_id)
    if open_only:
        sql += " AND closed_reason IS NULL AND (delivery_status IS NULL OR delivery_status != 'RECEIVED')"
    sql += " ORDER BY po_id"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


def get_open_purchase_orders(ingredient_id=None, conn=None) -> list[dict]:
    return get_purchase_orders(ingredient_id=ingredient_id, open_only=True, conn=conn)


def get_purchase_order(po_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)))


def get_purchase_order_by_alert(alert_id, conn=None) -> Optional[dict]:
    """PO dang mo cua 1 canh bao (moi canh bao toi da 1 PO mo - ux_po_alert)."""
    with _use(conn) as c:
        return _row(c.execute(
            "SELECT * FROM purchase_orders WHERE alert_id = ? AND closed_reason IS NULL",
            (alert_id,),
        ))


def insert_purchase_order(data: dict, conn=None):
    """Them 1 PO. `data` la dict {ten_cot: gia_tri}. Tra po_id
    (lay tu data neu co, neu khong thi rowid do SQLite cap)."""
    with _use(conn) as c:
        rowid = _insert(c, "purchase_orders", data)
        return data.get("po_id", rowid)


def update_purchase_order(po_id, fields: dict, conn=None) -> int:
    with _use(conn) as c:
        return _update(c, "purchase_orders", "po_id", po_id, fields)


# ---------------------------------------------------------------------------
# 7. audit_runs
# ---------------------------------------------------------------------------

def get_audit_runs(run_date=None, status=None, conn=None) -> list[dict]:
    sql = "SELECT * FROM audit_runs WHERE 1 = 1"
    params: list = []
    if run_date is not None:
        sql += " AND date(run_date) = date(?)"
        params.append(_to_db_time(run_date)[:10])
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY run_id DESC"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


def get_audit_run(run_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM audit_runs WHERE run_id = ?", (run_id,)))


def get_running_audit_run(conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM audit_runs WHERE status = 'RUNNING' LIMIT 1"))


def get_latest_audit_run(run_date=None, conn=None) -> Optional[dict]:
    runs = get_audit_runs(run_date=run_date, conn=conn)
    return runs[0] if runs else None


def insert_audit_run(data: dict, conn=None) -> int:
    with _use(conn) as c:
        return _insert(c, "audit_runs", data)


def update_audit_run(run_id, fields: dict, conn=None) -> int:
    with _use(conn) as c:
        return _update(c, "audit_runs", "run_id", run_id, fields)


# ---------------------------------------------------------------------------
# 8. alerts
# ---------------------------------------------------------------------------

def get_alerts(run_id=None, status=None, ingredient_id=None, conn=None) -> list[dict]:
    sql = "SELECT * FROM alerts WHERE 1 = 1"
    params: list = []
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if ingredient_id is not None:
        sql += " AND ingredient_id = ?"
        params.append(ingredient_id)
    sql += " ORDER BY alert_id"
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


def get_alert(alert_id, conn=None) -> Optional[dict]:
    with _use(conn) as c:
        return _row(c.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)))


def insert_alert(data: dict, conn=None) -> int:
    """Them canh bao. options_json / causes neu la list/dict se tu doi sang chuoi JSON."""
    data = dict(data)
    for key in ("options_json", "causes"):
        if key in data and not isinstance(data[key], (str, type(None))):
            data[key] = json.dumps(data[key], ensure_ascii=False)
    with _use(conn) as c:
        return _insert(c, "alerts", data)


def update_alert(alert_id, fields: dict, conn=None) -> int:
    with _use(conn) as c:
        return _update(c, "alerts", "alert_id", alert_id, fields)


# ---------------------------------------------------------------------------
# 9. audit_logs (chi DOC; GHI thi dung services/audit_log.write_audit_log cua Duyen)
# ---------------------------------------------------------------------------

def get_audit_logs(event_type=None, run_id=None, entity_type=None, entity_id=None,
                   limit=None, conn=None) -> list[dict]:
    sql = "SELECT * FROM audit_logs WHERE 1 = 1"
    params: list = []
    for col, val in (("event_type", event_type), ("run_id", run_id),
                     ("entity_type", entity_type), ("entity_id", entity_id)):
        if val is not None:
            sql += f" AND {col} = ?"
            params.append(val)
    sql += " ORDER BY log_id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _use(conn) as c:
        return _rows(c.execute(sql, params))


# ---------------------------------------------------------------------------
# compute_snapshot_hash  (M05 luu vao alerts, D05 so lai truoc khi tao PO)
# ---------------------------------------------------------------------------

def compute_snapshot_hash(ingredient_id, conn=None) -> str:
    """Bam SHA-256 cac du lieu anh huong toi 1 nguyen lieu:
    ton theo lo, don dat truoc dung nguyen lieu nay, PO dang mo, NCC cua nguyen lieu.
    Bat ky thay doi nao trong cac du lieu do -> chuoi hash khac.
    """
    with _use(conn) as c:
        ingredient = _row(c.execute(
            "SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)))
        batches = _rows(c.execute(
            "SELECT * FROM inventory_batches WHERE ingredient_id = ? ORDER BY batch_id",
            (ingredient_id,)))
        pre_orders = _rows(c.execute(
            """SELECT p.*, r.qty_per_cake FROM pre_orders p
               JOIN recipes r ON r.cake_name = p.cake_name
               WHERE r.ingredient_id = ? ORDER BY p.order_id""",
            (ingredient_id,)))
        open_pos = _rows(c.execute(
            """SELECT * FROM purchase_orders
               WHERE ingredient_id = ? AND closed_reason IS NULL
               ORDER BY po_id""",
            (ingredient_id,)))
        suppliers = _rows(c.execute(
            "SELECT * FROM suppliers WHERE ingredient_id = ? ORDER BY sup_id",
            (ingredient_id,)))

    snapshot = {
        "ingredient": ingredient,
        "batches": batches,
        "pre_orders": pre_orders,
        "open_pos": open_pos,
        "suppliers": suppliers,
    }
    text = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
