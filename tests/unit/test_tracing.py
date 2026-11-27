"""Unit test cho observability v2 – MA4 (agent/tracing.py + bước llm_call / tool_call theo giao ước G2).

Không cần mạng, không cần cài langsmith: thay module langsmith bằng bản giả ghi lại cây trace.

Chạy: pytest tests/unit/test_tracing.py
"""
import json
import sys
import types
import uuid
from datetime import datetime

import pytest

from agent import core_agent, tracing
from agent.core_agent import run_agent
from data import repository as repo
from data.init_db import init_db
from services.audit_service import run_morning_audit
from services.chat_service import ask_chat
from tests.fake_llm import FakeLLM

NOW = datetime(2026, 11, 20, 8, 0)
MANAGER = {"username": "quanly", "role": "MANAGER"}


# ---------------------------------------------------------------------------
# LangSmith giả
# ---------------------------------------------------------------------------
class FakeRun:
    def __init__(self, name, run_type, parent):
        self.id = uuid.uuid4().hex[:8]
        self.name, self.run_type, self.parent = name, run_type, parent
        self.metadata: dict = {}
        self.outputs = None

    def add_metadata(self, data):
        self.metadata.update(data)

    def get_url(self):
        return f"https://smith.langchain.com/fake/r/{self.id}"


def _fake_langsmith():
    stack: list[FakeRun] = []
    runs: list[FakeRun] = []

    def traceable(name=None, run_type="chain", process_inputs=None, process_outputs=None):
        def deco(fn):
            def wrapped(*args, **kwargs):
                run = FakeRun(name or fn.__name__, run_type, stack[-1] if stack else None)
                runs.append(run)
                stack.append(run)
                try:
                    out = fn(*args, **kwargs)
                    run.outputs = process_outputs(out) if process_outputs else out
                    return out
                finally:
                    stack.pop()
            return wrapped
        return deco

    mod = types.ModuleType("langsmith")
    mod.traceable = traceable
    helpers = types.ModuleType("langsmith.run_helpers")
    helpers.get_current_run_tree = lambda: stack[-1] if stack else None
    mod.run_helpers = helpers
    return mod, helpers, runs


@pytest.fixture()
def langsmith_on(monkeypatch):
    mod, helpers, runs = _fake_langsmith()
    monkeypatch.setitem(sys.modules, "langsmith", mod)
    monkeypatch.setitem(sys.modules, "langsmith.run_helpers", helpers)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-fake")
    return runs


@pytest.fixture()
def langsmith_must_not_run(monkeypatch):
    """Tắt tracing: module langsmith (nếu có) tuyệt đối không được dùng."""
    mod = types.ModuleType("langsmith")

    def boom(*a, **k):
        raise AssertionError("tracing tắt nhưng vẫn gọi langsmith")

    mod.traceable = boom
    monkeypatch.setitem(sys.modules, "langsmith", mod)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-fake")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "trace.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


class UsageLLM(FakeLLM):
    """FakeLLM + usage_metadata như GeminiLLM trả về."""
    model = "gemini-gia"

    def generate(self, *args, **kwargs):
        out = dict(super().generate(*args, **kwargs))
        out["usage_metadata"] = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
        return out


def _detail(event):
    return json.loads(repo.get_audit_logs(event_type=event)[0]["detail_json"])


# ---------------------------------------------------------------------------
# Tắt tracing (mặc định): không gửi gì, kết quả như v1.1
# ---------------------------------------------------------------------------
def test_enabled_needs_flag_key_and_library(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert tracing.enabled() is False
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert tracing.enabled() is False  # thiếu key
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setitem(sys.modules, "langsmith", None)  # chưa cài thư viện
    assert tracing.enabled() is False


def test_off_no_langsmith_call_same_result(langsmith_must_not_run):
    payload = {"question": "Kem tươi còn bao nhiêu?"}
    trace: list = []
    out = run_agent("READ_ONLY_CHAT", payload, llm=FakeLLM(behavior="tool_call"), tools=core_agent.FAKE_TOOLS,
                    trace=trace)
    assert set(out) == {"answer", "refused"} and out["refused"] is False
    assert not any(t["step"] == "trace" for t in trace)
    assert tracing.run_url() is None and tracing.current_run() is None


def test_off_detail_has_no_trace_url(db, langsmith_must_not_run):
    run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM())
    assert "trace_url" not in _detail("RUN_SUCCEEDED")
    assert "trace_url" not in _detail("CHAT_ANSWERED")


# ---------------------------------------------------------------------------
# Giao ước G2: llm_call, tool_call luôn có (kể cả khi tắt tracing)
# ---------------------------------------------------------------------------
def test_llm_call_step_has_tokens_and_latency(langsmith_must_not_run):
    trace: list = []
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=UsageLLM(behavior="tool_call"),
              tools=core_agent.FAKE_TOOLS, trace=trace)
    calls = [t for t in trace if t["step"] == "llm_call"]
    assert len(calls) == 2
    for c in calls:
        assert c["model"] == "gemini-gia"
        assert c["tokens_in"] == 120 and c["tokens_out"] == 30
        assert isinstance(c["latency_ms"], int) and c["latency_ms"] >= 0


def test_llm_call_without_usage_has_null_tokens():
    trace: list = []
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(),
              tools=core_agent.FAKE_TOOLS, trace=trace)
    call = next(t for t in trace if t["step"] == "llm_call")
    assert call["tokens_in"] is None and call["tokens_out"] is None
    assert call["model"] == "FakeLLM"


