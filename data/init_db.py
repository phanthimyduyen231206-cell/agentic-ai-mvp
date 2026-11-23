"""
data/init_db.py - Tạo cơ sở dữ liệu SQLite bakery.db và nạp dữ liệu mẫu.

Người sở hữu: Phương Anh (việc P01 + P02).
Căn cứ: Mục 5 và Mục 7 bản đặc tả.

Cách chạy (đứng ở thư mục gốc của repo):
    python data/init_db.py                         # bộ dữ liệu chuẩn (đủ các kịch bản thiếu hàng)
    python data/init_db.py --scenario no_shortage  # bộ dữ liệu đủ hàng (dùng cho TC01)
    python data/init_db.py --db duong_dan_khac.db  # ghi ra file khác (dùng trong test)

Mỗi lần chạy sẽ XÓA file .db cũ rồi tạo lại từ đầu, nên chạy bao nhiêu lần cũng được.
"""

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Thêm thư mục gốc của repo vào sys.path để import được services/clock.py
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "bakery.db"


# ---------------------------------------------------------------------------
# 1. Lấy "bây giờ" từ services/clock.py (D03): có DEMO_DATE thì dùng giờ demo, không thì giờ thật.
# ---------------------------------------------------------------------------
def get_now() -> datetime:
    from services.clock import now

    return now()


# ---------------------------------------------------------------------------
# 2. SCHEMA: 9 bảng theo Mục 7
# ---------------------------------------------------------------------------
# Danh sách mã sự kiện dùng chung với services/audit_log.py (D04), không giữ bản riêng ở đây:
# thêm mã mới (vd CHAT_FAILED) vào audit_log.py thì ràng buộc CHECK của bảng audit_logs tự cập nhật
# khi chạy lại init_db.
from services.audit_log import EVENT_TYPES  # noqa: E402  (cần sys.path ở trên)

_EVENT_LIST = ", ".join(f"'{e}'" for e in EVENT_TYPES)

