"""Gọi Gemini, vòng lặp tool (tối đa 6 vòng + 1 lượt tổng hợp), kiểm JSON, câu mẫu dự phòng. Không ghi dữ liệu.

Chủ file: Mai Anh (M03). Chữ ký hàm: xem docs/contracts.md mục 7.

    run_agent(mode, payload, llm=None, tools=None, trace=None) -> dict

- mode = "MORNING_AUDIT": payload = {"run_date", "alerts": [AlertDraft]}
  → {"alerts": [{"ingredient_id", "explanation", "tradeoff_a", "tradeoff_b", "ai_fallback"}]}
- mode = "READ_ONLY_CHAT": payload = {"question", "history"?} → {"answer", "refused"}
  history = [{"question", "answer", "refused"}] – tối đa 4 lượt gần nhất, mỗi câu ≤ 300 ký tự (MA2).
- llm=None → Gemini thật (GeminiLLM); test truyền FakeLLM() (tests/fake_llm.py).
- tools=None → 4 tool đọc của agent/tools.py (Q03); khi Q03 chưa có hàm thì dùng
  4 tool giả trả dữ liệu mẫu đúng contracts.
- trace: truyền một list để nhận nhật ký từng bước (tool đã gọi, tool bị chặn,
  lý do dùng câu dự phòng). audit_service / chat_service dùng để ghi audit log.
- Vòng tool (MA2): chạy đủ mọi function_call của một lượt (gọi song song), gửi kết quả lại bằng
  function_response; hết 6 vòng thì gọi thêm 1 lượt không có tool để Gemini tổng hợp câu trả lời.
- Gemini lỗi, quá giờ, lượt tổng hợp vẫn đòi tool, hoặc JSON sai → trả câu mẫu do code sinh, KHÔNG raise.
- Hậu kiểm số liệu (agent/output_check.py): câu Gemini có con số không nằm trong dữ liệu đầu vào
  (AlertDraft khi rà soát, kết quả tool khi chat) → bỏ câu đó, dùng câu mẫu, trace "numbers_mismatch".
- Đầu ra MORNING_AUDIT có thêm cờ "ai_fallback" cho từng cảnh báo: True = câu mẫu do code sinh
  (Gemini lỗi / JSON sai / thiếu cảnh báo / số lệch), False = câu của Gemini đã qua hậu kiểm.

Gemini không bao giờ thấy hàm ghi: chỉ 4 tên trong ALLOWED_TOOLS được chạy, tên khác bị chặn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent.output_check import find_unknown_numbers
from agent.prompts import (
    CURRENT_QUESTION_HEADER,
    FINAL_ROUND_NOTE,
    HISTORY_HEADER,
    OUT_OF_SCOPE_MESSAGE,
    REFUSAL_MESSAGE,
    get_system_prompt,
)

logger = logging.getLogger(__name__)

Mode = Literal["MORNING_AUDIT", "READ_ONLY_CHAT"]

MAX_TOOL_ROUNDS = 6
MAX_HISTORY_TURNS = 4      # chat: số lượt hỏi đáp gần nhất gửi kèm (MA2)
MAX_HISTORY_CHARS = 300    # chat: cắt mỗi câu hỏi / câu trả lời trong lịch sử
FORECAST_DAYS = 3  # H = 3 ngày nhìn tới (calc.py); Gemini được nhắc tới số này dù AlertDraft không ghi
DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Đúng 4 tool đọc theo contracts mục 6. Không có create_draft_po, write_audit_log.
ALLOWED_TOOLS = (
    "check_inventory_fefo",
    "calc_preorder_demand",
    "check_delayed_po",
    "compare_suppliers",
)

# Bộ tool cho từng điểm vào, chọn trong code (không nhận từ giao diện).
TOOLS_BY_MODE: dict[str, tuple[str, ...]] = {
    "MORNING_AUDIT": ALLOWED_TOOLS,
    "READ_ONLY_CHAT": ALLOWED_TOOLS,
}

CHAT_FALLBACK_ANSWER = (
    "Hệ thống tạm thời chưa trả lời được câu hỏi này. Anh/chị xem số liệu ở tab "
    "«Kho & Đơn đặt trước» hoặc thử hỏi lại sau ít phút."
)
CHAT_EMPTY_ANSWER = "Anh/chị nhập câu hỏi về tồn kho, lô hàng, đơn đặt trước, NCC hoặc PO giúp mình nhé."


# ---------------------------------------------------------------------------
# Kiểm JSON đầu ra bằng pydantic
# ---------------------------------------------------------------------------

class _StripText(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class AlertExplanation(_StripText):
    ingredient_id: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    tradeoff_a: str = Field(min_length=1)
    tradeoff_b: str = Field(min_length=1)


class MorningOutput(BaseModel):
    alerts: list[AlertExplanation]


class ChatOutput(_StripText):
    answer: str = Field(min_length=1)
    refused: bool


# ---------------------------------------------------------------------------
# Tool: lấy registry thật của Q03, chưa có thì dùng tool giả
# ---------------------------------------------------------------------------

def _normalize_tools(registry: Any) -> dict[str, Callable[..., Any]]:
    """Nhận dict {tên: hàm} hoặc list/tuple các hàm; chỉ giữ 4 tên được phép."""
    if registry is None:
        return {}
    if isinstance(registry, dict):
        items = registry.items()
    else:
        items = ((getattr(fn, "__name__", ""), fn) for fn in registry)
    return {name: fn for name, fn in items if name in ALLOWED_TOOLS and callable(fn)}


def load_default_tools() -> dict[str, Callable[..., Any]]:
    """Tool thật từ agent/tools.py (READ_TOOLS hoặc TOOL_REGISTRY); thiếu thì dùng tool giả."""
    try:
        from agent import tools as tools_module
    except Exception:  # noqa: BLE001 – file lỗi import thì vẫn chạy bằng tool giả
        tools_module = None
    registry = None
    if tools_module is not None:
        registry = getattr(tools_module, "READ_TOOLS", None) or getattr(tools_module, "TOOL_REGISTRY", None)
    tools = _normalize_tools(registry)
    if len(tools) == len(ALLOWED_TOOLS):
        return tools
    logger.warning("agent/tools.py chưa đủ 4 tool (Q03) – core_agent đang dùng TOOL GIẢ.")
    return {**FAKE_TOOLS, **tools}


# Tool giả: dữ liệu theo data/expected.md (DEMO_DATE=2026-11-20T08:00), tên trường theo contracts mục 6
# (on_hand_qty, ok). Mỗi kết quả có "_fake": True.
_FAKE_STOCK = {
    "KEM_TUOI": {
        "ingredient_id": "KEM_TUOI", "ingredient_name": "Kem tươi Whipping Cream", "unit": "lít",
        "batches": [
            {"batch_id": "KEM-L1", "qty": 7.0, "expiry_date": "2026-11-20T20:00:00"},
            {"batch_id": "KEM-L2", "qty": 3.0, "expiry_date": "2026-11-25T20:00:00"},
        ],
    },
    "BO_LAT": {
        "ingredient_id": "BO_LAT", "ingredient_name": "Bơ lạt", "unit": "kg",
        "batches": [{"batch_id": "BO-L1", "qty": 5.0, "expiry_date": "2026-12-20T08:00:00"}],
    },
}


def _fake_unknown(ingredient_id: str | None) -> dict:
    return {"ok": False, "error_code": "INGREDIENT_NOT_FOUND",
            "message": f"Không có dữ liệu giả cho {ingredient_id}", "_fake": True}


def _fake_check_inventory_fefo(ingredient_id: str | None = None, at: str | None = None, days: int = 0) -> dict:
    if ingredient_id is None:
        return {"ok": True, "items": [{"ingredient_id": k, "ingredient_name": v["ingredient_name"], "unit": v["unit"],
                                       "on_hand_qty": sum(b["qty"] for b in v["batches"])}
                                      for k, v in _FAKE_STOCK.items()],
                "_fake": True}
    data = _FAKE_STOCK.get(ingredient_id)
    if data is None:
        return _fake_unknown(ingredient_id)
    return {"ok": True, **data, "on_hand_qty": sum(b["qty"] for b in data["batches"]), "_fake": True}


def _fake_calc_preorder_demand(ingredient_id: str | None = None, days: int = 3) -> dict:
    orders = [{"order_id": "DH01", "delivery_date": "2026-11-21", "ingredient_id": "KEM_TUOI", "need_qty": 3.0}]
    if ingredient_id is not None:
        orders = [o for o in orders if o["ingredient_id"] == ingredient_id]
    return {"days": days, "orders": orders, "_fake": True}


def _fake_check_delayed_po(ingredient_id: str | None = None, days: int = 3) -> dict:
    pos = [{"po_id": "PO-004", "ingredient_id": "BO_LAT", "sup_id": "NCC_CHINH", "qty": 20.0,
            "status": "SENT", "delivery_status": "DELAYED", "expected_delivery_date": "2026-11-19"}]
    if ingredient_id is not None:
        pos = [p for p in pos if p["ingredient_id"] == ingredient_id]
    return {"pos": pos, "_fake": True}


def _fake_compare_suppliers(ingredient_id: str) -> dict:
    if ingredient_id != "KEM_TUOI":
        return _fake_unknown(ingredient_id)
    return {"ingredient_id": "KEM_TUOI", "suppliers": [
        {"sup_id": "NCC_CHINH", "name": "NCC Chính", "price": 90000, "lead_time_hours": 24, "moq": 1},
        {"sup_id": "NCC_HOA_TOC", "name": "NCC Hỏa tốc", "price": 103500, "lead_time_hours": 2, "moq": 1},
    ], "_fake": True}


FAKE_TOOLS: dict[str, Callable[..., Any]] = {
    "check_inventory_fefo": _fake_check_inventory_fefo,
    "calc_preorder_demand": _fake_calc_preorder_demand,
    "check_delayed_po": _fake_check_delayed_po,
    "compare_suppliers": _fake_compare_suppliers,
}


# ---------------------------------------------------------------------------
# Gemini thật – cùng giao diện generate() với FakeLLM
# ---------------------------------------------------------------------------

_TOOL_DECLARATIONS: dict[str, dict] = {
    "check_inventory_fefo": {
        "description": "Tồn kho theo từng lô, xếp theo hạn dùng (FEFO). Không truyền ingredient_id để xem tất cả. "
                       "days > 0 để xem lô nào hết hạn trước khi kịp dùng.",
        "properties": {
            "ingredient_id": ("STRING", "Mã nguyên liệu viết hoa, ví dụ KEM_TUOI"),
            "at": ("STRING", "Thời điểm xem tồn, dạng YYYY-MM-DDTHH:MM:SS; bỏ trống = bây giờ"),
            "days": ("INTEGER", "Số ngày nhìn tới"),
        },
        "required": [],
    },
    "calc_preorder_demand": {
        "description": "Lượng nguyên liệu cần cho các đơn đặt trước trong số ngày tới.",
        "properties": {
            "ingredient_id": ("STRING", "Mã nguyên liệu; bỏ trống = tất cả"),
            "days": ("INTEGER", "Số ngày tới, mặc định 3"),
        },
        "required": [],
    },
    "check_delayed_po": {
        "description": "Các PO đã gửi đang giao trễ hoặc sắp tới hạn giao.",
        "properties": {
            "ingredient_id": ("STRING", "Mã nguyên liệu; bỏ trống = tất cả"),
            "days": ("INTEGER", "Số ngày tới, mặc định 3"),
        },
        "required": [],
    },
    "compare_suppliers": {
        "description": "So sánh các NCC của một nguyên liệu theo giá, thời gian giao, MOQ.",
        "properties": {"ingredient_id": ("STRING", "Mã nguyên liệu, bắt buộc")},
        "required": ["ingredient_id"],
    },
}


class GeminiLLM:
    """Bọc google-genai thành generate() giống FakeLLM để core_agent dùng chung một vòng lặp."""

    RETRY_CODES = (429, 500, 503, 504)

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 timeout_ms: int = 60_000, retries: int = 2) -> None:
        from dotenv import load_dotenv
        from google import genai
        from google.genai import types

        load_dotenv()
        self._types = types
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.retries = retries
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Thiếu GEMINI_API_KEY trong .env")
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))

    def _declarations(self, names: list[str]) -> list:
        t = self._types
        decls = []
        for name in names:
            spec = _TOOL_DECLARATIONS.get(name)
            if spec is None:
                continue
            props = {k: t.Schema(type=typ, description=desc) for k, (typ, desc) in spec["properties"].items()}
            decls.append(t.FunctionDeclaration(
                name=name, description=spec["description"],
                parameters=t.Schema(type="OBJECT", properties=props, required=spec["required"]),
            ))
        return decls

    def _build_contents(self, user_message: str, tool_results: list[dict[str, Any]] | None,
                        final: bool) -> list:
        """Hội thoại gửi Gemini theo chuẩn function calling:

        user(câu hỏi) → model(function_call…) → user(function_response…) → … [→ câu nhắc lượt cuối]

        Mỗi vòng giữ nguyên nội dung model Gemini đã trả (`_model_content`, có thought signature);
        không có thì dựng lại từ tool_name + arguments. Kết quả tool gửi bằng function_response,
        không nối thành chữ.
        """
        t = self._types
        contents = [t.Content(role="user", parts=[t.Part(text=user_message)])]
        rounds: dict[int, list[dict]] = {}
        for item in tool_results or []:
            rounds.setdefault(int(item.get("round") or 0), []).append(item)
        for _, items in sorted(rounds.items()):
            model_content = items[0].get("_model_content")
            if model_content is None:
                model_content = t.Content(role="model", parts=[
                    t.Part.from_function_call(name=i["tool_name"], args=i.get("arguments") or {}) for i in items])
            contents.append(model_content)
            contents.append(t.Content(role="user", parts=[
                t.Part(function_response=t.FunctionResponse(
                    id=i.get("call_id"), name=i["tool_name"], response=_as_response(i.get("result"))))
                for i in items]))
        if final:
            contents[-1].parts.append(t.Part(text=FINAL_ROUND_NOTE))
        return contents

    def generate(self, mode: str, system_prompt: str, user_message: str,
                 available_tools: list[str] | None = None,
                 tool_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        from google.genai import errors

        t = self._types
        names = list(available_tools or [])
        # Lượt tổng hợp cuối: không cho gọi tool nữa nhưng vẫn khai báo các tool đã dùng để lịch sử hợp lệ.
        final = not names and bool(tool_results)
        if final:
            names = sorted({r.get("tool_name") for r in tool_results if r.get("tool_name") in _TOOL_DECLARATIONS})
        decls = self._declarations(names)
        config = t.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            tools=[t.Tool(function_declarations=decls)] if decls else None,
            tool_config=(t.ToolConfig(function_calling_config=t.FunctionCallingConfig(mode="NONE"))
                         if final and decls else None),
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            # Gemini không cho bật JSON mode cùng lúc với tool → khi có tool thì đọc JSON từ văn bản.
            response_mime_type=None if decls else "application/json",
        )
        contents = self._build_contents(user_message, tool_results, final)
        resp = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                break
            except errors.APIError as exc:
                if exc.code not in self.RETRY_CODES or attempt == self.retries:
                    raise
                time.sleep(5 * (attempt + 1))
        calls = getattr(resp, "function_calls", None) or []
        if calls:
            candidates = getattr(resp, "candidates", None) or []
            return {
                "type": "tool_call",
                # Đủ mọi function_call của lượt này (Gemini có thể gọi song song nhiều tool)
                "calls": [{"tool_name": c.name, "arguments": dict(c.args or {}), "call_id": getattr(c, "id", None)}
                          for c in calls],
                "_model_content": candidates[0].content if candidates else None,
            }
        return {"type": "text", "text": resp.text or ""}


def _as_response(result: Any) -> dict:
    """function_response cần dict thuần JSON."""
    data = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    return data if isinstance(data, dict) else {"result": data}


# ---------------------------------------------------------------------------
# Câu dự phòng do code sinh (không qua LLM)
# ---------------------------------------------------------------------------

def _option_text(opt: dict, unit: str) -> str:
    when = "kịp" if opt.get("meets_earliest_preorder") else "KHÔNG kịp"
    return (f"{opt.get('sup_name', opt.get('sup_id'))}: đặt {opt.get('order_qty')} {unit}, "
            f"tổng {opt.get('total_cost')} đ, hàng về {opt.get('arrival_at')}, {when} đơn đặt trước sớm nhất")


def fallback_explanation(alert: dict) -> dict:
    """Diễn giải mẫu cho 1 AlertDraft, chỉ dùng số có trong dữ liệu. Luôn có ai_fallback = True."""
    name = alert.get("ingredient_name") or alert.get("ingredient_id")
    unit = alert.get("unit", "")
    explanation = (
        f"{name} dự kiến còn {alert.get('projected_stock')} {unit}, thấp hơn mức tối thiểu "
        f"{alert.get('min_stock')} {unit}; cần bổ sung {alert.get('shortage_qty')} {unit}. "
        f"Nguyên nhân chính: {alert.get('main_cause')}."
    )
    options = alert.get("options") or []
    note = alert.get("note")
    if options:
        tradeoff_a = f"Phương án A – {_option_text(options[0], unit)}."
        if len(options) > 1:
            b = options[1]
            tradeoff_b = (f"Phương án B – {_option_text(b, unit)}; chênh lệch so với A: "
                          f"{b.get('cost_diff_pct_vs_A')}%.")
        else:
            tradeoff_b = f"Không có phương án B: {note}" if note else "Không có phương án B."
    elif alert.get("open_po_id"):
        tradeoff_a = f"Đã có PO {alert['open_po_id']} đang mở cho nguyên liệu này, chờ PO này về."
        tradeoff_b = "Không có phương án B."
    else:
        tradeoff_a = note or "Cần xử lý thủ công."
        tradeoff_b = "Không có phương án B."
    return {"ingredient_id": alert.get("ingredient_id"), "explanation": explanation,
            "tradeoff_a": tradeoff_a, "tradeoff_b": tradeoff_b, "ai_fallback": True}


def _morning_fallback(alerts: list[dict]) -> dict:
    return {"alerts": [fallback_explanation(a) for a in alerts]}


# ---------------------------------------------------------------------------
# Vòng lặp chính
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> Any:
    """Đọc JSON, chấp nhận Gemini bọc trong ```json ... ```."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    return json.loads(cleaned)