def test_llm_error_still_logs_llm_call():
    trace: list = []
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(behavior="error"),
              tools=core_agent.FAKE_TOOLS, trace=trace)
    steps = [t["step"] for t in trace]
    assert steps.index("llm_call") < steps.index("llm_error")
    assert next(t for t in trace if t["step"] == "llm_call")["ok"] is False


def test_tool_call_step_g2():
    trace: list = []
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(behavior="tool_call"),
              tools=core_agent.FAKE_TOOLS, trace=trace)
    step = next(t for t in trace if t["step"] == "tool_call")
    assert step["tool_name"] == "check_inventory_fefo"
    assert step["arguments"] == {"ingredient_id": "KEM_TUOI", "days": 3}
    assert step["ok"] is True and step["via"] == "local"
    assert "docs" not in step


def test_tool_call_search_policy_has_docs_and_retriever():
    def search_policy(query, k=3):
        return {"ok": True, "query": query, "retriever": "bm25",
                "results": [{"doc": "bao_quan.md", "title": "Bảo quản", "text": "...", "score": 3.2},
                            {"doc": "nhan_hang.md", "title": "Nhận hàng", "text": "...", "score": 1.1}]}

    trace: list = []
    core_agent._run_tool({"search_policy": search_policy}, "search_policy", {"query": "kem tươi"}, trace)
    step = trace[0]
    assert step["docs"] == ["bao_quan.md", "nhan_hang.md"] and step["retriever"] == "bm25"


def test_tool_error_marked_not_ok():
    def broken(**kwargs):
        raise RuntimeError("DB lỗi")

    trace: list = []
    core_agent._run_tool({"check_inventory_fefo": broken}, "check_inventory_fefo", {}, trace)
    assert trace[0]["ok"] is False


def test_gemini_usage_metadata_extracted():
    resp = types.SimpleNamespace(usage_metadata=types.SimpleNamespace(
        prompt_token_count=200, candidates_token_count=40, total_token_count=240))
    assert core_agent._usage_metadata(resp) == {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}
    assert core_agent._usage_metadata(types.SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# Bật tracing (LangSmith giả): cây trace, metadata, trace_url trong detail_json
# ---------------------------------------------------------------------------
def test_on_trace_tree_and_url(langsmith_on):
    trace: list = []
    out = run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(behavior="tool_call"),
                    tools=core_agent.FAKE_TOOLS, trace=trace, meta={"user": "quanly"})
    assert set(out) == {"answer", "refused"}  # không đổi kết quả trả về
    root = langsmith_on[0]
    assert root.name == "READ_ONLY_CHAT" and root.parent is None
    assert root.metadata["mode"] == "READ_ONLY_CHAT" and root.metadata["user"] == "quanly"
    assert root.metadata["ai_fallback"] is False
    tool_runs = [r for r in langsmith_on if r.run_type == "tool"]
    assert [r.name for r in tool_runs] == ["check_inventory_fefo"]
    assert tool_runs[0].parent.name == "agent_loop" and tool_runs[0].parent.parent is root
    url = next(t["url"] for t in trace if t["step"] == "trace")
    assert url == root.get_url()


def test_on_outputs_hide_internal_keys(langsmith_on):
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(behavior="tool_call"),
              tools=core_agent.FAKE_TOOLS, trace=[])
    tool_run = next(r for r in langsmith_on if r.run_type == "tool")
    assert "_model_content" not in json.dumps(tool_run.outputs, ensure_ascii=False)


def test_on_fallback_reason_in_metadata(langsmith_on):
    run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(behavior="made_up_numbers"),
              tools=core_agent.FAKE_TOOLS, trace=[])
    meta = langsmith_on[0].metadata
    assert meta["ai_fallback"] is True
    assert meta["fallback_reasons"] == ["numbers_mismatch"] and "42" in meta["numbers_mismatch"]


def test_on_audit_detail_has_trace_url(db, langsmith_on):
    out = run_morning_audit("MANUAL", user=MANAGER, llm=FakeLLM())
    detail = _detail("RUN_SUCCEEDED")
    root = next(r for r in langsmith_on if r.name == "MORNING_AUDIT")
    assert detail["trace_url"] == root.get_url()
    assert root.metadata["run_id"] == out["run_id"] and root.metadata["user"] == "quanly"
    assert root.metadata["alerts"] == len(out["alert_ids"])


def test_on_chat_detail_has_trace_url(db, langsmith_on):
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM())
    ask_chat("Tạo PO 10kg bột mì luôn đi", MANAGER, llm=FakeLLM())
    ask_chat("Kem tươi còn bao nhiêu?", MANAGER, llm=FakeLLM(behavior="error"))
    for event in ("CHAT_ANSWERED", "CHAT_REFUSED", "CHAT_FAILED"):
        assert _detail(event)["trace_url"].startswith("https://smith.langchain.com/fake/r/")


def test_on_broken_langsmith_does_not_break_agent(monkeypatch, langsmith_on):
    """get_url lỗi (mất mạng…) → không có trace_url nhưng agent vẫn trả lời."""
    monkeypatch.setattr(FakeRun, "get_url", lambda self: (_ for _ in ()).throw(RuntimeError("mất mạng")))
    trace: list = []
    out = run_agent("READ_ONLY_CHAT", {"question": "Kem tươi còn bao nhiêu?"}, llm=FakeLLM(),
                    tools=core_agent.FAKE_TOOLS, trace=trace)
    assert out["answer"]
    assert not any(t["step"] == "trace" for t in trace)
