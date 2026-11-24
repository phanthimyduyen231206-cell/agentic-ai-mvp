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


def test_get_moq_unknown_supplier_returns_none(db):
    # Không đoán MOQ = 1: thiếu dữ liệu NCC thì trả None để giao diện khóa nút Tạo PO
    assert ui_api.get_moq("KEM_TUOI", "NCC_KHONG_CO") is None


def test_get_moq_supplier_not_selling_this_ingredient_returns_none(db):
    # Dữ liệu mẫu: Mascarpone chỉ có NCC Chính, NCC Hỏa tốc không bán
    assert ui_api.get_moq("MASCARPONE", "NCC_HOA_TOC") is None


@pytest.mark.parametrize("qty, moq, expected", [
    (23, 10, 30), (4, 2, 4), (3, 2, 4), (13, 5, 15), (0.3, 0.5, 0.5), (0, 2, 0),
])
def test_round_up_moq_same_as_calc(qty, moq, expected):
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(expected)
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(calc.round_up_moq(qty, moq))


@pytest.mark.parametrize("moq", [None, 0])
def test_round_up_moq_without_moq_keeps_qty(moq):
    assert ui_api.round_up_moq(7, moq) == 7


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


# ---- Đăng nhập qua services/auth.py (YE2) -----------------------------------
@pytest.fixture()
def pins(monkeypatch):
    from services import auth
    monkeypatch.setenv("MANAGER_PINS", "Duyên:2468,Yến:1357")
    monkeypatch.delenv("MANAGER_PIN", raising=False)
    auth.reset_failures()
    yield
    auth.reset_failures()


def test_login_manager_ok_uses_name_from_config(pins):
    user, msg = ui_api.login_manager("  yến ", "1357")
    assert user == {"username": "Yến", "role": "MANAGER"}
    assert "Xin chào" in msg


def test_login_manager_wrong_pin_counts_down(pins):
    user, msg = ui_api.login_manager("Yến", "0000")
    assert user is None and "Còn 4 lần thử" in msg


def test_login_manager_locked_after_5_wrong_pins(pins):
    for _ in range(5):
        ui_api.login_manager("Yến", "0000")
    user, msg = ui_api.login_manager("Yến", "1357")      # đúng PIN nhưng đang bị khóa
    assert user is None and "Thử lại sau" in msg
    user, _ = ui_api.login_manager("Duyên", "2468")      # khóa theo từng tên
    assert user is not None


@pytest.mark.parametrize("name", ["", "   "])
def test_login_requires_name(pins, name):
    assert ui_api.login_manager(name, "1357")[0] is None
    assert ui_api.login_staff(name)[0] is None


def test_login_staff_ok():
    user, _ = ui_api.login_staff(" Lan ")
    assert user == {"username": "Lan", "role": "STAFF"}


# ---- Chat gửi kèm lịch sử (YE2) -----------------------------------------------
def _history(n):
    return [{"question": f"Câu {i}", "answer": f"Đáp {i}", "refused": False, "fallback": False}
            for i in range(n)]


def test_ask_chat_sends_last_turns_when_supported(monkeypatch):
    seen = {}

    def fake(question, user, history=None, llm=None):
        seen["history"] = history
        return {"answer": "ok", "refused": False, "fallback": True}

    monkeypatch.setattr(ui_api._chat, "ask_chat", fake)
    result = ui_api.ask_chat("Còn bao nhiêu?", {"username": "Yến", "role": "STAFF"}, history=_history(6))
    assert result == {"answer": "ok", "refused": False, "fallback": True}
    assert [t["question"] for t in seen["history"]] == ["Câu 2", "Câu 3", "Câu 4", "Câu 5"]
    assert set(seen["history"][0]) == {"question", "answer", "refused"}


def test_ask_chat_old_chat_service_without_history(monkeypatch):
    monkeypatch.setattr(ui_api._chat, "ask_chat",
                        lambda question, user: {"answer": "ok", "refused": False})
    result = ui_api.ask_chat("Còn bao nhiêu?", {"username": "Yến", "role": "STAFF"}, history=_history(2))
    assert result == {"answer": "ok", "refused": False, "fallback": False}
