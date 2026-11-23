"""Unit test cho services/calc.py (Q02). Chạy: pytest tests/unit/test_calc.py -v

Số liệu kỳ vọng: data/expected.md, docs/contracts.md (Mục 5), đặc tả Mục 5.3 – 5.4.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services import calc  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
TODAY = NOW.date()


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


def _sql(sql, params=()):
    conn = repo.get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _alerts():
    return {a["ingredient_id"]: a for a in calc.compute_alerts(TODAY)}


def _quote(sup_id, cost, hours, qty=1.0, reliability=0.9):
    return {"sup_id": sup_id, "sup_name": sup_id, "unit_price": cost / qty, "order_qty": qty,
            "total_cost": cost, "lead_time_hours": hours, "lead_time_days": calc.lead_time_days(hours),
            "arrival_at": datetime(2026, 11, 20, 8, 0), "reliability": reliability}


# ---------- Hàm thuần ----------

@pytest.mark.parametrize("hours, days", [(1, 1), (2, 1), (24, 1), (25, 2), (48, 2), (96, 4)])
def test_lead_time_days(hours, days):
    assert calc.lead_time_days(hours) == days


@pytest.mark.parametrize("qty, moq, expected", [
    (23, 10, 30), (10, 10, 10), (10, 25, 25), (13, 5, 15), (13, 2, 14), (1.1, 1, 2), (4, 2, 4),
    (0, 5, 0), (0.6, 0.2, 0.6), (0.61, 0.2, 0.8),
    (0.6000000001, 0.2, 0.6),  # sai số float, không phải thiếu thật → không đặt thêm 1 MOQ
])
def test_round_up_moq(qty, moq, expected):
    assert calc.round_up_moq(qty, moq) == pytest.approx(expected)


def test_build_causes_sorted_and_main_cause():
    causes, main = calc.build_causes(3.0, 3.0, 6.0, 0.0, False)
    assert causes == [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0},
                      {"code": "DAILY_USAGE", "qty": 3.0}]
    assert main == "EXPIRING_LOT"


def test_build_causes_tie_uses_priority_order():
    _, main = calc.build_causes(5.0, 5.0, 0.0, 5.0, False)
    assert main == "PREORDER"
    _, main = calc.build_causes(0.0, 2.0, 0.0, 2.0, False)
    assert main == "DAILY_USAGE"


def test_build_causes_open_po_not_ranked():
    causes, main = calc.build_causes(1.0, 0.6, 0.0, 0.0, True)
    assert causes[-1] == {"code": "OPEN_PO", "qty": None}
    assert main == "PREORDER"


def test_build_causes_empty():
    causes, main = calc.build_causes(0.0, 0.0, 0.0, 0.0, False)
    assert causes == [] and main is None


def test_choose_options_no_supplier():
    options, note = calc.choose_options([], NOW, None)
    assert options == [] and note == "Cần xử lý thủ công"


def test_choose_options_single_supplier():
    options, note = calc.choose_options([_quote("X", 100, 24)], NOW, None)
    assert [o["option"] for o in options] == ["A"]
    assert "Chỉ có 1 NCC hợp lệ" in note


def test_choose_options_same_supplier_cheapest_and_fastest():
    options, note = calc.choose_options([_quote("X", 100, 2), _quote("Y", 200, 24)], NOW, None)
    assert [(o["option"], o["sup_id"]) for o in options] == [("A", "X")]
    assert "vừa rẻ nhất vừa nhanh nhất" in note


def test_choose_options_a_b_and_cost_diff():
    options, note = calc.choose_options([_quote("CHEAP", 360000, 24), _quote("FAST", 414000, 2)], NOW, None)
    assert note is None
    a, b = options
    assert (a["option"], a["sup_id"], a["cost_diff_pct_vs_A"]) == ("A", "CHEAP", 0.0)
    assert (b["option"], b["sup_id"], b["cost_diff_pct_vs_A"]) == ("B", "FAST", 15.0)


def test_choose_options_ties():
    # A hòa chi phí → lead time ngắn hơn; B hòa lead time → chi phí thấp hơn.
    quotes = [_quote("S1", 100, 48), _quote("S2", 100, 24), _quote("S3", 150, 2), _quote("S4", 120, 2)]
    a, b = calc.choose_options(quotes, NOW, None)[0]
    assert a["sup_id"] == "S2"
    assert b["sup_id"] == "S4"


def test_choose_options_reason_has_hours_and_cost_diff():
    # Số Bơ lạt (expected.md): A NCC Chính 24h 3.750.000đ, B Hỏa tốc 2h 4.025.000đ.
    quotes = [_quote("NCC_CHINH", 3750000, 24, qty=15), _quote("NCC_HOA_TOC", 4025000, 2, qty=14)]
    a, b = calc.choose_options(quotes, NOW, None)[0]
    assert a["reason"] == "Rẻ nhất, về chậm hơn B 22 giờ"
    assert b["reason"] == "Nhanh nhất, đắt hơn A 7.3% (275.000đ)"
    assert b["cost_diff_pct_vs_A"] == 7.3   # % trong câu reason khớp đúng cột chênh lệch


def test_choose_options_tie_prefers_reliability_then_sup_id():
    # Hòa cả tiền lẫn giờ → NCC tin cậy hơn thắng dù sup_id đứng sau theo chữ cái.
    quotes = [_quote("AAA", 100, 24, reliability=0.80), _quote("ZZZ", 100, 24, reliability=0.95),
              _quote("FAST", 150, 2)]
    a, b = calc.choose_options(quotes, NOW, None)[0]
    assert (a["sup_id"], b["sup_id"]) == ("ZZZ", "FAST")
    # B hòa giờ và tiền → cũng chọn reliability cao hơn
    quotes = [_quote("CHEAP", 50, 24), _quote("B1", 100, 2, reliability=0.7), _quote("B2", 100, 2, reliability=0.99)]
    assert calc.choose_options(quotes, NOW, None)[0][1]["sup_id"] == "B2"
    # Reliability cũng hòa → theo sup_id
    quotes = [_quote("Y", 100, 24), _quote("X", 100, 24), _quote("FAST", 150, 2)]
    assert calc.choose_options(quotes, NOW, None)[0][0]["sup_id"] == "X"


def test_reliability_only_breaks_ties_after_spec_rules():
    # A hòa tiền: lead time ngắn hơn vẫn thắng NCC tin cậy hơn (quy tắc 5.4 đứng trước).
    quotes = [_quote("RELIABLE", 100, 48, reliability=0.99), _quote("QUICKER", 100, 24, reliability=0.5),
              _quote("FAST", 150, 2)]
    assert calc.choose_options(quotes, NOW, None)[0][0]["sup_id"] == "QUICKER"
    assert "reliability" not in calc.choose_options(quotes, NOW, None)[0][0]   # JSON options không đổi


@pytest.mark.parametrize("arrival, expected", [
    (datetime(2026, 11, 21, 20, 0), False),   # về 20:00 đúng ngày giao → không kịp
    (datetime(2026, 11, 21, 8, 0), False),    # 08:00 vẫn trễ mốc 07:00
    (datetime(2026, 11, 21, 7, 0), True),     # đúng 07:00 → kịp
    (datetime(2026, 11, 20, 22, 0), True),    # về tối hôm trước
    (datetime(2026, 11, 22, 6, 0), False),    # sau ngày giao
])
def test_meets_earliest_preorder_cutoff_7am(arrival, expected):
    q = _quote("X", 100, 48)  # arrival_at được _option dùng trực tiếp
    q["arrival_at"] = arrival
    assert calc._option("A", q, 100, date(2026, 11, 21), "r")["meets_earliest_preorder"] is expected


def test_meets_earliest_preorder_without_preorder_is_true():
    q = _quote("X", 100, 48)
    q["arrival_at"] = datetime(2026, 11, 30, 20, 0)
    assert calc._option("A", q, 100, None, "r")["meets_earliest_preorder"] is True


# ---------- Trên dữ liệu mẫu ----------

def test_standard_seed_has_exactly_4_alerts(db):
    repo.update_purchase_order("PO-004", {"delivery_status": "DELAYED"})
    assert sorted(_alerts()) == ["BOT_MI", "BO_LAT", "KEM_TUOI", "MASCARPONE"]


def test_no_shortage_seed_has_no_alert(db_no_shortage):
    assert calc.compute_alerts(TODAY) == []


def test_alert_json_shape(db):
    kem = _alerts()["KEM_TUOI"]
    assert set(kem) == {"ingredient_id", "ingredient_name", "unit", "projected_stock", "min_stock",
                        "shortage_qty", "causes", "main_cause", "open_po_id", "options", "note",
                        "snapshot_hash"}
    assert kem["snapshot_hash"] == repo.compute_snapshot_hash("KEM_TUOI")
    assert set(kem["options"][0]) == {"option", "sup_id", "sup_name", "unit_price", "order_qty",
                                      "total_cost", "lead_time_hours", "lead_time_days", "arrival_at",
                                      "meets_earliest_preorder", "cost_diff_pct_vs_A", "reason"}


def test_po_pending_overdue_is_not_supplier_delay(db):
    # PO-004 chưa được audit đổi sang DELAYED → không gắn SUPPLIER_DELAY, nhưng vẫn không tính incoming.
    bo = _alerts()["BO_LAT"]
    assert bo["projected_stock"] == pytest.approx(-10.0)
    assert "SUPPLIER_DELAY" not in [c["code"] for c in bo["causes"]]
    assert bo["main_cause"] == "PREORDER"


def test_open_po_blocks_new_options(db):
    repo.insert_purchase_order({
        "po_id": "PO-005", "sup_id": "NCC_CHINH", "ingredient_id": "KEM_TUOI", "order_qty": 4,
        "unit_price": 90000, "total_cost": 360000, "status": "DRAFT",
        "created_by": "manager", "created_at": NOW.isoformat(timespec="seconds"),
    })
    kem = _alerts()["KEM_TUOI"]
    assert kem["open_po_id"] == "PO-005"
    assert kem["options"] == []
    assert {"code": "OPEN_PO", "qty": None} in kem["causes"]
    assert kem["main_cause"] == "EXPIRING_LOT"
    assert "PO-005" in kem["note"]


def test_closed_po_is_not_open_po(db):
    repo.insert_purchase_order({
        "po_id": "PO-005", "sup_id": "NCC_CHINH", "ingredient_id": "KEM_TUOI", "order_qty": 4,
        "unit_price": 90000, "total_cost": 360000, "status": "DRAFT", "closed_reason": "REJECTED",
        "created_by": "manager", "created_at": NOW.isoformat(timespec="seconds"),
    })
    kem = _alerts()["KEM_TUOI"]
    assert kem["open_po_id"] is None and len(kem["options"]) == 2


def test_no_valid_supplier_manual(db):
    _sql("UPDATE suppliers SET contact_email = NULL WHERE ingredient_id = 'MASCARPONE'")
    mas = _alerts()["MASCARPONE"]
    assert mas["options"] == []
    assert mas["note"] == "Cần xử lý thủ công"


def test_inactive_and_no_email_suppliers_are_excluded(db):
    _sql("UPDATE suppliers SET active = 0 WHERE ingredient_id = 'KEM_TUOI' AND sup_id = 'NCC_HOA_TOC'")
    ids = [s["sup_id"] for s in calc.valid_suppliers("KEM_TUOI")]
    assert ids == ["NCC_ANH_DUONG", "NCC_CHINH"]
    # Hỏa tốc của Bột mì trống email (TC11)
    assert "NCC_HOA_TOC" not in [s["sup_id"] for s in calc.valid_suppliers("BOT_MI")]


def test_moq_rounding_anh_duong_25kg(db):
    # Bỏ NCC Chính → chỉ còn Ánh Dương (MOQ 25): thiếu 10 kg phải đặt 25 kg.
    _sql("UPDATE suppliers SET active = 0 WHERE ingredient_id = 'BOT_MI' AND sup_id = 'NCC_CHINH'")
    bot = _alerts()["BOT_MI"]
    (opt,) = bot["options"]
    assert opt["sup_id"] == "NCC_ANH_DUONG"
    assert opt["order_qty"] == pytest.approx(25.0)
    assert opt["total_cost"] == pytest.approx(450000.0)
    assert opt["lead_time_days"] == 2
    assert opt["arrival_at"] == "2026-11-22T08:00:00"
    assert opt["meets_earliest_preorder"] is False  # đơn sớm nhất giao 21/11


def test_slow_supplier_uses_longer_horizon(db):
    # Ánh Dương giao 96 giờ → D = 4 ngày: projected(4) = −3 → thiếu 5 lít (MOQ 5 → 5).
    _sql("UPDATE suppliers SET lead_time_hours = 96, price = 50000 "
         "WHERE ingredient_id = 'KEM_TUOI' AND sup_id = 'NCC_ANH_DUONG'")
    kem = _alerts()["KEM_TUOI"]
    assert kem["shortage_qty"] == pytest.approx(4.0)  # cảnh báo vẫn tính theo H = 3
    a, b = kem["options"]
    assert (a["sup_id"], a["order_qty"], a["total_cost"]) == ("NCC_ANH_DUONG", 5.0, 250000.0)
    assert a["lead_time_days"] == 4 and a["meets_earliest_preorder"] is False
    assert b["sup_id"] == "NCC_HOA_TOC" and b["order_qty"] == pytest.approx(4.0)


def test_run_time(db):
    assert calc._run_time(TODAY) == NOW                      # hôm nay → clock.now()
    assert calc._run_time(date(2026, 11, 19)) == datetime(2026, 11, 19, 8, 0)  # ngày khác → 08:00