SCHEMA_SQL = f"""
-- 1. Danh mục nguyên liệu
CREATE TABLE ingredients (
    id              TEXT PRIMARY KEY,                       -- vd 'BOT_MI'
    name            TEXT NOT NULL,
    unit            TEXT NOT NULL,                          -- kg, lít, quả, ml
    min_stock       REAL NOT NULL CHECK (min_stock >= 0),
    shelf_life_days INTEGER NOT NULL CHECK (shelf_life_days > 0),
    daily_usage     REAL NOT NULL DEFAULT 0 CHECK (daily_usage >= 0)  -- đơn vị nguyên liệu / ngày
);

-- 2. Tồn kho theo lô (FEFO: expiry_date tăng dần, cùng hạn thì received_at sớm hơn trước)
CREATE TABLE inventory_batches (
    batch_id      TEXT PRIMARY KEY,
    ingredient_id TEXT NOT NULL REFERENCES ingredients(id),
    qty           REAL NOT NULL CHECK (qty >= 0),
    expiry_date   TEXT NOT NULL,                            -- 'YYYY-MM-DDTHH:MM:SS'
    received_at   TEXT NOT NULL,                            -- 'YYYY-MM-DDTHH:MM:SS'
    po_id         TEXT REFERENCES purchase_orders(po_id)    -- NULL nếu là tồn đầu kỳ
);

-- 3. Công thức: 1 dòng = 1 nguyên liệu của 1 loại bánh
CREATE TABLE recipes (
    cake_name     TEXT NOT NULL,
    ingredient_id TEXT NOT NULL REFERENCES ingredients(id),
    qty_per_cake  REAL NOT NULL CHECK (qty_per_cake > 0),
    PRIMARY KEY (cake_name, ingredient_id)
);

-- 4. Đơn khách đặt trước
CREATE TABLE pre_orders (
    order_id      TEXT PRIMARY KEY,
    cake_name     TEXT NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    delivery_date TEXT NOT NULL                             -- 'YYYY-MM-DD'
);

-- 5. Nhà cung cấp: mỗi dòng là 1 NCC cho 1 nguyên liệu
CREATE TABLE suppliers (
    sup_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    ingredient_id   TEXT NOT NULL REFERENCES ingredients(id),
    price           REAL NOT NULL CHECK (price > 0),        -- đơn giá (VND / đơn vị nguyên liệu)
    lead_time_hours INTEGER NOT NULL CHECK (lead_time_hours > 0),
    moq             REAL NOT NULL CHECK (moq > 0),
    reliability     REAL NOT NULL CHECK (reliability BETWEEN 0 AND 1),
    contact_email   TEXT,                                   -- NULL = không gửi PO được
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (sup_id, ingredient_id)
);

-- 6. Đơn mua hàng (PO). Chỉ 3 trạng thái; từ chối/hủy = đóng PO bằng closed_reason
CREATE TABLE purchase_orders (
    po_id                  TEXT PRIMARY KEY,                -- vd 'PO-004'
    alert_id               INTEGER REFERENCES alerts(alert_id),
    sup_id                 TEXT NOT NULL,
    ingredient_id          TEXT NOT NULL REFERENCES ingredients(id),
    order_qty              REAL NOT NULL CHECK (order_qty > 0),
    unit_price             REAL NOT NULL CHECK (unit_price > 0),
    total_cost             REAL NOT NULL CHECK (total_cost >= 0),
    status                 TEXT NOT NULL CHECK (status IN ('DRAFT', 'APPROVED', 'SENT')),
    delivery_status        TEXT CHECK (delivery_status IN ('PENDING', 'DELAYED', 'RECEIVED')
                                       OR delivery_status IS NULL),
    expected_delivery_date TEXT,
    received_qty           REAL NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
    created_by             TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    approved_by            TEXT,
    approved_at            TEXT,
    sent_by                TEXT,
    sent_at                TEXT,
    send_attempts          INTEGER NOT NULL DEFAULT 0,
    last_send_error        TEXT,
    closed_reason          TEXT CHECK (closed_reason IN ('REJECTED', 'CANCELLED')
                                       OR closed_reason IS NULL),
    closed_by              TEXT,
    closed_at              TEXT,
    -- DELAYED/PENDING/RECEIVED là trạng thái giao hàng, chỉ có khi PO đã SENT
    CHECK (delivery_status IS NULL OR status = 'SENT'),
    FOREIGN KEY (sup_id, ingredient_id) REFERENCES suppliers(sup_id, ingredient_id)
);

-- 7. Phiên rà soát
CREATE TABLE audit_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,                              -- 'YYYY-MM-DD'
    "trigger"   TEXT NOT NULL CHECK ("trigger" IN ('SCHEDULED', 'MANUAL')),
    is_rerun    INTEGER NOT NULL DEFAULT 0 CHECK (is_rerun IN (0, 1)),
    status      TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    alert_count INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

-- 8. Cảnh báo thiếu hàng (điểm dừng 1 của quản lý)
CREATE TABLE alerts (
    alert_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES audit_runs(run_id),
    ingredient_id   TEXT NOT NULL REFERENCES ingredients(id),
    event_type      TEXT NOT NULL DEFAULT 'SHORTAGE',
    status          TEXT NOT NULL CHECK (status IN ('OPEN', 'SELECTED', 'RESOLVED', 'DISMISSED')),
    main_cause      TEXT,
    causes          TEXT NOT NULL DEFAULT '[]',             -- JSON:[{{"code": "EXPIRING_LOT", "qty": 6.0}}, ...]
    projected_stock REAL,
    shortage_qty    REAL,
    options_json    TEXT NOT NULL DEFAULT '[]',             -- JSON phương án A/B do calc.py tính
    explanation     TEXT,
    snapshot_hash   TEXT,
    selected_option TEXT CHECK (selected_option IN ('A', 'B') OR selected_option IS NULL),
    selected_qty    REAL,
    selected_by     TEXT,
    selected_at     TEXT,
    po_id           TEXT REFERENCES purchase_orders(po_id),
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);

-- 9. Nhật ký
CREATE TABLE audit_logs (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    actor       TEXT NOT NULL,                              -- 'SYSTEM', 'SCHEDULER', tên quản lý...
    event_type  TEXT NOT NULL CHECK (event_type IN ({_EVENT_LIST})),
    entity_type TEXT,
    entity_id   TEXT,
    run_id      INTEGER REFERENCES audit_runs(run_id),
    detail_json TEXT
);

-- Ràng buộc chống trùng (Mục 7, Mục 9)
CREATE UNIQUE INDEX ux_run_running ON audit_runs(status) WHERE status = 'RUNNING';
CREATE UNIQUE INDEX ux_po_alert    ON purchase_orders(alert_id) WHERE closed_reason IS NULL;

-- Index cho truy vấn hay dùng
CREATE INDEX ix_batches_fefo ON inventory_batches(ingredient_id, expiry_date, received_at);
CREATE INDEX ix_preorders_date ON pre_orders(delivery_date);
CREATE INDEX ix_po_ingredient ON purchase_orders(ingredient_id, status);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


# ---------------------------------------------------------------------------
# 3. DỮ LIỆU MẪU (P02)
# ---------------------------------------------------------------------------
# (id, tên, đơn vị, min_stock, shelf_life_days, daily_usage)
INGREDIENTS = [
    ("BOT_MI",      "Bột mì",                   "kg",  10,  180, 12),
    ("BO_LAT",      "Bơ lạt Anchor",            "kg",  3,   60,  1),
    ("KEM_TUOI",    "Kem tươi Whipping Cream",  "lít", 2,   7,   1),
    ("MASCARPONE",  "Phô mai Mascarpone",       "kg",  0.5, 14,  0.2),
    ("TRUNG",       "Trứng gà",                 "quả", 30,  21,  20),
    ("DUONG",       "Đường cát trắng",          "kg",  5,   365, 2),
    ("SUA_TUOI",    "Sữa tươi không đường",     "lít", 4,   7,   3),
    ("MEN_NO",      "Men nở",                   "kg",  0.5, 180, 0.1),
    ("BOT_CACAO",   "Bột cacao",                "kg",  0.5, 365, 0.1),
    ("SOCOLA",      "Socola đen",               "kg",  1,   180, 0.5),
    ("VANI",        "Tinh chất vani",           "ml",  100, 365, 20),
    ("PHO_MAI_KEM", "Cream cheese",             "kg",  2,   30,  0.5),
]

# (tên bánh, nguyên liệu, lượng cho 1 bánh)
RECIPES = [
    ("Bánh kem sinh nhật", "BOT_MI", 0.1), ("Bánh kem sinh nhật", "TRUNG", 2),
    ("Bánh kem sinh nhật", "DUONG", 0.08), ("Bánh kem sinh nhật", "KEM_TUOI", 0.2),
    ("Bánh kem sinh nhật", "SUA_TUOI", 0.05), ("Bánh kem sinh nhật", "VANI", 5),

    ("Croissant", "BOT_MI", 0.025), ("Croissant", "BO_LAT", 0.12),
    ("Croissant", "MEN_NO", 0.002), ("Croissant", "DUONG", 0.01),
    ("Croissant", "SUA_TUOI", 0.02),

    ("Tiramisu", "MASCARPONE", 0.1), ("Tiramisu", "TRUNG", 1),
    ("Tiramisu", "DUONG", 0.03), ("Tiramisu", "BOT_CACAO", 0.01),

    ("Bánh mì sữa", "BOT_MI", 0.08), ("Bánh mì sữa", "SUA_TUOI", 0.05),
    ("Bánh mì sữa", "MEN_NO", 0.002), ("Bánh mì sữa", "DUONG", 0.02),
    ("Bánh mì sữa", "TRUNG", 1), ("Bánh mì sữa", "BO_LAT", 0.01),

    ("Cheesecake", "PHO_MAI_KEM", 0.25), ("Cheesecake", "TRUNG", 2),
    ("Cheesecake", "DUONG", 0.05), ("Cheesecake", "SOCOLA", 0.03),
]

# (mã đơn, tên bánh, số lượng, giao sau bao nhiêu ngày kể từ hôm nay)
# Chỉ 3 đơn đầu rơi vào cửa sổ rà soát H = 3 ngày (ngày 0, 1, 2).
# Các đơn còn lại giao từ ngày +5 trở đi để không ảnh hưởng số liệu kịch bản.
PRE_ORDERS = [
    ("DH01", "Bánh kem sinh nhật", 15, 1),   # cần 3 lít kem tươi ngày mai
    ("DH02", "Croissant",          100, 1),  # cần 12 kg bơ sáng mai
    ("DH03", "Tiramisu",           10, 2),   # cần 1 kg mascarpone
    ("DH04", "Bánh mì sữa",        50, 5),
    ("DH05", "Cheesecake",         8,  6),
    ("DH06", "Croissant",          60, 6),
    ("DH07", "Bánh kem sinh nhật", 10, 7),
]

NCC_CHINH = ("NCC_CHINH", "NCC Chính", 24, "order@ncc-chinh.vn")
NCC_HOA_TOC = ("NCC_HOA_TOC", "NCC Hỏa tốc", 2, "order@hoatoc.vn")
NCC_ANH_DUONG = ("NCC_ANH_DUONG", "NCC Ánh Dương", 48, "order@anhduong.vn")

# (NCC, nguyên liệu, đơn giá, moq, reliability, email_override)
# email_override = "" nghĩa là để trống contact_email (mô phỏng gửi thất bại, TC11)
SUPPLIER_ROWS = [
    (NCC_CHINH, "BOT_MI", 20000, 10, 0.95, None),
    (NCC_ANH_DUONG, "BOT_MI", 18000, 25, 0.85, None),
    (NCC_HOA_TOC, "BOT_MI", 23000, 5, 0.90, ""),          # <- trống email (TC11)

    (NCC_CHINH, "BO_LAT", 250000, 5, 0.95, None),         # Bơ lạt: đúng 2 NCC hợp lệ (TC04)
    (NCC_HOA_TOC, "BO_LAT", 287500, 2, 0.90, None),

    (NCC_CHINH, "KEM_TUOI", 90000, 2, 0.95, None),
    (NCC_HOA_TOC, "KEM_TUOI", 103500, 1, 0.90, None),
    (NCC_ANH_DUONG, "KEM_TUOI", 82000, 5, 0.85, None),

    (NCC_CHINH, "MASCARPONE", 300000, 1, 0.95, None),     # Mascarpone: chỉ 1 NCC (TC12)

    (NCC_CHINH, "TRUNG", 3500, 30, 0.95, None),
    (NCC_ANH_DUONG, "TRUNG", 3200, 60, 0.85, None),

    (NCC_CHINH, "DUONG", 22000, 10, 0.95, None),
    (NCC_ANH_DUONG, "DUONG", 20000, 25, 0.85, None),
    (NCC_CHINH, "SUA_TUOI", 32000, 5, 0.95, None),
    (NCC_CHINH, "MEN_NO", 150000, 0.5, 0.95, None),
    (NCC_CHINH, "BOT_CACAO", 180000, 1, 0.95, None),
    (NCC_CHINH, "SOCOLA", 220000, 1, 0.95, None),
    (NCC_CHINH, "VANI", 800, 100, 0.95, None),
    (NCC_CHINH, "PHO_MAI_KEM", 260000, 1, 0.95, None),
]


def _dt(d: date, hh: int = 0, mm: int = 0) -> str:
    """Đổi ngày + giờ thành chuỗi ISO 'YYYY-MM-DDTHH:MM:SS' (có chữ T, theo docs/contracts.md)."""
    return datetime.combine(d, time(hh, mm)).isoformat(timespec="seconds")


def build_batches(today: date, scenario: str):
    """Trả về danh sách lô: (batch_id, ingredient_id, qty, expiry_date, received_at, po_id)."""
    d = lambda n: today + timedelta(days=n)  # noqa: E731  (ngày hôm nay + n)

    if scenario == "standard":
        kem = [
            ("KEM-L1", "KEM_TUOI", 7, _dt(d(0), 20, 0), _dt(d(-6), 9), None),  # hết hạn 20:00 tối nay
            ("KEM-L2", "KEM_TUOI", 3, _dt(d(5), 20, 0), _dt(d(-1), 9), None),  # còn hạn 5 ngày
        ]
        bo = [("BO-L1", "BO_LAT", 5, _dt(d(30), 23, 59), _dt(d(-20), 9), None)]
        bot_mi = [
            ("BOTMI-L1", "BOT_MI", 25, _dt(d(60), 23, 59), _dt(d(-30), 9), "PO-001"),
            ("BOTMI-L2", "BOT_MI", 15, _dt(d(120), 23, 59), _dt(d(-10), 9), None),
        ]
        mascarpone = [("MASC-L1", "MASCARPONE", 1, _dt(d(10), 23, 59), _dt(d(-4), 9), None)]
    else:  # no_shortage: đủ hàng, không lô nào hết hạn trong 3 ngày
        kem = [
            ("KEM-L1", "KEM_TUOI", 7, _dt(d(6), 20, 0), _dt(d(-1), 9), None),
            ("KEM-L2", "KEM_TUOI", 3, _dt(d(5), 20, 0), _dt(d(-1), 9), None),
        ]
        bo = [("BO-L1", "BO_LAT", 25, _dt(d(30), 23, 59), _dt(d(-20), 9), None)]
        bot_mi = [
            ("BOTMI-L1", "BOT_MI", 50, _dt(d(60), 23, 59), _dt(d(-30), 9), "PO-001"),
            ("BOTMI-L2", "BOT_MI", 30, _dt(d(120), 23, 59), _dt(d(-10), 9), None),
        ]
        mascarpone = [("MASC-L1", "MASCARPONE", 3, _dt(d(10), 23, 59), _dt(d(-4), 9), None)]

    chung = [  # giống nhau ở cả 2 kịch bản
        ("TRUNG-L1", "TRUNG", 60, _dt(d(3), 23, 59), _dt(d(-18), 9), "PO-002"),  # hết hạn sau 3 ngày
        ("TRUNG-L2", "TRUNG", 90, _dt(d(10), 23, 59), _dt(d(-11), 9), None),     # hết hạn sau 10 ngày
        ("DUONG-L1", "DUONG", 25, _dt(d(200), 23, 59), _dt(d(-15), 9), None),
        ("SUA-L1", "SUA_TUOI", 20, _dt(d(5), 23, 59), _dt(d(-2), 9), "PO-003"),
        ("MEN-L1", "MEN_NO", 2, _dt(d(90), 23, 59), _dt(d(-30), 9), None),
        ("CACAO-L1", "BOT_CACAO", 2, _dt(d(200), 23, 59), _dt(d(-40), 9), None),
        ("SOCOLA-L1", "SOCOLA", 5, _dt(d(120), 23, 59), _dt(d(-20), 9), None),
        ("VANI-L1", "VANI", 500, _dt(d(300), 23, 59), _dt(d(-60), 9), None),
        ("PMK-L1", "PHO_MAI_KEM", 6, _dt(d(20), 23, 59), _dt(d(-5), 9), None),
    ]
    return kem + bo + bot_mi + mascarpone + chung


def build_purchase_orders(today: date, scenario: str):
    """PO lịch sử (đã nhận) + PO-004 giao trễ (chỉ có ở kịch bản chuẩn)."""
    d = lambda n: today + timedelta(days=n)  # noqa: E731
    cols = ("po_id", "sup_id", "ingredient_id", "order_qty", "unit_price", "total_cost",
            "status", "delivery_status", "expected_delivery_date", "received_qty",
            "created_by", "created_at", "approved_by", "approved_at", "sent_by", "sent_at")
    rows = [
        ("PO-001", "NCC_CHINH", "BOT_MI", 25, 20000, 500000, "SENT", "RECEIVED",
         d(-30).isoformat(), 25, "Duyên", _dt(d(-32), 8), "Duyên", _dt(d(-32), 9), "Duyên", _dt(d(-32), 9)),
        ("PO-002", "NCC_CHINH", "TRUNG", 60, 3500, 210000, "SENT", "RECEIVED",
         d(-18).isoformat(), 60, "Duyên", _dt(d(-20), 8), "Duyên", _dt(d(-20), 9), "Duyên", _dt(d(-20), 9)),
        ("PO-003", "NCC_CHINH", "SUA_TUOI", 20, 32000, 640000, "SENT", "RECEIVED",
         d(-2).isoformat(), 20, "Duyên", _dt(d(-4), 8), "Duyên", _dt(d(-4), 9), "Duyên", _dt(d(-4), 9)),
    ]
    if scenario == "standard":
        # Bẫy đơn nhập trễ: SENT, hạn giao = hôm qua, chưa nhận.
        # delivery_status để PENDING: chính audit_service (bước 3) sẽ đổi sang DELAYED khi rà soát (TC14).
        rows.append(
            ("PO-004", "NCC_CHINH", "BO_LAT", 20, 250000, 5000000, "SENT", "PENDING",
             d(-1).isoformat(), 0, "Duyên", _dt(d(-3), 8), "Duyên", _dt(d(-3), 9), "Duyên", _dt(d(-3), 9))
        )
    return cols, rows


def seed(conn: sqlite3.Connection, scenario: str, now: datetime) -> None:
    today = now.date()

    conn.executemany("INSERT INTO ingredients VALUES (?, ?, ?, ?, ?, ?)", INGREDIENTS)
    conn.executemany("INSERT INTO recipes VALUES (?, ?, ?)", RECIPES)
    conn.executemany(
        "INSERT INTO pre_orders VALUES (?, ?, ?, ?)",
        [(oid, cake, qty, (today + timedelta(days=n)).isoformat()) for oid, cake, qty, n in PRE_ORDERS],
    )

    sup_rows = []
    for (sup_id, name, lead, email), ing, price, moq, rel, email_override in SUPPLIER_ROWS:
        contact = None if email_override == "" else email
        sup_rows.append((sup_id, name, ing, price, lead, moq, rel, contact, 1))
    conn.executemany("INSERT INTO suppliers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sup_rows)

    # PO phải có trước lô (vì lô tham chiếu po_id)
    cols, po_rows = build_purchase_orders(today, scenario)
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(f"INSERT INTO purchase_orders ({', '.join(cols)}) VALUES ({placeholders})", po_rows)

    conn.executemany("INSERT INTO inventory_batches VALUES (?, ?, ?, ?, ?, ?)", build_batches(today, scenario))


# ---------------------------------------------------------------------------
# 4. Hàm chính
# ---------------------------------------------------------------------------
def init_db(db_path=DEFAULT_DB, scenario: str = "standard", now: datetime | None = None) -> Path:
    """Xóa DB cũ, tạo 9 bảng, nạp dữ liệu mẫu. Trả về đường dẫn file DB."""
    if scenario not in ("standard", "no_shortage"):
        raise ValueError(f"scenario không hợp lệ: {scenario}")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # xóa để tạo lại từ đầu

    now = now or get_now()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        seed(conn, scenario, now)
        conn.commit()
    finally:
        conn.close()
    return db_path


def load_env() -> None:
    """Đọc file .env (DEMO_DATE, BAKERY_DB_PATH...) như app.py và scheduler.py.

    Chỉ gọi khi chạy bằng lệnh, để dữ liệu mẫu dựng theo đúng DEMO_DATE mà app đang dùng.
    Biến đã đặt sẵn trên cửa sổ lệnh được giữ nguyên (setdefault), test không bị ảnh hưởng.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # để in được tiếng Việt trên cửa sổ lệnh Windows
    load_env()
    env_db = os.environ.get("BAKERY_DB_PATH", "").strip()
    default_db = (ROOT / env_db) if env_db and not Path(env_db).is_absolute() else Path(env_db or DEFAULT_DB)
    parser = argparse.ArgumentParser(description="Tạo bakery.db và nạp dữ liệu mẫu")
    parser.add_argument("--scenario", choices=["standard", "no_shortage"], default="standard")
    parser.add_argument("--db", default=str(default_db), help="đường dẫn file .db")
    args = parser.parse_args()

    path = init_db(args.db, args.scenario)
    print(f"Giờ dựng dữ liệu: {get_now().isoformat(timespec='minutes')} "
          f"(DEMO_DATE = {os.environ.get('DEMO_DATE') or 'trống, dùng giờ thật'})")
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f"Đã tạo {path} (scenario = {args.scenario})")
    for t in tables:
        n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t:<18} {n:>3} dòng")
    conn.close()


if __name__ == "__main__":
    main()
