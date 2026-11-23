"""Unit test cho scheduler.py (Y04) – DB tạm, FakeLLM, không chờ đến 08:00 thật."""
import functools
from datetime import datetime

import pytest

import scheduler
from data import repository
from data.init_db import init_db
from services import audit_service
from tests.fake_llm import FakeLLM

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "sched.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    # Không gọi Gemini thật trong test
    monkeypatch.setattr(audit_service, "run_morning_audit",
                        functools.partial(audit_service.run_morning_audit, llm=FakeLLM()))
    return path


def test_now_creates_one_scheduled_run(db):
    assert scheduler.main(["--now"]) == 0
    runs = repository.get_audit_runs()
    assert len(runs) == 1
    assert runs[0]["trigger"] == "SCHEDULED"
    assert runs[0]["status"] == "SUCCEEDED"
    assert runs[0]["alert_count"] == 4
    started = repository.get_audit_logs(event_type="RUN_STARTED")
    assert started[0]["actor"] == "scheduler"


def test_now_twice_same_day_does_not_create_second_run(db):
    assert scheduler.main(["--now"]) == 0
    assert scheduler.main(["--now"]) == 0          # ALREADY_DONE vẫn coi là bình thường
    assert len(repository.get_audit_runs()) == 1


def test_registers_single_job_at_0800_vietnam_time():
    pytest.importorskip("apscheduler")
    sched = scheduler.build_scheduler()
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    fields = {f.name: str(f) for f in jobs[0].trigger.fields}
    assert fields["hour"] == "8" and fields["minute"] == "0"
    assert str(jobs[0].trigger.timezone) == "Asia/Ho_Chi_Minh"
