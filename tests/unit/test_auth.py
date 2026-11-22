"""Unit test services/auth.py (DU2): PIN riêng từng quản lý, tên bắt buộc, khóa tạm khi sai PIN."""
import pytest

from services import auth


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MANAGER_PINS", "Duyên:2468, Mai Anh : 1357 ,dòng-sai, :999")
    monkeypatch.delenv("MANAGER_PIN", raising=False)
    auth.reset_failures()
    yield
    auth.reset_failures()


def test_dung_ten_dung_pin():
    r = auth.login_manager("Duyên", "2468")
    assert r.user == {"username": "Duyên", "role": "MANAGER"}


def test_ten_khong_phan_biet_hoa_thuong_va_khoang_trang():
    r = auth.login_manager("  mai   ANH ", "1357")
    assert r.user == {"username": "Mai Anh", "role": "MANAGER"}


def test_pin_cua_nguoi_khac_bi_tu_choi():
    r = auth.login_manager("Duyên", "1357")
    assert r.user is None and "không đúng" in r.message


def test_ten_khong_co_trong_danh_sach():
    assert auth.login_manager("Người lạ", "2468").user is None


def test_ten_bat_buoc():
    assert auth.login_manager("   ", "2468").user is None
    assert auth.login_staff("").user is None


def test_danh_sach_ten_quan_ly_bo_dong_sai():
    assert sorted(auth.manager_names()) == ["Duyên", "Mai Anh"]


def test_nhan_vien_chi_can_ten():
    assert auth.login_staff(" Yến ").user == {"username": "Yến", "role": "STAFF"}


def test_sai_5_lan_thi_khoa_ca_khi_nhap_dung(monkeypatch):
    for _ in range(auth.MAX_FAILS):
        assert auth.login_manager("Duyên", "0000").user is None
    r = auth.login_manager("Duyên", "2468")
    assert r.user is None and "Thử lại sau" in r.message
    # tên khác không bị ảnh hưởng
    assert auth.login_manager("Mai Anh", "1357").user is not None


def test_het_thoi_gian_khoa_thi_dang_nhap_lai_duoc(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    for _ in range(auth.MAX_FAILS):
        auth.login_manager("Duyên", "0000")
    assert auth.login_manager("Duyên", "2468").user is None
    now[0] += auth.LOCK_SECONDS + 1
    assert auth.login_manager("Duyên", "2468").user is not None


def test_dang_nhap_dung_thi_dem_lai_tu_dau():
    for _ in range(auth.MAX_FAILS - 1):
        auth.login_manager("Duyên", "0000")
    assert auth.login_manager("Duyên", "2468").user is not None
    assert "Còn 4 lần" in auth.login_manager("Duyên", "0000").message


def test_du_phong_manager_pin_chung(monkeypatch):
    monkeypatch.delenv("MANAGER_PINS", raising=False)
    monkeypatch.setenv("MANAGER_PIN", "8642")
    assert auth.manager_names() == []
    assert auth.login_manager("Phương Anh", "8642").user == {"username": "Phương Anh", "role": "MANAGER"}
    assert auth.login_manager("Phương Anh", "1111").user is None


def test_chua_cau_hinh_pin_thi_khong_cho_dang_nhap(monkeypatch):
    monkeypatch.delenv("MANAGER_PINS", raising=False)
    monkeypatch.delenv("MANAGER_PIN", raising=False)
    r = auth.login_manager("Duyên", "")
    assert r.user is None and "Chưa cấu hình" in r.message and "1234" not in r.message


def test_khong_lo_pin_trong_thong_bao():
    for pin in ("0000", "2468"):
        assert "2468" not in auth.login_manager("Duyên", pin).message
