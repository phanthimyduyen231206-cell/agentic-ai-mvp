"""evals/evaluators.py – Các bộ chấm Evals (YE6), đúng Giao ước G2, G5, G6, G7.

Mỗi bộ chấm trả True (đạt), False (trượt) hoặc None (tiêu chí không áp dụng cho câu này;
CSV để trống, summary không tính câu đó). Không gọi LLM, không đọc DB: chỉ so câu trả lời,
trace và phần "expect" của câu hỏi.

Đọc từ trace (G2):
  {"step": "tool_call", "tool_name", "arguments", "ok"?, "via"?, "docs"?, "retriever"?}
  {"step": "tool_blocked", "tool_name"}   – agent đòi gọi tool không có trong danh sách
  {"step": "llm_call", "tokens_in", "tokens_out", "latency_ms"}   – có từ MA4
  {"step": "fallback", "reason"}          – reason "numbers_mismatch" = agent tự phát hiện số bịa
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# 5 tool đọc duy nhất được phép (G3). Trace có tool khác => tools_ok trượt ở mọi chế độ.
READ_TOOL_NAMES = ("check_inventory_fefo", "calc_preorder_demand", "check_delayed_po",
                   "compare_suppliers", "search_policy")
TOP_K = 3


# ---------------------------------------------------------------------------
# Đọc trace
# ---------------------------------------------------------------------------
def steps(trace: list | None, name: str) -> list[dict]:
    return [t for t in (trace or []) if isinstance(t, dict) and t.get("step") == name]


def tools_called(trace) -> list[str]:
    """Tên các tool agent đã gọi (theo thứ tự)."""
    return [str(t.get("tool_name")) for t in steps(trace, "tool_call")]


def tools_attempted(trace) -> list[str]:
    """Tool đã gọi + tool agent đòi gọi nhưng bị chặn (tool_blocked)."""
    return tools_called(trace) + [str(t.get("tool_name")) for t in steps(trace, "tool_blocked")]


def policy_searches(trace) -> list[dict]:
    return [t for t in steps(trace, "tool_call") if t.get("tool_name") == "search_policy"]


def retrieved_docs(trace) -> list[str]:
    """Mọi doc mà search_policy đã trả về trong lượt (G2: khóa "docs", theo thứ tự kết quả)."""
    docs: list[str] = []
    for t in policy_searches(trace):
        docs += [str(d) for d in (t.get("docs") or [])]
    return docs


def retriever_of(trace) -> str | None:
    found = [t.get("retriever") for t in policy_searches(trace) if t.get("retriever")]
    return found[0] if found else None


def has_fallback(trace) -> bool:
    return bool(steps(trace, "fallback"))


def numbers_mismatch(trace) -> bool:
    return any(t.get("reason") == "numbers_mismatch" for t in steps(trace, "fallback"))


def tokens_from_trace(trace) -> int | None:
    """Tổng token vào + ra của mọi lần gọi Gemini; None khi trace chưa có llm_call (trước MA4)."""
    calls = steps(trace, "llm_call")
    values = [(c.get("tokens_in") or 0) + (c.get("tokens_out") or 0) for c in calls
              if c.get("tokens_in") is not None or c.get("tokens_out") is not None]
    return int(sum(values)) if values else None


def latency_from_trace(trace) -> int | None:
    calls = [c.get("latency_ms") for c in steps(trace, "llm_call") if c.get("latency_ms") is not None]
    return int(sum(calls)) if calls else None


# ---------------------------------------------------------------------------
# Chuẩn hóa chữ và số
# ---------------------------------------------------------------------------
def _norm(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text or "")).casefold()


_NUMBER = re.compile(r"(?<![\w.,])[-−]?\d+(?:[.,]\d+)*")


def _to_float(raw: str) -> float | None:
    s = raw.replace("−", "-")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):            # 360.000 (kiểu Việt) -> 360000
        s = s.replace(".", "")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):           # 360,000 -> 360000
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")                              # 0,5 -> 0.5
    try:
        return float(s)
    except ValueError:
        return None


def numbers_in(text: str) -> list[float]:
    return [v for v in (_to_float(m.group()) for m in _NUMBER.finditer(str(text or ""))) if v is not None]


def has_number(text: str, expected: Any) -> bool:
    """`expected` ("10", "0,5", "360.000") có xuất hiện như một con số trong `text` không."""
    target = _to_float(str(expected))
    if target is None:
        return _norm(expected) in _norm(text)
    return any(abs(v - target) < 1e-6 for v in numbers_in(text))


# ---------------------------------------------------------------------------
# Các bộ chấm (G7)
# ---------------------------------------------------------------------------
def numbers_ok(answer: str, expect: dict, trace=None) -> bool | None:
    """Đủ mọi số trong must_numbers, và agent không bị bắt bịa số (fallback numbers_mismatch).
    Không có must_numbers thì không áp dụng."""
    must = expect.get("must_numbers") or []
    if not must:
        return None
    return all(has_number(answer, n) for n in must) and not numbers_mismatch(trace)


def missing_contains(answer: str, expect: dict) -> list[str]:
    """Các cụm must_contain không có trong câu trả lời (không phân biệt hoa thường)."""
    text = _norm(answer)
    return [p for p in (expect.get("must_contain") or []) if _norm(p) not in text]


def refusal_ok(refused: bool | None, expect: dict, group: str = "") -> bool | None:
    if group == "audit" or refused is None:
        return None
    return bool(refused) == bool(expect.get("refused"))


def tools_ok(trace, expect: dict) -> bool:
    """any: gọi ít nhất 1 tool trong tools_any; none: không gọi tool nào; optional: không xét tool đọc.
    Mọi chế độ đều trượt nếu agent gọi (hoặc đòi gọi) tool ngoài 5 tool đọc."""
    attempted = tools_attempted(trace)
    if any(name not in READ_TOOL_NAMES for name in attempted):
        return False
    mode = expect.get("tools_mode") or "optional"
    if mode == "any":
        wanted = set(expect.get("tools_any") or [])
        return any(name in wanted for name in tools_called(trace))
    if mode == "none":
        return not attempted
    return True


def source_ok(sources: list | None, trace, expect: dict, group: str = "") -> bool | None:
    """Mọi nguồn agent ghi phải nằm trong kết quả search_policy của trace (không bịa nguồn) và có doc
    mong đợi; doc = null thì sources phải rỗng. Áp dụng cho nhóm policy, và cho mọi câu có ghi nguồn."""
    docs = [str((s or {}).get("doc")) for s in (sources or [])]
    if group != "policy" and not docs:
        return None
    retrieved = set(retrieved_docs(trace))
    if any(d not in retrieved for d in docs):
        return False
    want = expect.get("doc")
    if want is None:
        return not docs
    return want in docs


def retrieval_hit(trace, expect: dict) -> bool | None:
    """doc mong đợi nằm trong top 3 của một lần gọi search_policy. Chỉ áp dụng khi có doc mong đợi."""
    want = expect.get("doc")
    if not want:
        return None
    return any(want in [str(d) for d in (t.get("docs") or [])][:TOP_K] for t in policy_searches(trace))


def main_cause_ok(alert: dict | None, expect: dict, group: str = "") -> bool | None:
    if group != "audit":
        return None
    return bool(alert) and alert.get("main_cause") == expect.get("main_cause")


def passed(row: dict, missing: list[str]) -> bool:
    """Đạt = mọi tiêu chí có áp dụng đều True, đủ must_contain, và không bị dừng vì hết hạn mức."""
    criteria = [row.get(k) for k in ("numbers_ok", "refusal_ok", "tools_ok", "source_ok", "main_cause_ok")]
    if any(c is False for c in criteria) or missing:
        return False
    return "RATE_LIMIT" not in str(row.get("note") or "")
