"""TC13 – Chạy rà soát trùng (Mai Anh).

Cách thực hiện: bấm "Chạy rà soát" 2 lần liên tiếp.
Kết quả cần đạt: chỉ 1 phiên RUNNING; lần 2 bị từ chối.
"""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services import calc  # noqa: E402
from services.audit_service import run_morning_audit  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc13.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc13_second_click_rejected_while_running(db, monkeypatch):
    started = threading.Event()
    seen_running = []

    def slow_compute(run_date):
        started.set()
        time.sleep(0.5)  # phiên 1 đang chạy
        seen_running.append(len(repo.get_audit_runs(status="RUNNING")))
        return []

    monkeypatch.setattr(calc, "compute_alerts", slow_compute, raising=False)

    first = {}
    t = threading.Thread(target=lambda: first.update(run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())))
    t.start()
    assert started.wait(5)
    second = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())  # bấm lần 2
    t.join(10)

    assert second["status"] == "REJECTED_DUPLICATE"
    assert first["status"] == "SUCCEEDED"
    assert seen_running == [1]  # lúc đang chạy chỉ có đúng 1 phiên RUNNING
    assert len(repo.get_audit_runs()) == 1
    assert len(repo.get_audit_logs(event_type="RUN_REJECTED_DUPLICATE")) == 1


def test_tc13_database_blocks_second_running_row(db):
    """Lớp bảo vệ cuối ở DB: index ux_run_running không cho 2 dòng RUNNING."""
    import sqlite3

    repo.insert_audit_run({"run_date": "2026-11-20", "trigger": "MANUAL", "status": "RUNNING",
                           "started_at": "2026-11-20T08:00:00"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_audit_run({"run_date": "2026-11-20", "trigger": "SCHEDULED", "status": "RUNNING",
                               "started_at": "2026-11-20T08:00:01"})