def _log(trace: list | None, step: str, **detail: Any) -> None:
    if trace is not None:
        trace.append({"step": step, **detail})


def _run_tool(tools: dict[str, Callable[..., Any]], name: str, args: dict, trace: list | None) -> dict:
    if name not in tools:
        _log(trace, "tool_blocked", tool_name=name, arguments=args)
        return {"tool_name": name, "arguments": args,
                "result": {"ok": False, "error_code": "TOOL_NOT_ALLOWED",
                           "message": f"Công cụ {name} không được phép. Chỉ dùng: {', '.join(ALLOWED_TOOLS)}"}}
    try:
        result = tools[name](**args)
    except Exception as exc:  # noqa: BLE001 – lỗi tool trả về cho LLM như dữ liệu
        result = {"ok": False, "error_code": "TOOL_ERROR", "message": str(exc)}
    _log(trace, "tool_call", tool_name=name, arguments=args)
    return {"tool_name": name, "arguments": args, "result": result}


def _calls_of(resp: dict) -> list[dict]:
    """Danh sách lệnh gọi tool trong 1 phản hồi: dạng mới {"calls": [...]} hoặc dạng cũ 1 tool."""
    calls = resp.get("calls")
    if calls is None:
        calls = [{"tool_name": resp.get("tool_name", ""), "arguments": resp.get("arguments")}]
    out = []
    for c in calls:
        args = c.get("arguments") if isinstance(c, dict) else None
        out.append({"tool_name": str((c or {}).get("tool_name") or ""),
                    "arguments": args if isinstance(args, dict) else {},
                    "call_id": (c or {}).get("call_id")})
    return out


