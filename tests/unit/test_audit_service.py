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
        # Như calc thật (contracts mục 5): snapshot_hash tính ngay lúc tính cảnh báo
        box["calls"] += 1
        return [dict(d, snapshot_hash=repo.compute_snapshot_hash(d["ingredient_id"])) for d in box["value"]]

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


# ---------------------------------------------------------------------------
# Phiên mới đóng cảnh báo OPEN của phiên trước (fix/audit-supersede)
# ---------------------------------------------------------------------------
def _dismissed_logs():
    return [dict(r, detail=json.loads(r["detail_json"] or "{}"))
            for r in repo.get_audit_logs(event_type="ALERT_DISMISSED")]


def test_rerun_supersedes_old_open_alerts(db, drafts):
    run1 = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    run2 = run_morning_audit("MANUAL", rerun=True, user=MANAGER, llm=FakeLLM())

    open_alerts = repo.get_alerts(status="OPEN")
    ids = sorted(a["ingredient_id"] for a in open_alerts)
    assert ids == sorted({"KEM_TUOI", "BO_LAT"})  # mỗi nguyên liệu đúng 1 cảnh báo OPEN
    assert {a["run_id"] for a in open_alerts} == {run2["run_id"]}  # và là của phiên 2

    for alert_id in run1["alert_ids"]:
        old = repo.get_alert(alert_id)
        assert old["status"] == "DISMISSED" and old["resolved_at"]

    logs = _dismissed_logs()
    assert sorted(int(l["entity_id"]) for l in logs) == sorted(run1["alert_ids"])
    for l in logs:
        assert l["actor"] == "system"
        assert l["detail"] == {"reason": "SUPERSEDED", "by_run": run2["run_id"]}
        assert l["run_id"] == run2["run_id"]


def test_selected_alert_kept_after_rerun(db, drafts):
    run1 = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    kem = repo.get_alerts(run_id=run1["run_id"], ingredient_id="KEM_TUOI")[0]
    repo.update_alert(kem["alert_id"], {"status": "SELECTED"})  # quản lý đã chọn A, PO đang xử lý

    run_morning_audit("MANUAL", rerun=True, user=MANAGER, llm=FakeLLM())

    assert repo.get_alert(kem["alert_id"])["status"] == "SELECTED"
    bo_old = repo.get_alerts(run_id=run1["run_id"], ingredient_id="BO_LAT")[0]
    assert bo_old["status"] == "DISMISSED"
    assert kem["alert_id"] not in [int(l["entity_id"]) for l in _dismissed_logs()]


def test_no_shortage_run_also_closes_old_alerts(db, drafts):
    run1 = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    drafts["value"] = []  # phiên 2: không còn thiếu hàng (như kịch bản no_shortage)
    run2 = run_morning_audit("MANUAL", rerun=True, user=MANAGER, llm=FakeLLM())

    assert run2["status"] == "SUCCEEDED" and run2["alert_ids"] == []
    assert repo.get_alerts(status="OPEN") == []
    assert all(repo.get_alert(i)["status"] == "DISMISSED" for i in run1["alert_ids"])
    detail = json.loads(repo.get_audit_logs(event_type="RUN_SUCCEEDED")[0]["detail_json"])
    assert sorted(detail["superseded"]) == sorted(run1["alert_ids"])


def test_next_day_scheduled_run_closes_yesterday_alerts(db, drafts, monkeypatch):
    run1 = run_morning_audit("SCHEDULED", llm=FakeLLM())
    monkeypatch.setenv("DEMO_DATE", "2026-11-21T08:00")  # sang ngày mới, lịch 08:00 chạy
    run2 = run_morning_audit("SCHEDULED", llm=FakeLLM())

    assert run2["status"] == "SUCCEEDED" and run2["run_id"] != run1["run_id"]
    assert {a["run_id"] for a in repo.get_alerts(status="OPEN")} == {run2["run_id"]}
    assert len(repo.get_alerts(status="OPEN")) == 2


def test_failed_rerun_does_not_close_old_alerts(db, drafts, monkeypatch):
    run1 = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())

    def boom(run_date):
        raise RuntimeError("calc lỗi")

    monkeypatch.setattr(calc, "compute_alerts", boom)
    assert run_morning_audit("MANUAL", rerun=True, user=MANAGER, llm=FakeLLM())["status"] == "FAILED"
    assert all(repo.get_alert(i)["status"] == "OPEN" for i in run1["alert_ids"])
    assert _dismissed_logs() == []


