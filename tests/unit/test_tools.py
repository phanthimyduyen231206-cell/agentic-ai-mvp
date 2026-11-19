"""Unit test cho agent/tools.py (Q03). Chạy: pytest tests/unit/test_tools.py -v

Số liệu kỳ vọng lấy từ data/expected.md (H = 3, DEMO_DATE = 2026-11-20T08:00).
"""
import hashlib
import inspect
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import tools  # noqa: E402
from agent.prompts import READ_ONLY_TOOLS  # noqa: E402
from agent.tools import (  # noqa: E402
    READ_TOOLS,
    TOOL_DECLARATIONS,
    calc_preorder_demand,
    check_delayed_po,
    check_inventory_fefo,
    compare_suppliers,
)
from data.init_db import init_db  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
FOUR = ("check_inventory_fefo", "calc_preorder_demand", "check_delayed_po", "compare_suppliers")


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


def _set_delivery_status(path, po_id, status):
    with sqlite3.connect(path) as c:
        c.execute("UPDATE purchase_orders SET delivery_status = ? WHERE po_id = ?", (status, po_id))


def _dump_db(path) -> str:
    with sqlite3.connect(path) as c:
        return hashlib.sha256("\n".join(c.iterdump()).encode()).hexdigest()


# ---------- Registry / guardrail (đặc tả Mục 8) ----------

def test_read_tools_has_exactly_four_read_tools():
    assert tuple(READ_TOOLS) == FOUR
    assert set(READ_TOOLS) == set(READ_ONLY_TOOLS)  # khớp prompts.py của Mai Anh
    assert "create_draft_po" not in READ_TOOLS
    assert "write_audit_log" not in READ_TOOLS
    assert all(callable(fn) and fn.__name__ == name for name, fn in READ_TOOLS.items())


def test_module_has_no_write_functions():
    public = {n for n, v in vars(tools).items() if callable(v) and not n.startswith("_")}
    assert not {"create_draft_po", "write_audit_log", "approve_po", "send_po"} & public


def test_signatures_match_contracts():
    def sig(fn):
        return {k: p.default for k, p in inspect.signature(fn).parameters.items()}

    assert sig(check_inventory_fefo) == {"ingredient_id": None, "at": None, "days": 0}
    assert sig(calc_preorder_demand) == {"ingredient_id": None, "days": 3}
    assert sig(check_delayed_po) == {"ingredient_id": None, "days": 3}
    assert sig(compare_suppliers) == {"ingredient_id": inspect.Parameter.empty}


def test_declarations_match_signatures():
    assert set(TOOL_DECLARATIONS) == set(READ_TOOLS)
    for name, fn in READ_TOOLS.items():
        params = inspect.signature(fn).parameters
        spec = TOOL_DECLARATIONS[name]
        assert spec["description"]
        assert spec["parameters"]["type"] == "OBJECT"
        assert set(spec["parameters"]["properties"]) == set(params)
        required = {k for k, p in params.items() if p.default is inspect.Parameter.empty}
        assert set(spec["parameters"]["required"]) == required


def test_gemini_function_declarations_build():
    pytest.importorskip("google.genai")
    decls = tools.get_gemini_function_declarations()
    assert [d.name for d in decls] == list(FOUR)
    assert decls[3].parameters.required == ["ingredient_id"]


# ---------- check_inventory_fefo ----------

def test_inventory_kem_tuoi_fefo_order_and_expiring(db):
    r = check_inventory_fefo("KEM_TUOI", days=3)
    assert r["ok"] is True
    assert [b["batch_id"] for b in r["batches"]] == ["KEM-L1", "KEM-L2"]  # hạn sớm nhất trước
    assert r["on_hand_qty"] == pytest.approx(10.0)
    assert r["expiring_qty"] == pytest.approx(7.0)  # KEM-L1 hết hạn 20:00 hôm nay
    assert r["batches"][0]["expiry_date"] == "2026-11-20T20:00:00"
    assert r["batches"][0]["days_left"] == pytest.approx(0.5)
    assert r["batches"][0]["expires_within_days"] is True
    assert r["batches"][1]["expires_within_days"] is False
    proj = r["fefo_projection"]  # khớp bảng FEFO Kem tươi trong expected.md
    assert proj["expired_qty"] == pytest.approx(6.0)
    assert proj["unmet_qty"] == pytest.approx(2.0)
    assert proj["projected_stock"] == pytest.approx(-2.0)


def test_inventory_days_zero_has_no_projection(db):
    r = check_inventory_fefo("KEM_TUOI")
    assert r["expiring_qty"] == 0.0
    assert "fefo_projection" not in r


def test_inventory_at_after_expiry_drops_lot(db):
    r = check_inventory_fefo("KEM_TUOI", at="2026-11-20T21:00:00")
    assert [b["batch_id"] for b in r["batches"]] == ["KEM-L2"]
    assert r["on_hand_qty"] == pytest.approx(3.0)