def _ask_llm(llm: Any, mode: str, user_message: str, tools: dict, trace: list | None,
             tool_results: list[dict] | None = None) -> str | None:
    """Vòng hỏi Gemini ↔ chạy tool. Trả văn bản cuối, hoặc None nếu lỗi.

    - Mỗi phản hồi có thể gọi nhiều tool: chạy đủ cả, mỗi kết quả gắn "round" và "call_id"
      để GeminiLLM gửi lại bằng function_response đúng cặp.
    - Gọi tool tối đa MAX_TOOL_ROUNDS vòng. Hết vòng thì gọi thêm 1 lượt KHÔNG có tool
      (available_tools = []) để tổng hợp từ kết quả đã có; lượt đó vẫn đòi tool → None (câu dự phòng).
    - `tool_results`: truyền list để nhận lại kết quả các tool đã chạy (dùng cho hậu kiểm số liệu).
    """
    available = [n for n in TOOLS_BY_MODE[mode] if n in tools]
    system_prompt = get_system_prompt(mode)
    if tool_results is None:
        tool_results = []
    rounds = 0
    while True:
        final = rounds >= MAX_TOOL_ROUNDS
        try:
            resp = llm.generate(mode=mode, system_prompt=system_prompt, user_message=user_message,
                                available_tools=[] if final else available, tool_results=tool_results)
        except Exception as exc:  # noqa: BLE001 – mất mạng, 503, timeout… → câu dự phòng
            _log(trace, "llm_error", error=f"{type(exc).__name__}: {exc}"[:300])
            return None
        if resp.get("type") != "tool_call":
            return resp.get("text") or ""
        if final:
            _log(trace, "final_round_still_tool_call")
            return None
        rounds += 1
        for call in _calls_of(resp):
            entry = _run_tool(tools, call["tool_name"], call["arguments"], trace)
            entry.update(round=rounds, call_id=call["call_id"], _model_content=resp.get("_model_content"))
            tool_results.append(entry)
        if rounds >= MAX_TOOL_ROUNDS:
            _log(trace, "tool_rounds_exceeded", max_rounds=MAX_TOOL_ROUNDS)


