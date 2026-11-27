"""Hậu kiểm số liệu trong câu Gemini viết (MA1 – góp ý của thầy).

Chủ file: Mai Anh. Dùng trong agent/core_agent.py, không gọi LLM, không ghi dữ liệu.

Nguyên tắc: code tính số, Gemini chỉ diễn giải. Prompt đã dặn "chỉ dùng số có trong dữ liệu",
nhưng không thể chỉ tin lời dặn, nên code kiểm lại:

    find_unknown_numbers(text, *sources, extra=()) -> list[str]

- Tách mọi con số trong `text`: số lượng (4.0, 4,5), tiền (360000, 360.000, 360k), phần trăm (15%),
  ngày giờ (2026-11-21T08:00:00, 21/11, 08:00, 8h).
- So với tập số hợp lệ lấy từ `sources` (AlertDraft khi rà soát, kết quả tool khi chat),
  cộng `extra` (vài số cố định như số ngày nhìn tới H = 3).
- Trả danh sách con số không có trong dữ liệu (rỗng = hợp lệ). core_agent thấy danh sách khác rỗng
  thì bỏ câu của Gemini, dùng câu mẫu và ghi trace "numbers_mismatch".

Số phần tử của mỗi danh sách trong dữ liệu cũng hợp lệ ("gồm 1 lô" khi tool trả 1 lô) – phát hiện khi
chạy Gemini thật (MA3): câu đúng "Bơ lạt còn 5 kg, gồm 1 lô" từng bị chặn nhầm.

Không kiểm: mã có cả chữ lẫn số (KEM-L1, PO-004, DH01) vì đó là mã định danh, không phải số liệu.
So sánh theo giá trị tuyệt đối (-2.0 viết thành "âm 2 lít" vẫn khớp), cho phép làm tròn đúng số
chữ số thập phân Gemini viết (0.333 → "0.33").
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Khóa không phải số liệu nghiệp vụ: bỏ qua khi gom số hợp lệ (hash có chữ số ngẫu nhiên).
_SKIP_KEYS = {"snapshot_hash"}

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_DMY_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![\d/])")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])|(?<![\w])(\d{1,2})h(\d{2})?(?![\w])")
_K_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?k(?![\w])", re.IGNORECASE)
_ID_RE = re.compile(r"[\w-]*[A-Za-z][\w-]*\d[\w-]*|[\w-]*\d[\w-]*[A-Za-z][\w-]*")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


class _Allowed:
    """Tập số, ngày, giờ hợp lệ gom từ dữ liệu nguồn."""

    def __init__(self) -> None:
        self.numbers: set[float] = set()
        self.dates: set[tuple[int, int, int]] = set()  # (năm, tháng, ngày)
        self.times: set[tuple[int, int]] = set()       # (giờ, phút)

    # --- gom ---------------------------------------------------------------
    def add_value(self, value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            self.numbers.add(abs(float(value)))
        elif isinstance(value, str):
            self.add_text(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in _SKIP_KEYS:
                    self.add_value(v)
        elif isinstance(value, (list, tuple, set)):
            # Số phần tử cũng là số liệu có thật: "gồm 1 lô", "có 2 nhà cung cấp" (Gemini tự đếm danh sách)
            self.numbers.add(float(len(value)))
            for v in value:
                self.add_value(v)

    def add_text(self, text: str) -> None:
        for kind, val, _raw in _scan(text):
            if kind == "date":
                y, m, d, hm = val
                self.dates.add((y, m, d))
                self.numbers.update({float(d), float(m), float(y)})
                if hm is not None:
                    self._add_time(*hm)
            elif kind == "time":
                self._add_time(*val)
            elif kind == "number":
                self.numbers.update(val)

    def _add_time(self, h: int, mi: int) -> None:
        self.times.add((h, mi))
        self.numbers.update({float(h), float(mi)})

    # --- kiểm --------------------------------------------------------------
    def has_number(self, candidates: list[float], decimals: int) -> bool:
        for c in candidates:
            for a in self.numbers:
                if abs(c - a) <= 1e-6 * max(1.0, a):
                    return True
                if decimals > 0 and abs(round(a, decimals) - c) <= 1e-9:
                    return True
        return False

    def has_date(self, y: int | None, m: int, d: int) -> bool:
        return any((mm, dd) == (m, d) and (y is None or yy == y) for yy, mm, dd in self.dates)

    def has_time(self, h: int, mi: int) -> bool:
        return (h, mi) in self.times or (mi == 0 and float(h) in self.numbers)


def _candidates(token: str) -> tuple[list[float], int]:
    """Các cách hiểu một chuỗi số: '360.000' → 360000 hoặc 360.0; '4,5' → 4.5."""
    seps = re.findall(r"[.,]", token)
    if not seps:
        return [float(token)], 0
    out: list[float] = []
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):  # dấu phân cách hàng nghìn
        out.append(float(re.sub(r"[.,]", "", token)))
    decimals = 0
    if len(seps) == 1:
        out.append(float(token.replace(",", ".")))
        decimals = len(re.split(r"[.,]", token)[1])
    return out, decimals


def _scan(text: str):
    """Duyệt `text`, trả (loại, giá trị, chuỗi gốc). Phần đã đọc được xóa để không đọc 2 lần."""
    s = str(text)

    def cut(match: re.Match) -> str:
        return " " * (match.end() - match.start())

    for m in _ISO_RE.finditer(s):
        hm = (int(m.group(4)), int(m.group(5))) if m.group(4) else None
        yield "date", (int(m.group(1)), int(m.group(2)), int(m.group(3)), hm), m.group(0)
    s = _ISO_RE.sub(cut, s)

    for m in _DMY_RE.finditer(s):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            yield "dmy", (int(m.group(3)) if m.group(3) else None, mo, d), m.group(0)
        else:
            yield "number", [float(d), float(mo)], m.group(0)
    s = _DMY_RE.sub(cut, s)

    for m in _TIME_RE.finditer(s):
        if m.group(1) is not None:
            yield "time", (int(m.group(1)), int(m.group(2))), m.group(0)
        else:
            yield "time", (int(m.group(3)), int(m.group(4) or 0)), m.group(0)
    s = _TIME_RE.sub(cut, s)

    for m in _K_RE.finditer(s):
        values, _ = _candidates(m.group(1))
        yield "number", [v * 1000 for v in values], m.group(0)
    s = _K_RE.sub(cut, s)

    s = _ID_RE.sub(cut, s)  # mã định danh như KEM-L1, PO-004

    for m in _NUM_RE.finditer(s):
        token = m.group(0).strip(".,")
        if token:
            values, _ = _candidates(token)
            yield "number", values, token


def build_allowed(*sources: Any, extra: Iterable[Any] = ()) -> _Allowed:
    allowed = _Allowed()
    for src in sources:
        allowed.add_value(src)
    for x in extra:
        allowed.add_value(x)
    return allowed


def find_unknown_numbers(text: str, *sources: Any, extra: Iterable[Any] = ()) -> list[str]:
    """Các con số trong `text` không có trong dữ liệu nguồn. Rỗng = mọi số đều khớp."""
    if not text:
        return []
    allowed = build_allowed(*sources, extra=extra)
    bad: list[str] = []
    for kind, val, raw in _scan(text):
        if kind == "date":
            y, m, d, hm = val
            ok = allowed.has_date(y, m, d) and (hm is None or allowed.has_time(*hm))
        elif kind == "dmy":
            ok = allowed.has_date(*val)
        elif kind == "time":
            ok = allowed.has_time(*val)
        else:
            _, decimals = _candidates(raw) if re.fullmatch(r"[\d.,]+", raw) else (None, 0)
            ok = allowed.has_number(val, decimals)
        if not ok:
            bad.append(raw.strip())
    return bad
