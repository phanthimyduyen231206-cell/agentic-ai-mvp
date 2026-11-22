"""Unit test cho services/ui_api.py (Yến, YE1): MOQ, làm tròn MOQ, cảnh báo mềm số lượng đặt."""
from datetime import datetime

import pytest

from data.init_db import init_db
from services import calc, ui_api

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "ui.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


# ---- MOQ -------------------------------------------------------------------
def test_get_moq_reads_supplier_table(db):
    # Dữ liệu mẫu: Kem tươi, NCC Chính có MOQ 2 lít; Bơ lạt, NCC Chính có MOQ 5 kg
    assert ui_api.get_moq("KEM_TUOI", "NCC_CHINH") == 2.0
    assert ui_api.get_moq("BO_LAT", "NCC_CHINH") == 5.0


def test_get_moq_unknown_supplier_defaults_to_1(db):
    assert ui_api.get_moq("KEM_TUOI", "NCC_KHONG_CO") == 1.0


@pytest.mark.parametrize("qty, moq, expected", [
    (23, 10, 30), (4, 2, 4), (3, 2, 4), (13, 5, 15), (0.3, 0.5, 0.5), (0, 2, 0),
])
def test_round_up_moq_same_as_calc(qty, moq, expected):
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(expected)
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(calc.round_up_moq(qty, moq))


def test_round_up_moq_invalid_moq_keeps_qty():
    assert ui_api.round_up_moq(7, 0) == 7


# ---- Cảnh báo mềm ------------------------------------------------------------
def test_warning_when_less_than_shortage():
    msg = ui_api.order_qty_warning(2, 4, "lít")
    assert msg and "ít hơn" in msg and "4 lít" in msg


def test_warning_when_more_than_three_times_shortage():
    msg = ui_api.order_qty_warning(14, 4, "lít")
    assert msg and "gấp hơn 3 lần" in msg


@pytest.mark.parametrize("qty", [4, 8, 12])
def test_no_warning_in_normal_range(qty):
    # từ đúng lượng cần bổ sung đến đúng 3 lần: không nhắc
    assert ui_api.order_qty_warning(qty, 4, "lít") is None


@pytest.mark.parametrize("qty, shortage", [(0, 4), (5, 0), (5, None)])
def test_no_warning_when_input_not_usable(qty, shortage):
    assert ui_api.order_qty_warning(qty, shortage) is None
