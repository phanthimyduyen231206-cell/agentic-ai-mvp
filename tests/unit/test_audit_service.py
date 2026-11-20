"""Unit test cho services/audit_service.py (M05) – FakeLLM, DB tạm, compute_alerts giả.

Chạy: pytest tests/unit/test_audit_service.py
Khi Q02 (calc.compute_alerts) chưa có, test thay compute_alerts bằng AlertDraft mẫu của contracts mục 5.
"""
import json
import sqlite3
import threading
import time
from datetime import datetime

import pytest

from data import repository as repo
from data.init_db import init_db
from services import audit_service, calc
from services.audit_service import recover_stale_runs, run_morning_audit
from tests.fake_llm import FakeLLM

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}

KEM_TUOI = {
    "ingredient_id": "KEM_TUOI", "ingredient_name": "Kem tươi Whipping Cream", "unit": "lít",
    "projected_stock": -2.0, "min_stock": 2.0, "shortage_qty": 4.0,
    "causes": [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0},
               {"code": "DAILY_USAGE", "qty": 3.0}],
    "main_cause": "EXPIRING_LOT", "open_po_id": None,
    "options": [
        {"option": "A", "sup_id": "NCC_CHINH", "sup_name": "NCC Chính", "unit_price": 90000,
         "order_qty": 4.0, "total_cost": 360000, "lead_time_hours": 24, "lead_time_days": 1,
         "arrival_at": "2026-11-21T08:00:00", "meets_earliest_preorder": True, "cost_diff_pct_vs_A": 0,
         "reason": "Tổng chi phí thấp nhất"},
        {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 103500,
         "order_qty": 4.0, "total_cost": 414000, "lead_time_hours": 2, "lead_time_days": 1,
         "arrival_at": "2026-11-20T10:00:00", "meets_earliest_preorder": True, "cost_diff_pct_vs_A": 15.0,
         "reason": "Giao nhanh nhất"},
    ],
    "note": None, "snapshot_hash": "hash-kem",
}
BO_LAT = {
    "ingredient_id": "BO_LAT", "ingredient_name": "Bơ lạt", "unit": "kg",
    "projected_stock": -10.0, "min_stock": 3.0, "shortage_qty": 13.0,
    "causes": [{"code": "SUPPLIER_DELAY", "qty": None}, {"code": "PREORDER", "qty": 12.0}],
    "main_cause": "SUPPLIER_DELAY", "open_po_id": None, "options": [], "note": "Cần xử lý thủ công",
    "snapshot_hash": "hash-bo",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "audit.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


@pytest.fixture()
def drafts(monkeypatch):
    """compute_alerts giả; đổi nội dung qua drafts['value']."""
    box = {"value": [KEM_TUOI, BO_LAT], "calls": 0}

    def fake(run_date):
        box["calls"] += 1
        return box["value"]

    monkeypatch.setattr(calc, "compute_alerts", fake, raising=False)
    return box


def _events():
    return [r["event_type"] for r in reversed(repo.get_audit_logs())]


# ---------------------------------------------------------------------------
def test_success_saves_alerts_per_contracts(db, drafts):
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    assert out["status"] == "SUCCEEDED" and len(out["alert_ids"]) == 2

    run = repo.get_audit_run(out["run_id"])
    assert run["status"] == "SUCCEEDED" and run["alert_count"] == 2 and run["trigger"] == "MANUAL"
    assert run["finished_at"]

    kem = repo.get_alerts(run_id=out["run_id"], ingredient_id="KEM_TUOI")[0]
    assert kem["status"] == "OPEN"
    assert kem["main_cause"] == "EXPIRING_LOT"
    assert kem["snapshot_hash"] == repo.compute_snapshot_hash("KEM_TUOI")  # hàm của P03
    assert kem["projected_stock"] == -2.0 and kem["shortage_qty"] == 4.0
    assert json.loads(kem["causes"]) == KEM_TUOI["causes"]  # đủ dạng {code, qty}
    options = json.loads(kem["options_json"])
    assert [o["option"] for o in options] == ["A", "B"]
    assert all(o["tradeoff"] for o in options)  # A ← tradeoff_a, B ← tradeoff_b
    assert options[0]["total_cost"] == 360000
    assert kem["explanation"]

    bo = repo.get_alerts(run_id=out["run_id"], ingredient_id="BO_LAT")[0]
    assert json.loads(bo["causes"])[0] == {"code": "SUPPLIER_DELAY", "qty": None}
    assert json.loads(bo["options_json"]) == []

    ev = _events()
    assert ev[0] == "RUN_STARTED" and ev[-1] == "RUN_SUCCEEDED"
    assert ev.count("ALERT_CREATED") == 2
    assert repo.get_audit_logs(event_type="RUN_STARTED")[0]["actor"] == "quanly"


def test_no_shortage_succeeds_without_alerts_or_gemini(db, drafts):
    drafts["value"] = []
    llm = FakeLLM()
    out = run_morning_audit("SCHEDULED", llm=llm)
    assert out == {"run_id": out["run_id"], "status": "SUCCEEDED", "alert_ids": [],
                   "message": out["message"]}
    assert repo.get_alerts() == []
    assert llm.call_count == 0
    assert repo.get_audit_logs(event_type="RUN_SUCCEEDED")[0]["actor"] == "scheduler"


def test_gemini_error_still_saves_alerts_with_fallback(db, drafts):
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM(behavior="error"))
    assert out["status"] == "SUCCEEDED"
    kem = repo.get_alerts(ingredient_id="KEM_TUOI")[0]
    assert "-2.0" in kem["explanation"]  # câu mẫu do code sinh từ AlertDraft
    detail = json.loads(repo.get_audit_logs(event_type="RUN_SUCCEEDED")[0]["detail_json"])
    assert "llm_failed" in detail["fallback"]


