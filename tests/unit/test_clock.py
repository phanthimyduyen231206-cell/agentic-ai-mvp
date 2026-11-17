from datetime import date, datetime

from services import clock


def test_demo_date_sets_today(monkeypatch):
    monkeypatch.setenv("DEMO_DATE", "2026-11-20")
    assert clock.today() == date(2026, 11, 20)


def test_demo_datetime_sets_exact_time(monkeypatch):
    monkeypatch.setenv("DEMO_DATE", "2026-11-20T08:00")
    assert clock.now() == datetime(2026, 11, 20, 8, 0)


def test_without_demo_date_uses_real_time(monkeypatch):
    monkeypatch.delenv("DEMO_DATE", raising=False)
    assert abs((clock.now() - datetime.now(clock.TZ).replace(tzinfo=None)).total_seconds()) < 5
