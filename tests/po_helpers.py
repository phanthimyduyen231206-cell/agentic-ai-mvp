"""Dữ liệu dựng sẵn cho test po_service (D05): DB mẫu trong thư mục tạm + cảnh báo mẫu.

Chủ file: Duyên (D05). Test tự dựng cảnh báo bằng compute_snapshot_hash nên không cần chờ
audit_service (M05). Số liệu phương án lấy theo data/expected.md.
"""
import json
from datetime import datetime

import pytest

from data import repository
from data.init_db import init_db

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "Duyên", "role": "MANAGER"}
STAFF = {"username": "Nhân viên", "role": "STAFF"}

# Bơ lạt (TC04, TC07): thiếu 13 kg -> A = NCC Chính 15 kg, B = NCC Hỏa tốc 14 kg
BO_LAT_OPTIONS = [
    {"option": "A", "sup_id": "NCC_CHINH", "sup_name": "NCC Chính", "unit_price": 250000,
     "order_qty": 15.0, "total_cost": 3750000, "lead_time_hours": 24, "lead_time_days": 1,
     "arrival_at": "2026-11-21T08:00:00", "meets_earliest_preorder": False, "cost_diff_pct_vs_A": 0,
     "reason": "Tổng chi phí thấp nhất trong các NCC hợp lệ", "tradeoff": "Rẻ hơn nhưng về sau đơn sáng mai"},
    {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 287500,
     "order_qty": 14.0, "total_cost": 4025000, "lead_time_hours": 2, "lead_time_days": 1,
     "arrival_at": "2026-11-20T10:00:00", "meets_earliest_preorder": True, "cost_diff_pct_vs_A": 7.33,
     "reason": "Giao nhanh nhất (2 giờ)", "tradeoff": "Đắt hơn khoảng 7% nhưng kịp đơn sáng mai"},
]

# Bột mì qua NCC Hỏa tốc: NCC này để trống contact_email cho Bột mì (TC11)
BOT_MI_OPTIONS = [
    {"option": "A", "sup_id": "NCC_CHINH", "sup_name": "NCC Chính", "unit_price": 20000,
     "order_qty": 10.0, "total_cost": 200000, "lead_time_hours": 24, "lead_time_days": 1},
    {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 23000,
     "order_qty": 10.0, "total_cost": 230000, "lead_time_hours": 2, "lead_time_days": 1},
]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """DB chuẩn trong thư mục tạm, giờ demo 20/11 08:00, outbox trỏ vào thư mục tạm."""
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "po.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    from services import po_service
    monkeypatch.setattr(po_service, "OUTBOX_DIR", tmp_path / "outbox")
    return path


def make_alert(ingredient_id, options, status="OPEN", snapshot_hash=None):
    """Tạo một phiên rà soát SUCCEEDED + một cảnh báo như audit_service sẽ lưu; trả alert_id."""
    now = NOW.isoformat(timespec="seconds")
    run_id = repository.insert_audit_run({
        "run_date": NOW.date().isoformat(), "trigger": "MANUAL", "is_rerun": 0,
        "status": "SUCCEEDED", "started_at": now, "finished_at": now, "alert_count": 1,
    })
    return repository.insert_alert({
        "run_id": run_id, "ingredient_id": ingredient_id, "status": status,
        "main_cause": "SUPPLIER_DELAY", "causes": [{"code": "SUPPLIER_DELAY", "qty": 20.0}],
        "projected_stock": -10.0, "shortage_qty": 13.0, "options_json": json.dumps(options, ensure_ascii=False),
        "explanation": "Câu mẫu", "created_at": now,
        "snapshot_hash": snapshot_hash or repository.compute_snapshot_hash(ingredient_id),
    })


def po_count():
    return len(repository.get_purchase_orders())


def logs(event_type):
    return repository.get_audit_logs(event_type=event_type)
