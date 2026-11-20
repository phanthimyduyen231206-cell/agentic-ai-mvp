"""scheduler.py - Lập lịch rà soát buổi sáng lúc 08:00 (Asia/Ho_Chi_Minh).

Chủ file: Yến (Y04). Căn cứ: Mục 2.1 bước 1 và Mục 11 bản đặc tả.

File này CHỈ đăng ký lịch và gọi run_morning_audit("SCHEDULED"), không chứa luật
nghiệp vụ. Chống chạy trùng (đã có phiên SUCCEEDED hôm nay, đang có phiên RUNNING)
do audit_service tự kiểm tra, nên lịch có lỡ gọi 2 lần cũng chỉ tạo 1 phiên.

Cách chạy (đứng ở thư mục gốc của repo, đã bật .venv):
    python scheduler.py          # chạy nền, chờ đến 08:00 mỗi ngày (Ctrl + C để dừng)
    python scheduler.py --now    # chạy rà soát 1 lần NGAY rồi thoát (dùng khi demo)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TIMEZONE = "Asia/Ho_Chi_Minh"
RUN_HOUR, RUN_MINUTE = 8, 0
JOB_ID = "morning_audit"

logger = logging.getLogger("scheduler")


def load_env() -> None:
    """Đọc file .env (GEMINI_API_KEY, BAKERY_DB_PATH, DEMO_DATE...) như app.py."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_scheduled_audit() -> dict:
    """Việc của lịch 08:00: gọi rà soát với trigger SCHEDULED và ghi kết quả ra màn hình."""
    from services import audit_service

    logger.info("Bắt đầu rà soát theo lịch...")
    result = audit_service.run_morning_audit("SCHEDULED")
    logger.info("Kết quả: %s | phiên #%s | %s", result.get("status"), result.get("run_id"),
                result.get("message"))
    return result


def recover_on_start() -> None:
    """Phiên RUNNING bị bỏ dở quá lâu (app tắt giữa chừng) -> FAILED, để hôm nay chạy lại được."""
    from services import audit_service

    recovered = audit_service.recover_stale_runs()
    if recovered:
        logger.warning("Đã đánh dấu FAILED các phiên bị gián đoạn: %s", recovered)


def build_scheduler():
    """Tạo BlockingScheduler với 1 việc: 08:00 mỗi ngày, giờ Việt Nam."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_scheduled_audit,
        CronTrigger(hour=RUN_HOUR, minute=RUN_MINUTE, timezone=TIMEZONE),
        id=JOB_ID,
        replace_existing=True,   # chạy lại file không đăng ký thêm lịch thứ hai
        max_instances=1,         # lần trước chưa xong thì không chạy chồng
        coalesce=True,           # máy ngủ lỡ nhiều lần thì chỉ bù 1 lần
        misfire_grace_time=3600,  # trễ tối đa 1 tiếng vẫn chạy bù
    )
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lập lịch rà soát buổi sáng 08:00")
    parser.add_argument("--now", action="store_true", help="chạy rà soát 1 lần ngay rồi thoát (demo)")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # in được tiếng Việt trên cửa sổ lệnh Windows
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout, force=True)
    load_env()
    recover_on_start()

    if args.now:
        result = run_scheduled_audit()
        return 0 if result.get("status") in ("SUCCEEDED", "ALREADY_DONE") else 1

    scheduler = build_scheduler()
    logger.info("Đã đăng ký lịch rà soát lúc %02d:%02d mỗi ngày (%s). Bấm Ctrl + C để dừng.",
                RUN_HOUR, RUN_MINUTE, TIMEZONE)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Đã dừng lịch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