# ---------------------------------------------------------------------------
# MA1: snapshot_hash tính TRƯỚC khi gọi Gemini (hở TC15) + cờ ai_fallback
# ---------------------------------------------------------------------------
class _SlowLLMWithStockIssue(FakeLLM):
    """Gemini chậm: trong lúc "đang nghĩ" thì quản lý ca khác xuất 1 lít Kem tươi (TC15)."""

    def generate(self, *args, **kwargs):
        if self.call_count == 0:
            from services import inventory_service
            inventory_service.issue_stock_fefo("KEM_TUOI", 1, {"username": "quanly2", "role": "MANAGER"})
        return super().generate(*args, **kwargs)


def _kem_alert(out):
    return repo.get_alerts(run_id=out["run_id"], ingredient_id="KEM_TUOI")[0]


def test_tc15_stock_issued_while_waiting_gemini_blocks_po(db):
    """calc thật + Gemini chậm + xuất kho trong lúc chờ → tạo PO bị SNAPSHOT_CHANGED."""
    from services import po_service
    from services.po_service import GuardrailError

    hash_before = repo.compute_snapshot_hash("KEM_TUOI")
    out = run_morning_audit("MANUAL", user=MANAGER, llm=_SlowLLMWithStockIssue())
    assert out["status"] == "SUCCEEDED"
    alert = _kem_alert(out)
    assert alert["snapshot_hash"] == hash_before  # hash của dữ liệu calc đã dùng, không phải sau Gemini
    assert repo.compute_snapshot_hash("KEM_TUOI") != hash_before

    with pytest.raises(GuardrailError) as exc:
        po_service.create_draft_po(alert["alert_id"], "A", None, MANAGER)
    assert exc.value.code == "SNAPSHOT_CHANGED"
    assert repo.get_alert(alert["alert_id"])["status"] == "OPEN"


def test_po_created_when_stock_unchanged(db):
    """Đối chứng: không ai đụng kho trong lúc chờ Gemini → tạo PO bình thường."""
    from services import po_service

    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    po_id = po_service.create_draft_po(_kem_alert(out)["alert_id"], "A", None, MANAGER)
    assert repo.get_purchase_order(po_id)["status"] == "DRAFT"


def test_draft_without_hash_gets_hash_before_gemini(db, monkeypatch):
    """AlertDraft thiếu snapshot_hash → audit_service tự tính, vẫn TRƯỚC khi gọi Gemini."""
    monkeypatch.setattr(audit_service, "_compute_alerts", lambda d: [dict(KEM_TUOI, snapshot_hash=None)])
    hash_before = repo.compute_snapshot_hash("KEM_TUOI")
    out = run_morning_audit("MANUAL", user=MANAGER, llm=_SlowLLMWithStockIssue())
    assert _kem_alert(out)["snapshot_hash"] == hash_before
    assert repo.compute_snapshot_hash("KEM_TUOI") != hash_before


def test_ai_fallback_flag_per_alert(db, drafts):
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    assert all(a["ai_fallback"] == 0 for a in repo.get_alerts(run_id=out["run_id"]))
    created = [json.loads(r["detail_json"]) for r in repo.get_audit_logs(event_type="ALERT_CREATED")]
    assert all(d["ai_fallback"] is False for d in created)


def test_gemini_error_sets_ai_fallback_true(db, drafts):
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM(behavior="error"))
    assert all(a["ai_fallback"] == 1 for a in repo.get_alerts(run_id=out["run_id"]))


def test_made_up_numbers_use_template_and_flag(db, drafts):
    """FakeLLM bịa số → lưu câu mẫu do code sinh, ai_fallback = 1, RUN_SUCCEEDED ghi numbers_mismatch."""
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM(behavior="made_up_numbers"))
    kem = _kem_alert(out)
    assert "99.5" not in kem["explanation"] and "-2.0" in kem["explanation"]
    assert kem["ai_fallback"] == 1
    assert all("123456" not in (o["tradeoff"] or "") for o in json.loads(kem["options_json"]))
    detail = json.loads(repo.get_audit_logs(event_type="RUN_SUCCEEDED")[0]["detail_json"])
    assert "numbers_mismatch" in detail["fallback"]