def test_inventory_trung_lot_expiring_after_window(db):
    r = check_inventory_fefo("TRUNG", days=3)
    assert r["on_hand_qty"] == pytest.approx(150.0)
    assert r["expiring_qty"] == 0.0  # TRUNG-L1 hết hạn ngày +3, ngoài ngày 0…2
    assert r["fefo_projection"]["projected_stock"] == pytest.approx(50.0)


def test_inventory_without_id_lists_all_ingredients(db):
    r = check_inventory_fefo()
    items = {i["ingredient_id"]: i for i in r["items"]}
    assert len(items) == 12
    assert items["BOT_MI"]["on_hand_qty"] == pytest.approx(40.0)
    assert items["BO_LAT"]["on_hand_qty"] == pytest.approx(5.0)


def test_inventory_accepts_lowercase_and_float_days(db):
    r = check_inventory_fefo(ingredient_id=" kem_tuoi ", days=3.0)
    assert r["ok"] is True and r["ingredient_id"] == "KEM_TUOI" and r["days"] == 3


# ---------- calc_preorder_demand ----------

@pytest.mark.parametrize("ing, preorder, retail", [
    ("KEM_TUOI", 3.0, 3.0), ("BO_LAT", 12.0, 3.0), ("BOT_MI", 4.0, 36.0), ("MASCARPONE", 1.0, 0.6),
    ("TRUNG", 40.0, 60.0), ("DUONG", 2.5, 6.0), ("SUA_TUOI", 2.75, 9.0), ("VANI", 75.0, 60.0),
    ("SOCOLA", 0.0, 1.5),
])
def test_preorder_demand_matches_expected(db, ing, preorder, retail):
    r = calc_preorder_demand(ing, days=3)
    assert r["ok"] is True
    assert r["preorder_demand"] == pytest.approx(preorder)
    assert r["retail_demand"] == pytest.approx(retail)
    assert r["from_date"] == "2026-11-20" and r["to_date"] == "2026-11-22"


def test_preorder_kem_tuoi_earliest_date_and_orders(db):
    r = calc_preorder_demand("KEM_TUOI")
    assert r["earliest_preorder_date"] == "2026-11-21"
    assert sum(o["need_qty"] for o in r["orders"]) == pytest.approx(3.0)


def test_preorder_none_when_no_orders(db):
    r = calc_preorder_demand("SOCOLA")
    assert r["orders"] == [] and r["earliest_preorder_date"] is None


def test_preorder_days_zero_is_empty(db):
    r = calc_preorder_demand("KEM_TUOI", days=0)
    assert r["preorder_demand"] == 0.0 and r["retail_demand"] == 0.0 and r["orders"] == []


def test_preorder_without_id_covers_all(db):
    r = calc_preorder_demand()
    items = {i["ingredient_id"]: i for i in r["items"]}
    assert len(items) == 12
    assert items["BO_LAT"]["preorder_demand"] == pytest.approx(12.0)


# ---------- check_delayed_po ----------

def test_delayed_po_seed_pending_overdue_not_counted(db):
    """PO-004 seed còn PENDING (quá hạn, chưa rà soát) → không cộng delayed_qty, liệt kê riêng."""
    r = check_delayed_po("BO_LAT")
    assert r["ok"] is True
    assert r["delayed_qty"] == 0.0 and r["delayed_pos"] == []
    assert r["incoming_stock"] == 0.0
    assert [p["po_id"] for p in r["overdue_not_yet_flagged"]] == ["PO-004"]
    assert r["overdue_not_yet_flagged"][0]["remaining_qty"] == pytest.approx(20.0)
    assert r["open_unsent_pos"] == []


def test_delayed_po_after_flagged_delayed(db):
    _set_delivery_status(db, "PO-004", "DELAYED")
    r = check_delayed_po("BO_LAT")
    assert r["delayed_qty"] == pytest.approx(20.0)  # expected.md: delayed_qty = 20
    assert [p["po_id"] for p in r["delayed_pos"]] == ["PO-004"]
    assert r["overdue_not_yet_flagged"] == []
    assert r["incoming_stock"] == 0.0  # PO DELAYED không tính vào incoming


def test_delayed_po_incoming_within_window(db):
    with sqlite3.connect(db) as c:
        c.execute("UPDATE purchase_orders SET expected_delivery_date = '2026-11-21' WHERE po_id = 'PO-004'")
    assert check_delayed_po("BO_LAT", days=3)["incoming_stock"] == pytest.approx(20.0)
    assert check_delayed_po("BO_LAT", days=1)["incoming_stock"] == 0.0  # chỉ ngày 0


