"""TC01 – Ngày không thiếu hàng (Phương Anh, P06).

Đặc tả Mục 13: seed --scenario no_shortage, chạy rà soát
-> không tạo cảnh báo, không tạo PO; phiên SUCCEEDED; có log RUN_SUCCEEDED.

Dùng dữ liệu no_shortage (P02), calc.compute_alerts (Q02), audit_service.run_morning_audit (M05).
Gemini thay bằng FakeLLM nên chạy không cần mạng.
"""
from datetime import datetime

import pytest

from data import repository
from data.init_db import init_db
from services import calc
from services.audit_service import run_morning_audit
from tests.fake_llm import FakeLLM

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "Quản lý", "role": "MANAGER"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """DB kịch bản no_shortage trong thư mục tạm, giờ demo 20/11/2026 08:00."""
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc01.db", "no_shortage", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_TC01_compute_alerts_tra_rong(db):
    """Bước tính số (Q02): đủ hàng thì không có nguyên liệu nào bị cảnh báo."""
    assert calc.compute_alerts(NOW.date()) == []


def test_TC01_ra_soat_khong_tao_canh_bao_khong_tao_po(db):
    po_before = repository.get_purchase_orders()
    llm = FakeLLM()

    result = run_morning_audit("MANUAL", user=MANAGER, llm=llm)

    # Phiên rà soát thành công, không có cảnh báo
    assert result["status"] == "SUCCEEDED"
    assert result["alert_ids"] == []
    run = repository.get_audit_run(result["run_id"])
    assert run["status"] == "SUCCEEDED"
    assert run["finished_at"] is not None
    assert repository.get_alerts() == []

    # Không tạo PO mới, PO cũ không đổi
    assert repository.get_purchase_orders() == po_before

    # Nhật ký: có RUN_STARTED và RUN_SUCCEEDED, không có cảnh báo / lỗi / giao trễ
    def count(event):
        return len(repository.get_audit_logs(event_type=event, run_id=result["run_id"]))

    assert count("RUN_STARTED") == 1
    assert count("RUN_SUCCEEDED") == 1
    assert count("ALERT_CREATED") == 0
    assert count("RUN_FAILED") == 0
    assert len(repository.get_audit_logs(event_type="DELIVERY_DELAYED")) == 0

    # Không thiếu hàng thì không cần gọi Gemini
    assert llm.call_count == 0


def test_TC01_lich_tu_dong_cung_khong_tao_canh_bao(db):
    """Rà soát tự động lúc 08:00 (scheduler, không có user) cho cùng kết quả."""
    result = run_morning_audit("SCHEDULED", llm=FakeLLM())
    assert result["status"] == "SUCCEEDED"
    assert result["alert_ids"] == []
    assert repository.get_audit_run(result["run_id"])["trigger"] == "SCHEDULED"
    assert len(repository.get_audit_logs(event_type="RUN_SUCCEEDED")) == 1