def _morning(payload: dict, llm: Any, tools: dict, trace: list | None) -> dict:
    alerts = list(payload.get("alerts") or [])
    if not alerts:
        return {"alerts": []}
    message = json.dumps({"run_date": payload.get("run_date"), "alerts": alerts},
                         ensure_ascii=False, default=str)
    text = _ask_llm(llm, "MORNING_AUDIT", message, tools, trace)
    if text is None:
        _log(trace, "fallback", reason="llm_failed")
        return _morning_fallback(alerts)
    try:
        parsed = MorningOutput.model_validate(_parse_json(text))
    except (ValueError, ValidationError) as exc:
        _log(trace, "fallback", reason="invalid_json", error=str(exc)[:300])
        return _morning_fallback(alerts)

    by_id = {item.ingredient_id: item.model_dump() for item in parsed.alerts}
    result = []
    for alert in alerts:  # giữ đúng thứ tự và đủ ingredient_id của đầu vào
        item = by_id.get(alert.get("ingredient_id"))
        if item is None:
            _log(trace, "fallback", reason="missing_alert", ingredient_id=alert.get("ingredient_id"))
            result.append(fallback_explanation(alert))
            continue
        bad = _unknown_numbers_in_alert(item, alert, payload.get("run_date"))
        if bad:
            _log(trace, "fallback", reason="numbers_mismatch", ingredient_id=alert.get("ingredient_id"),
                 numbers=bad[:10])
            result.append(fallback_explanation(alert))
            continue
        result.append({**item, "ai_fallback": False})
    return {"alerts": result}


