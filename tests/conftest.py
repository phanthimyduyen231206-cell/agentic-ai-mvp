"""Cấu hình chung cho pytest (v2 – MA4).

Test không bao giờ gửi trace lên LangSmith thật, kể cả khi .env của máy đang bật LANGSMITH_TRACING=true
(GeminiLLM() gọi load_dotenv() sẽ nạp .env vào os.environ; load_dotenv không ghi đè biến đã đặt ở đây).
Test cần tracing (tests/unit/test_tracing.py) tự bật lại bằng LangSmith giả.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_real_langsmith(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
