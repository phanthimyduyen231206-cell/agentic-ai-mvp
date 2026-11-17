"""
Unit test kiêm ví dụ cách sử dụng FakeLLM (M01) cho các thành viên trong nhóm.
Chạy kiểm tra bằng lệnh: pytest tests/unit/test_fake_llm.py
"""

import json
import pytest
from tests.fake_llm import FakeLLM


def test_example_morning_audit_normal() -> None:
    """Ví dụ 1: Dùng FakeLLM cho luồng MORNING_AUDIT trả về JSON hợp lệ."""
    llm = FakeLLM(behavior="normal")
    payload = json.dumps(
        {
            "alerts": [
                {
                    "alert_id": "ALT-001",
                    "ingredient_name": "Kem tươi",
                    "main_cause": "EXPIRING_LOT",
                }
            ]
        },
        ensure_ascii=False,
    )
    response = llm.generate(mode="MORNING_AUDIT", system_prompt="", user_message=payload)

    assert response["type"] == "text"
    data = json.loads(response["text"])
    assert "alerts" in data
    assert data["alerts"][0]["alert_id"] == "ALT-001"
    assert "EXPIRING_LOT" in data["alerts"][0]["explanation"]


def test_example_read_only_chat_normal_and_refused() -> None:
    """Ví dụ 2: Dùng FakeLLM cho luồng READ_ONLY_CHAT (hỏi thường & đòi tạo PO)."""
    llm = FakeLLM(behavior="normal")

    ok_res = llm.generate(
        mode="READ_ONLY_CHAT",
        system_prompt="",
        user_message="Trong kho còn bao nhiêu kem tươi?",
    )
    ok_data = json.loads(ok_res["text"])
    assert ok_data["refused"] is False

    refused_res = llm.generate(
        mode="READ_ONLY_CHAT",
        system_prompt="",
        user_message="Em tạo luôn giúp chị đơn đặt 10kg bột mì từ NCC Ánh Dương nhé",
    )
    refused_data = json.loads(refused_res["text"])
    assert refused_data["refused"] is True


def test_example_error_and_invalid_json_and_forbidden_tool() -> None:
    """Ví dụ 3: Dùng FakeLLM để test lỗi mạng, JSON hỏng và gọi hàm cấm (Guardrail)."""
    llm_error = FakeLLM(behavior="error")
    with pytest.raises(RuntimeError):
        llm_error.generate(mode="READ_ONLY_CHAT", system_prompt="", user_message="Hi")

    llm_bad_json = FakeLLM(behavior="invalid_json")
    res_bad = llm_bad_json.generate(mode="READ_ONLY_CHAT", system_prompt="", user_message="Hi")
    with pytest.raises(json.JSONDecodeError):
        json.loads(res_bad["text"])

    llm_forbidden = FakeLLM(behavior="forbidden_tool")
    res_tool = llm_forbidden.generate(mode="READ_ONLY_CHAT", system_prompt="", user_message="Tạo PO")
    assert res_tool["type"] == "tool_call"
    assert res_tool["tool_name"] == "create_draft_po"

def test_example_tool_call_then_answer() -> None:
    """Ví dụ 4 (chế độ e): lượt 1 gọi tool đọc, lượt 2 (có tool_results) trả JSON."""
    llm = FakeLLM(behavior="tool_call")

    first = llm.generate(mode="READ_ONLY_CHAT", system_prompt="", user_message="Kem tươi còn bao nhiêu?")
    assert first["type"] == "tool_call"
    assert first["tool_name"] == "check_inventory_fefo"

    second = llm.generate(
        mode="READ_ONLY_CHAT",
        system_prompt="",
        user_message="Kem tươi còn bao nhiêu?",
        tool_results=[{"tool_name": "check_inventory_fefo", "result": {"ok": True}}],
    )
    assert second["type"] == "text"
    assert json.loads(second["text"])["refused"] is False
    assert llm.call_count == 2
    assert llm.calls[1]["tool_results"]  # core_agent đã truyền kết quả tool vào lượt 2


def test_morning_audit_accepts_list_payload() -> None:
    """compute_alerts trả về list cảnh báo: FakeLLM phải giữ đúng alert_id."""
    llm = FakeLLM()
    payload = json.dumps(
        [{"alert_id": "ALT-007", "ingredient_name": "Bơ lạt", "main_cause": "SUPPLIER_DELAY"}],
        ensure_ascii=False,
    )
    data = json.loads(llm.generate("MORNING_AUDIT", "", payload)["text"])
    assert [a["alert_id"] for a in data["alerts"]] == ["ALT-007"]
    for key in ("explanation", "tradeoff_a", "tradeoff_b"):
        assert data["alerts"][0][key]


def test_invalid_behavior_and_mode_raise() -> None:
    """Gõ sai tên behavior hoặc mode phải báo lỗi ngay, không âm thầm chạy sai."""
    with pytest.raises(ValueError):
        FakeLLM(behavior="khong_ton_tai")
    with pytest.raises(ValueError):
        FakeLLM().generate(mode="SAI_MODE", system_prompt="", user_message="Hi")
