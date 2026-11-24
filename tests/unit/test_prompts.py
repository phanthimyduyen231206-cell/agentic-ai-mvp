"""
Unit test cho agent/prompts.py (MA-02).
Chạy: pytest tests/unit/test_prompts.py
"""

import json

import pytest

from agent.prompts import (
    CAUSE_CODES,
    MORNING_AUDIT_OUTPUT_EXAMPLE,
    MORNING_AUDIT_PROMPT,
    READ_ONLY_CHAT_OUTPUT_EXAMPLE,
    READ_ONLY_CHAT_PROMPT,
    READ_ONLY_TOOLS,
    REFUSAL_MESSAGE,
    RULE_LOCK_LINE,
    get_system_prompt,
)
from tests.fake_llm import FakeLLM


def test_both_prompts_keep_rule_lock_line() -> None:
    """Dòng chống prompt injection phải có nguyên văn trong cả 2 prompt."""
    assert RULE_LOCK_LINE in MORNING_AUDIT_PROMPT
    assert RULE_LOCK_LINE in READ_ONLY_CHAT_PROMPT


def test_chat_prompt_lists_only_read_tools_and_refusal() -> None:
    for tool in READ_ONLY_TOOLS:
        assert tool in READ_ONLY_CHAT_PROMPT
    # Không được nhắc tới hàm ghi như một công cụ có thể gọi
    assert "create_draft_po" not in READ_ONLY_CHAT_PROMPT
    assert "write_audit_log" not in READ_ONLY_CHAT_PROMPT
    # Câu từ chối hướng sang Tab 2 "Rà soát"
    assert 'Tab 2 "Rà soát"' in REFUSAL_MESSAGE
    assert REFUSAL_MESSAGE in READ_ONLY_CHAT_PROMPT


def test_prompts_require_numbers_from_data_only() -> None:
    assert "Chỉ dùng con số có trong dữ liệu" in MORNING_AUDIT_PROMPT
    assert "Không đoán số" in READ_ONLY_CHAT_PROMPT


def test_examples_in_prompts_are_valid_json() -> None:
    """Ví dụ JSON nhúng trong prompt phải đúng y như hằng số (không lệch khi sửa)."""
    assert json.dumps(MORNING_AUDIT_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2) in MORNING_AUDIT_PROMPT
    assert json.dumps(READ_ONLY_CHAT_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2) in READ_ONLY_CHAT_PROMPT


def test_examples_match_fake_llm_schema() -> None:
    """Schema ví dụ trong prompt phải khớp đầu ra FakeLLM, để test và Gemini thật cùng một dạng."""
    llm = FakeLLM()

    morning = json.loads(llm.generate("MORNING_AUDIT", MORNING_AUDIT_PROMPT, "{}")["text"])
    assert set(morning) == set(MORNING_AUDIT_OUTPUT_EXAMPLE)
    assert set(morning["alerts"][0]) == set(MORNING_AUDIT_OUTPUT_EXAMPLE["alerts"][0])

    chat = json.loads(llm.generate("READ_ONLY_CHAT", READ_ONLY_CHAT_PROMPT, "Kem tươi còn bao nhiêu?")["text"])
    assert set(chat) == set(READ_ONLY_CHAT_OUTPUT_EXAMPLE)


def test_get_system_prompt() -> None:
    assert get_system_prompt("MORNING_AUDIT") is MORNING_AUDIT_PROMPT
    assert get_system_prompt("READ_ONLY_CHAT") is READ_ONLY_CHAT_PROMPT
    with pytest.raises(ValueError):
        get_system_prompt("CHAT_GHI")


def test_morning_prompt_follows_contracts() -> None:
    """contracts mục 7: đầu ra luồng sáng khóa theo ingredient_id (chưa có alert_id)."""
    item = MORNING_AUDIT_OUTPUT_EXAMPLE["alerts"][0]
    assert set(item) == {"ingredient_id", "explanation", "tradeoff_a", "tradeoff_b"}
    assert "alert_id" not in MORNING_AUDIT_PROMPT
    for code in CAUSE_CODES:
        assert code in MORNING_AUDIT_PROMPT
    # Phải hướng dẫn cả trường hợp 1 phương án và không có phương án (contracts mục 5)
    assert "options chỉ có A" in MORNING_AUDIT_PROMPT
    assert "open_po_id" in MORNING_AUDIT_PROMPT


def test_chat_prompt_has_tool_signatures_from_contracts() -> None:
    for sig in (
        "check_inventory_fefo(ingredient_id=None, at=None, days=0)",
        "calc_preorder_demand(ingredient_id=None, days=3)",
        "check_delayed_po(ingredient_id=None, days=3)",
        "compare_suppliers(ingredient_id)",
    ):
        assert sig in READ_ONLY_CHAT_PROMPT


def test_prompt_examples_do_not_use_real_kem_tuoi_numbers() -> None:
    """MA1 – góp ý của thầy: ví dụ trong prompt dùng nguyên liệu giả định, không chép số Kem tươi thật."""
    real_values = ["-2.0", "360000", "414000", "15.0", "90000", "103500", "2026-11-21T08:00:00",
                   "2026-11-20T10:00:00", "2026-11-20T20:00:00", "2026-11-25T20:00:00", "KEM-L1", "KEM-L2",
                   "Kem tươi Whipping Cream"]
    for prompt in (MORNING_AUDIT_PROMPT, READ_ONLY_CHAT_PROMPT):
        for value in real_values:
            assert value not in prompt, value
    assert MORNING_AUDIT_OUTPUT_EXAMPLE["alerts"][0]["ingredient_id"] == "NGUYEN_LIEU_X"


def test_chat_prompt_explains_history_and_rounds() -> None:
    """MA2: prompt nói rõ khung lịch sử và số vòng tool khớp core_agent."""
    from agent import core_agent
    from agent.prompts import CURRENT_QUESTION_HEADER, HISTORY_HEADER, MAX_TOOL_ROUNDS_IN_PROMPT

    assert MAX_TOOL_ROUNDS_IN_PROMPT == core_agent.MAX_TOOL_ROUNDS
    assert HISTORY_HEADER in READ_ONLY_CHAT_PROMPT and CURRENT_QUESTION_HEADER in READ_ONLY_CHAT_PROMPT