def test_delayed_po_lists_open_unsent(db):
    with sqlite3.connect(db) as c:
        c.execute(
            """INSERT INTO purchase_orders (po_id, sup_id, ingredient_id, order_qty, unit_price,
                   total_cost, status, created_by, created_at)
               VALUES ('PO-099', 'NCC_CHINH', 'KEM_TUOI', 4, 90000, 360000, 'DRAFT', 'test',
                   '2026-11-20T08:00:00')""")
    r = check_delayed_po("KEM_TUOI")
    assert [p["po_id"] for p in r["open_unsent_pos"]] == ["PO-099"]
    assert r["incoming_stock"] == 0.0 and r["delayed_qty"] == 0.0


def test_delayed_po_without_id(db):
    _set_delivery_status(db, "PO-004", "DELAYED")
    r = check_delayed_po()
    assert r["by_ingredient"] == [{"ingredient_id": "BO_LAT", "incoming_stock": 0.0, "delayed_qty": 20.0}]
    assert "delayed_qty" not in r  # không cộng chung các đơn vị khác nhau


def test_delayed_po_no_shortage_has_none(db_no_shortage):
    r = check_delayed_po("BO_LAT")
    assert r["delayed_pos"] == [] and r["overdue_not_yet_flagged"] == []


# ---------- compare_suppliers ----------

def test_compare_suppliers_bo_lat(db):
    r = compare_suppliers("BO_LAT")
    assert r["ok"] is True
    sups = {s["sup_id"]: s for s in r["suppliers"]}
    assert set(sups) == {"NCC_CHINH", "NCC_HOA_TOC"}
    assert (sups["NCC_CHINH"]["price"], sups["NCC_CHINH"]["moq"], sups["NCC_CHINH"]["lead_time_days"]) == (250000, 5, 1)
    assert (sups["NCC_HOA_TOC"]["price"], sups["NCC_HOA_TOC"]["moq"]) == (287500, 2)
    assert sups["NCC_HOA_TOC"]["lead_time_hours"] == 2 and sups["NCC_HOA_TOC"]["lead_time_days"] == 1
    for s in r["suppliers"]:
        assert {"price", "lead_time_hours", "lead_time_days", "moq", "reliability", "active"} <= set(s)
        assert "contact_email" not in s


def test_compare_suppliers_lead_time_48h_and_missing_email(db):
    sups = {s["sup_id"]: s for s in compare_suppliers("BOT_MI")["suppliers"]}
    assert sups["NCC_ANH_DUONG"]["lead_time_days"] == 2
    assert sups["NCC_HOA_TOC"]["has_contact_email"] is False
    assert sups["NCC_CHINH"]["has_contact_email"] is True


def test_compare_suppliers_mascarpone_single(db):
    assert [s["sup_id"] for s in compare_suppliers("MASCARPONE")["suppliers"]] == ["NCC_CHINH"]


# ---------- Lỗi đầu vào ----------

@pytest.mark.parametrize("call", [
    lambda: check_inventory_fefo("KHONG_CO"),
    lambda: calc_preorder_demand("KHONG_CO"),
    lambda: check_delayed_po("KHONG_CO"),
    lambda: compare_suppliers("KHONG_CO"),
])
def test_unknown_ingredient_returns_error(db, call):
    r = call()
    assert r["ok"] is False and r["error_code"] == "INGREDIENT_NOT_FOUND" and r["message"]


@pytest.mark.parametrize("call", [
    lambda: check_inventory_fefo("KEM_TUOI", at="ngày mai"),
    lambda: check_inventory_fefo("KEM_TUOI", days=-1),
    lambda: calc_preorder_demand("KEM_TUOI", days=2.5),
    lambda: check_delayed_po("BO_LAT", days="ba"),
    lambda: compare_suppliers(""),
])
def test_invalid_argument_returns_error(db, call):
    r = call()
    assert r["ok"] is False and r["error_code"] == "INVALID_ARGUMENT"


# ---------- Chỉ đọc, JSON được ----------

def test_all_tools_json_serializable_and_read_only(db):
    before = _dump_db(db)
    calls = [
        check_inventory_fefo(), check_inventory_fefo("KEM_TUOI", days=3),
        calc_preorder_demand(), calc_preorder_demand("BOT_MI"),
        check_delayed_po(), check_delayed_po("BO_LAT"),
        compare_suppliers("KEM_TUOI"),
    ]
    for r in calls:
        assert r["ok"] is True
        json.dumps(r, ensure_ascii=False)
    assert _dump_db(db) == before


def test_tools_called_like_core_agent(db):
    """core_agent gọi tools[name](**args) với args do Gemini sinh."""
    r = READ_TOOLS["check_inventory_fefo"](**{"ingredient_id": "KEM_TUOI", "days": 3})
    assert r["fefo_projection"]["expired_qty"] == pytest.approx(6.0)