def test_already_done_returns_previous_run(db, drafts):
    first = run_morning_audit("SCHEDULED", llm=FakeLLM())
    second = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    assert second["status"] == "ALREADY_DONE"
    assert second["run_id"] == first["run_id"] and second["alert_ids"] == first["alert_ids"]
    assert len(repo.get_audit_runs()) == 1 and drafts["calls"] == 1


def test_rerun_creates_new_run(db, drafts):
    first = run_morning_audit("SCHEDULED", llm=FakeLLM())
    again = run_morning_audit("MANUAL", rerun=True, user=MANAGER, llm=FakeLLM())
    assert again["status"] == "SUCCEEDED" and again["run_id"] != first["run_id"]
    assert repo.get_audit_run(again["run_id"])["is_rerun"] == 1


def test_calc_error_marks_failed_and_releases_lock(db, drafts, monkeypatch):
    def boom(run_date):
        raise ZeroDivisionError("lỗi tính toán")

    monkeypatch.setattr(calc, "compute_alerts", boom)
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    assert out["status"] == "FAILED" and out["alert_ids"] == []
    run = repo.get_audit_run(out["run_id"])
    assert run["status"] == "FAILED" and "ZeroDivisionError" in run["error"]
    assert "RUN_FAILED" in _events()
    assert repo.get_running_audit_run() is None  # chạy lại được ngay

    monkeypatch.setattr(calc, "compute_alerts", lambda d: [], raising=False)
    assert run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())["status"] == "SUCCEEDED"


def test_missing_calc_fails_gracefully(db, monkeypatch):
    monkeypatch.delattr(calc, "compute_alerts", raising=False)
    out = run_morning_audit("SCHEDULED", llm=FakeLLM())
    assert out["status"] == "FAILED"
    assert "compute_alerts" in repo.get_audit_run(out["run_id"])["error"]


def test_save_is_atomic(db, drafts, monkeypatch):
    """Lỗi giữa chừng khi lưu cảnh báo → không lưu cảnh báo nào, phiên FAILED."""
    real = repo.insert_alert
    count = {"n": 0}

    def flaky(data, conn=None):
        count["n"] += 1
        if count["n"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(data, conn=conn)

    monkeypatch.setattr(repo, "insert_alert", flaky)
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    assert out["status"] == "FAILED"
    assert repo.get_alerts() == []
    assert repo.get_audit_logs(event_type="ALERT_CREATED") == []


def test_invalid_trigger_raises(db):
    with pytest.raises(ValueError):
        run_morning_audit("BUTTON")


# ---------------------------------------------------------------------------
def test_recover_stale_runs(db, drafts):
    repo.insert_audit_run({"run_date": "2026-11-20", "trigger": "SCHEDULED", "status": "RUNNING",
                           "started_at": "2026-11-20T07:40:00"})  # 20 phút trước NOW
    assert run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())["status"] == "REJECTED_DUPLICATE"

    recovered = recover_stale_runs()
    assert len(recovered) == 1
    assert repo.get_audit_run(recovered[0])["status"] == "FAILED"
    assert run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())["status"] == "SUCCEEDED"


def test_recover_keeps_recent_running(db):
    run_id = repo.insert_audit_run({"run_date": "2026-11-20", "trigger": "MANUAL", "status": "RUNNING",
                                    "started_at": "2026-11-20T07:55:00"})  # 5 phút trước
    assert recover_stale_runs() == []
    assert repo.get_audit_run(run_id)["status"] == "RUNNING"


def test_concurrent_calls_only_one_runs(db, drafts, monkeypatch):
    """2 lần bấm gần như cùng lúc: 1 phiên chạy, lần kia bị từ chối."""
    started = threading.Event()

    def slow(run_date):
        started.set()
        time.sleep(0.5)
        return []

    monkeypatch.setattr(calc, "compute_alerts", slow)
    results = {}
    t = threading.Thread(target=lambda: results.update(a=run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())))
    t.start()
    assert started.wait(5)
    results["b"] = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    t.join(10)
    assert results["a"]["status"] == "SUCCEEDED"
    assert results["b"]["status"] == "REJECTED_DUPLICATE"
    assert len(repo.get_audit_runs()) == 1
    assert len(repo.get_audit_logs(event_type="RUN_REJECTED_DUPLICATE")) == 1


def test_mark_delayed_only_overdue_pending(db):
    delayed = audit_service.mark_delayed_pos(None)
    assert delayed == ["PO-004"]
    po = repo.get_purchase_order("PO-004")
    assert po["status"] == "SENT" and po["delivery_status"] == "DELAYED"
    assert repo.get_purchase_order("PO-001")["delivery_status"] == "RECEIVED"
    assert audit_service.mark_delayed_pos(None) == []  # chạy lại không ghi trùng


def test_snapshot_hash_changes_when_stock_changes(db, drafts):
    """Hash lưu lúc tạo cảnh báo khác hash sau khi tồn kho đổi → create_draft_po phát hiện được (TC15)."""
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    saved = repo.get_alerts(run_id=out["run_id"], ingredient_id="KEM_TUOI")[0]["snapshot_hash"]
    batch = repo.get_batches("KEM_TUOI")[0]
    repo.update_batch_qty(batch["batch_id"], batch["qty"] + 1)
    assert repo.compute_snapshot_hash("KEM_TUOI") != saved
