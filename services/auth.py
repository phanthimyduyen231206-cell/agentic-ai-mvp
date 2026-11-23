"""Đăng nhập: mỗi quản lý một PIN riêng, nhân viên phải nhập tên, khóa tạm khi sai PIN nhiều lần.

Chủ file: Duyên (DU2). Giao diện gọi qua services/ui_api.py. Chữ ký hàm: docs/contracts.md Mục 9.

Cấu hình trong .env:
    MANAGER_PINS=Tên quản lý 1:PIN1,Tên quản lý 2:PIN2
Nếu chưa có MANAGER_PINS thì dùng MANAGER_PIN cũ (một PIN chung), tên người đăng nhập vẫn bắt buộc.
Vai trò lấy từ kết quả đăng nhập ở đây, không lấy từ tham số giao diện gửi lên (đặc tả Mục 8).
Không ghi PIN vào log, không trả PIN ra ngoài.
"""
from __future__ import annotations

import hmac
import os
import time
from typing import NamedTuple

MAX_FAILS = 5            # sai liên tiếp bao nhiêu lần thì khóa
LOCK_SECONDS = 5 * 60    # khóa trong 5 phút

# Đếm lần sai theo tên quản lý, trong bộ nhớ của tiến trình app.
# Dùng đồng hồ thật (time.monotonic), không dùng clock.now(): DEMO_DATE cố định giờ nên
# nếu dùng clock thì thời gian khóa không bao giờ hết.
_failures: dict[str, list] = {}   # key -> [số lần sai liên tiếp, khóa đến (monotonic) hoặc 0]


class LoginResult(NamedTuple):
    user: dict | None    # {"username": str, "role": "MANAGER" | "STAFF"} khi thành công
    message: str         # câu tiếng Việt để giao diện hiện


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def _clean_name(name) -> str:
    return " ".join(str(name or "").split())


def _manager_pins() -> dict[str, tuple[str, str]]:
    """{key tên: (tên hiển thị, PIN)} đọc từ MANAGER_PINS. Dòng sai định dạng thì bỏ qua."""
    pins: dict[str, tuple[str, str]] = {}
    for item in os.getenv("MANAGER_PINS", "").split(","):
        if ":" not in item:
            continue
        name, pin = item.rsplit(":", 1)
        name, pin = _clean_name(name), pin.strip()
        if name and pin:
            pins[_key(name)] = (name, pin)
    return pins


def manager_names() -> list[str]:
    """Tên các quản lý đã cấu hình (để giao diện cho chọn). Rỗng nếu chỉ dùng MANAGER_PIN chung."""
    return [display for display, _ in _manager_pins().values()]


def is_configured() -> bool:
    """Đã có ít nhất một cách đăng nhập quản lý (MANAGER_PINS hoặc MANAGER_PIN)."""
    return bool(_manager_pins()) or bool(os.getenv("MANAGER_PIN", "").strip())


def _locked_seconds_left(key: str) -> int:
    entry = _failures.get(key)
    if not entry or not entry[1]:
        return 0
    left = entry[1] - time.monotonic()
    if left <= 0:
        _failures.pop(key, None)    # hết thời gian khóa thì đếm lại từ đầu
        return 0
    return int(left) + 1


def _record_failure(key: str) -> None:
    entry = _failures.setdefault(key, [0, 0])
    entry[0] += 1
    if entry[0] >= MAX_FAILS:
        entry[1] = time.monotonic() + LOCK_SECONDS


def login_manager(name, pin) -> LoginResult:
    """Đăng nhập quản lý bằng tên + PIN. Sai 5 lần liên tiếp thì khóa tên đó 5 phút."""
    display = _clean_name(name)
    if not display:
        return LoginResult(None, "Vui lòng nhập tên của bạn.")
    if not is_configured():
        return LoginResult(None, "Chưa cấu hình PIN quản lý trong file .env. Xem README.")

    key = _key(display)
    left = _locked_seconds_left(key)
    if left:
        minutes = (left + 59) // 60
        return LoginResult(None, f"Đăng nhập sai quá {MAX_FAILS} lần. Thử lại sau khoảng {minutes} phút.")

    pins = _manager_pins()
    if pins:
        found = pins.get(key)
        expected = found[1] if found else ""
        display = found[0] if found else display
    else:
        expected = os.getenv("MANAGER_PIN", "").strip()

    ok = bool(expected) and hmac.compare_digest(str(pin or "").encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _record_failure(key)
        remaining = MAX_FAILS - _failures.get(key, [0])[0]
        if remaining <= 0:
            return LoginResult(None, f"Đăng nhập sai quá {MAX_FAILS} lần. Thử lại sau 5 phút.")
        return LoginResult(None, f"Tên hoặc mã PIN không đúng. Còn {remaining} lần thử.")

    _failures.pop(key, None)
    return LoginResult({"username": display, "role": "MANAGER"}, f"Xin chào {display}.")


def login_staff(name) -> LoginResult:
    """Nhân viên chỉ xem: không cần PIN nhưng bắt buộc nhập tên để nhật ký ghi đúng người."""
    display = _clean_name(name)
    if not display:
        return LoginResult(None, "Vui lòng nhập tên của bạn.")
    return LoginResult({"username": display, "role": "STAFF"}, f"Xin chào {display}.")


def reset_failures() -> None:
    """Xóa bộ đếm sai PIN (dùng trong test)."""
    _failures.clear()
