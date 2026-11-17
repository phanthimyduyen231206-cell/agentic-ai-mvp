"""Ngày giờ hệ thống theo giờ Việt Nam; cho đặt ngày giả để demo và test.

Chủ file: Duyên (D03). Mọi module lấy "bây giờ" qua now() / today(), không gọi datetime.now() trực tiếp.

Biến môi trường DEMO_DATE:
- để trống: dùng giờ thật;
- "YYYY-MM-DD": dùng ngày đó, giờ lấy theo giờ thật;
- "YYYY-MM-DDTHH:MM": dùng đúng ngày giờ đó.
"""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def now() -> datetime:
    """Giờ Việt Nam hiện tại (không kèm múi giờ), hoặc ngày giờ giả nếu có DEMO_DATE."""
    real = datetime.now(TZ).replace(tzinfo=None, microsecond=0)
    demo = os.getenv("DEMO_DATE", "").strip()
    if not demo:
        return real
    if "T" in demo:
        return datetime.fromisoformat(demo)
    return datetime.combine(date.fromisoformat(demo), real.time())


def today() -> date:
    """Ngày hôm nay theo now()."""
    return now().date()