def _unknown_numbers_in_alert(item: dict, alert: dict, run_date: Any) -> list[str]:
    """Số trong explanation, tradeoff_a, tradeoff_b phải có trong AlertDraft (hoặc run_date, H = 3)."""
    extra = [run_date, FORECAST_DAYS, len(alert.get("options") or []), len(alert.get("causes") or [])]
    text = " | ".join(str(item.get(k) or "") for k in ("explanation", "tradeoff_a", "tradeoff_b"))
    return find_unknown_numbers(text, alert, extra=extra)


def clean_history(history: Any) -> list[dict]:
    """Lịch sử chat an toàn để gửi Gemini: tối đa MAX_HISTORY_TURNS lượt gần nhất,
    mỗi câu hỏi / câu trả lời cắt còn MAX_HISTORY_CHARS ký tự, bỏ phần tử sai dạng.

    Mỗi lượt: {"question": str, "answer": str, "refused": bool} – giống st.session_state["chat_history"].
    """
    if not isinstance(history, (list, tuple)):
        return []
    turns = []
    for item in history:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()[:MAX_HISTORY_CHARS]
        a = str(item.get("answer") or "").strip()[:MAX_HISTORY_CHARS]
        if q or a:
            turns.append({"question": q, "answer": a, "refused": bool(item.get("refused", False))})
    return turns[-MAX_HISTORY_TURNS:]


