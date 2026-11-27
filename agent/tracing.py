"""Observability (v2 – MA4): theo dõi Agent bằng LangSmith, lớp bọc mỏng, mặc định TẮT.

Chủ file: Mai Anh. Dùng trong agent/core_agent.py.

- LANGSMITH_TRACING khác "true" (mặc định), thiếu LANGSMITH_API_KEY hoặc chưa cài thư viện langsmith
  → mọi hàm ở đây là no-op: không import langsmith, không gửi gì ra ngoài, kết quả y như v1.1.
- Bật → các hàm gắn @traced(...) thành một nút trong cây trace trên LangSmith:
      MORNING_AUDIT / READ_ONLY_CHAT (chain, gốc) → _ask_llm (chain) → GeminiLLM.generate (llm)
                                                                    → <tên tool> (tool)
- Kiểm tra bật/tắt lúc GỌI hàm (không phải lúc import) để test và app đổi được qua biến môi trường.

Bước "llm_call", "tool_call" trong list trace (giao ước G2) KHÔNG phụ thuộc file này: core_agent luôn ghi,
kể cả khi tắt LangSmith, để Evals đo token, độ trễ, nguồn.
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable

_TRUE = {"1", "true", "yes", "on"}

# Nạp .env ngay khi import (không ghi đè biến đã có), để LANGSMITH_* trong .env có hiệu lực từ lượt gọi
# đầu tiên – trước đây .env chỉ được nạp khi tạo GeminiLLM, sau khi nút gốc đã quyết định tắt tracing.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def enabled() -> bool:
    """Tracing bật khi LANGSMITH_TRACING=true, có LANGSMITH_API_KEY và import được langsmith."""
    if (os.getenv("LANGSMITH_TRACING") or "").strip().lower() not in _TRUE:
        return False
    if not os.getenv("LANGSMITH_API_KEY"):
        return False
    try:
        import langsmith  # noqa: F401
    except ImportError:
        return False
    return True


def _clean(value: Any, depth: int = 0) -> Any:
    """Bỏ khóa nội bộ (bắt đầu bằng "_", vd _model_content của Gemini) để trace dễ đọc, không lỗi JSON."""
    if depth > 6:
        return str(value)[:200]
    if isinstance(value, dict):
        return {k: _clean(v, depth + 1) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)[:500]


def _process_inputs(inputs: dict) -> dict:
    out = {}
    for k, v in inputs.items():
        if k in ("self", "llm", "tools"):  # đối tượng client / bảng hàm: không cần trong trace
            continue
        out[k] = _clean(v)
    return out


def _process_outputs(outputs: Any) -> dict:
    cleaned = _clean(outputs)
    return cleaned if isinstance(cleaned, dict) else {"output": cleaned}


def traced(name: str | None = None, run_type: str = "chain", name_from: str | None = None) -> Callable:
    """Decorator: bật tracing thì bọc bằng langsmith.traceable, tắt thì gọi thẳng hàm gốc.

    name_from: lấy tên nút từ tham số (vd "mode" → MORNING_AUDIT / READ_ONLY_CHAT, "name" → tên tool).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            from langsmith import traceable

            run_name = name or fn.__name__
            if name_from:
                value = kwargs.get(name_from)
                if value is None:
                    try:
                        import inspect

                        value = inspect.signature(fn).bind_partial(*args, **kwargs).arguments.get(name_from)
                    except TypeError:
                        value = None
                run_name = str(value or run_name)
            try:
                wrapped = traceable(name=run_name, run_type=run_type,
                                    process_inputs=_process_inputs, process_outputs=_process_outputs)(fn)
            except TypeError:  # langsmith cũ chưa có process_inputs/process_outputs
                wrapped = traceable(name=run_name, run_type=run_type)(fn)
            return wrapped(*args, **kwargs)

        return wrapper

    return decorator


def current_run() -> Any:
    """RunTree đang chạy (None nếu tắt tracing hoặc không ở trong hàm @traced)."""
    if not enabled():
        return None
    try:
        from langsmith.run_helpers import get_current_run_tree

        return get_current_run_tree()
    except Exception:  # noqa: BLE001 – tracing không bao giờ được làm hỏng agent
        return None


def add_metadata(**metadata: Any) -> None:
    """Gắn metadata vào nút trace hiện tại (mode, người dùng, run_id, ai_fallback, lý do dự phòng…)."""
    run = current_run()
    if run is None:
        return
    data = {k: _clean(v) for k, v in metadata.items() if v is not None}
    try:
        if hasattr(run, "add_metadata"):
            run.add_metadata(data)
        else:  # langsmith cũ
            run.extra = run.extra or {}
            run.extra.setdefault("metadata", {}).update(data)
    except Exception:  # noqa: BLE001
        pass


def run_url() -> str | None:
    """Link của nút trace hiện tại trên LangSmith (gọi trong hàm gốc → link cây trace của lượt đó)."""
    run = current_run()
    if run is None:
        return None
    try:
        if hasattr(run, "get_url"):
            return run.get_url()
        from langsmith import Client

        return Client().get_run_url(run=run, project_name=os.getenv("LANGSMITH_PROJECT") or None)
    except Exception:  # noqa: BLE001 – không lấy được link thì thôi, không làm hỏng agent
        return None
