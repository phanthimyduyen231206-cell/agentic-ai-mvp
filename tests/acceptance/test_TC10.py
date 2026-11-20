"""TC10 – Restart khi đang chờ chọn (Mai Anh).

Cách thực hiện: tắt và mở lại app khi cảnh báo đang OPEN.
Kết quả cần đạt: cảnh báo và phương án vẫn còn, chọn tiếp được.

"Tắt app" = bỏ hết đối tượng Python, xóa module đã import; "mở lại" = import lại, mở kết nối DB mới
và gọi recover_stale_runs() như khi khởi động. Mọi trạng thái phải đọc lại được từ DB.
"""
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.init_db import init_db  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}

KEM_TUOI = {
    "ingredient_id": "KEM_TUOI", "ingredient_name": "Kem tươi Whipping Cream", "unit": "lít",
    "projected_stock": -2.0, "min_stock": 2.0, "shortage_qty": 4.0,
    "causes": [{"code": "EXPIRING_LOT", "qty": 6.0}], "main_cause": "EXPIRING_LOT", "open_po_id": None,
    "options": [
        {"option": "A", "sup_id": "NCC_CHINH", "sup_name": "NCC Chính", "unit_price": 90000, "order_qty": 4.0,
         "total_cost": 360000, "lead_time_hours": 24, "arrival_at": "2026-11-21T08:00:00",
         "meets_earliest_preorder": True, "cost_diff_pct_vs_A": 0, "reason": "Rẻ nhất"},
        {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 103500,
         "order_qty": 4.0, "total_cost": 414000, "lead_time_hours": 2, "arrival_at": "2026-11-20T10:00:00",
         "meets_earliest_preorder": True, "cost_diff_pct_vs_A": 15.0, "reason": "Nhanh nhất"},
    ],
    "note": None, "snapshot_hash": "hash-kem",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc10.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def _restart_modules():
    """Giả lập tắt/mở lại app: import lại các module của hệ thống."""
    for name in ("services.audit_service", "data.repository"):
        sys.modules.pop(name, None)
    repo = importlib.import_module("data.repository")
    audit_service = importlib.import_module("services.audit_service")
    return repo, audit_service


def test_tc10_alert_survives_restart(db, monkeypatch):
    from services import calc
    from services.audit_service import run_morning_audit

    monkeypatch.setattr(calc, "compute_alerts", lambda run_date: [KEM_TUOI], raising=False)
    out = run_morning_audit("SCHEDULED", llm=FakeLLM())
    assert out["status"] == "SUCCEEDED"
    alert_id = out["alert_ids"][0]

    # --- tắt và mở lại app ---
    repo, audit_service = _restart_modules()
    assert audit_service.recover_stale_runs() == []  # phiên SUCCEEDED không bị đụng tới

    alert = repo.get_alert(alert_id)
    assert alert["status"] == "OPEN"  # vẫn chờ quản lý chọn
    assert alert["explanation"]
    options = json.loads(alert["options_json"])
    assert [o["option"] for o in options] == ["A", "B"]
    assert all(o["tradeoff"] for o in options)
    assert alert["snapshot_hash"] == repo.compute_snapshot_hash("KEM_TUOI")  # create_draft_po kiểm tiếp được

    # Hôm nay đã rà soát → mở lại app không tạo phiên/cảnh báo mới
    again = audit_service.run_morning_audit("SCHEDULED", llm=FakeLLM())
    assert again["status"] == "ALREADY_DONE" and again["alert_ids"] == [alert_id]


def test_tc10_crash_while_running_is_recovered(db, monkeypatch):
    """App tắt ngang khi phiên đang RUNNING → khởi động lại sau 15 phút thì phiên được đóng, rà soát lại được."""
    from data import repository as repo

    repo.insert_audit_run({"run_date": "2026-11-20", "trigger": "SCHEDULED", "status": "RUNNING",
                           "started_at": "2026-11-20T07:30:00"})
    repo, audit_service = _restart_modules()
    from services import calc
    monkeypatch.setattr(calc, "compute_alerts", lambda run_date: [], raising=False)

    recovered = audit_service.recover_stale_runs()
    assert len(recovered) == 1
    assert repo.get_audit_run(recovered[0])["status"] == "FAILED"
    assert audit_service.run_morning_audit("SCHEDULED", llm=FakeLLM())["status"] == "SUCCEEDED"