def _chat_message(question: str, history: list[dict]) -> str:
    """Không có lịch sử → gửi đúng câu hỏi. Có lịch sử → khung LỊCH SỬ … / CÂU HỎI HIỆN TẠI …"""
    if not history:
        return question
    lines = [HISTORY_HEADER]
    for i, turn in enumerate(history, 1):
        lines.append(f"[{i}] Người dùng hỏi: {turn['question']}")
        lines.append(f"[{i}] Trợ lý đáp{' (từ chối)' if turn['refused'] else ''}: {turn['answer']}")
    return "\n".join(lines) + f"\n\n{CURRENT_QUESTION_HEADER} {question}"


def _chat(payload: dict, llm: Any, tools: dict, trace: list | None) -> dict:
    question = str(payload.get("question") or "").strip()
    if not question:
        return {"answer": CHAT_EMPTY_ANSWER, "refused": True}
    history = clean_history(payload.get("history"))
    tool_results: list[dict] = []
    text = _ask_llm(llm, "READ_ONLY_CHAT", _chat_message(question, history), tools, trace, tool_results)
    if text is None:
        _log(trace, "fallback", reason="llm_failed")
        return {"answer": CHAT_FALLBACK_ANSWER, "refused": False}
    try:
        out = ChatOutput.model_validate(_parse_json(text)).model_dump()
    except (ValueError, ValidationError) as exc:
        _log(trace, "fallback", reason="invalid_json", error=str(exc)[:300])
        return {"answer": CHAT_FALLBACK_ANSWER, "refused": False}
    # Số trong câu trả lời phải lấy từ kết quả tool của lượt này (hoặc có sẵn trong câu hỏi / câu từ chối).
    # Câu trả lời cũ trong lịch sử đã qua hậu kiểm ở lượt trước nên được nhắc lại.
    bad = find_unknown_numbers(out["answer"], [r.get("result") for r in tool_results], question, history,
                               extra=[REFUSAL_MESSAGE, OUT_OF_SCOPE_MESSAGE, FORECAST_DAYS])
    if bad:
        _log(trace, "fallback", reason="numbers_mismatch", numbers=bad[:10])
        return {"answer": CHAT_FALLBACK_ANSWER, "refused": False}
    return out


def run_agent(mode: Mode, payload: dict, llm: Any = None,
              tools: Any = None, trace: list | None = None) -> dict:
    """Điểm vào duy nhất của agent (contracts mục 7). Không ghi dữ liệu, không raise khi Gemini lỗi."""
    if mode not in TOOLS_BY_MODE:
        raise ValueError(f"mode không hợp lệ: {mode}. Chỉ nhận MORNING_AUDIT hoặc READ_ONLY_CHAT")
    tool_map = _normalize_tools(tools) if tools is not None else load_default_tools()

    if llm is None:
        try:
            llm = GeminiLLM()
        except Exception as exc:  # noqa: BLE001 – thiếu key / thiếu thư viện → vẫn trả câu dự phòng
            _log(trace, "llm_error", error=f"{type(exc).__name__}: {exc}"[:300])
            _log(trace, "fallback", reason="llm_unavailable")
            if mode == "MORNING_AUDIT":
                return _morning_fallback(list(payload.get("alerts") or []))
            return {"answer": CHAT_FALLBACK_ANSWER, "refused": False}

    if mode == "MORNING_AUDIT":
        return _morning(payload, llm, tool_map, trace)
    return _chat(payload, llm, tool_map, trace)
