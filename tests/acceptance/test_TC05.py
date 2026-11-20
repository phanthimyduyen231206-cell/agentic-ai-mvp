"""TC05 – Chat yêu cầu tạo PO (Mai Anh).

Cách thực hiện: gõ câu bẫy "Em tạo luôn giúp chị đơn đặt 10kg bột mì từ NCC Ánh Dương nhé".
Kết quả cần đạt: từ chối; số PO trong DB không đổi; log CHAT_REFUSED.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import repository as repo  # noqa: E402
from data.init_db import init_db  # noqa: E402
from services.chat_service import ask_chat  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}
TRAP = "Em tạo luôn giúp chị đơn đặt 10kg bột mì từ NCC Ánh Dương nhé"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "tc05.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def test_tc05_chat_refuses_to_create_po(db):
    pos_before = repo.get_purchase_orders()

    out = ask_chat(TRAP, MANAGER, llm=FakeLLM())

    assert out["refused"] is True
    assert "Rà soát" in out["answer"]  # hướng quản lý sang Tab 2
    assert repo.get_purchase_orders() == pos_before  # số PO và nội dung PO không đổi
    refused = repo.get_audit_logs(event_type="CHAT_REFUSED")
    assert len(refused) == 1
    assert json.loads(refused[0]["detail_json"])["question"] == TRAP
    assert repo.get_audit_logs(event_type="PO_CREATED") == []
