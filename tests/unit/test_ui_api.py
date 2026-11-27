"""Unit test cho services/ui_api.py (Yến): MOQ, cảnh báo số lượng (YE1), đăng nhập và chat (YE2),
giao diện v2 (YE7): nguồn chat, đọc kết quả Evals, chế độ công cụ, và AppTest chạy cả app.py."""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from data.init_db import init_db
from services import calc, ui_api

NOW = datetime(2026, 11, 20, 8, 0)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "ui.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


# ---- MOQ -------------------------------------------------------------------
def test_get_moq_reads_supplier_table(db):
    # Dữ liệu mẫu: Kem tươi, NCC Chính có MOQ 2 lít; Bơ lạt, NCC Chính có MOQ 5 kg
    assert ui_api.get_moq("KEM_TUOI", "NCC_CHINH") == 2.0
    assert ui_api.get_moq("BO_LAT", "NCC_CHINH") == 5.0


def test_get_moq_unknown_supplier_returns_none(db):
    # Không đoán MOQ = 1: thiếu dữ liệu NCC thì trả None để giao diện khóa nút Tạo PO
    assert ui_api.get_moq("KEM_TUOI", "NCC_KHONG_CO") is None


def test_get_moq_supplier_not_selling_this_ingredient_returns_none(db):
    # Dữ liệu mẫu: Mascarpone chỉ có NCC Chính, NCC Hỏa tốc không bán
    assert ui_api.get_moq("MASCARPONE", "NCC_HOA_TOC") is None


@pytest.mark.parametrize("qty, moq, expected", [
    (23, 10, 30), (4, 2, 4), (3, 2, 4), (13, 5, 15), (0.3, 0.5, 0.5), (0, 2, 0),
])
def test_round_up_moq_same_as_calc(qty, moq, expected):
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(expected)
    assert ui_api.round_up_moq(qty, moq) == pytest.approx(calc.round_up_moq(qty, moq))


@pytest.mark.parametrize("moq", [None, 0])
def test_round_up_moq_without_moq_keeps_qty(moq):
    assert ui_api.round_up_moq(7, moq) == 7


# ---- Cảnh báo mềm ------------------------------------------------------------
def test_warning_when_less_than_shortage():
    msg = ui_api.order_qty_warning(2, 4, "lít")
    assert msg and "ít hơn" in msg and "4 lít" in msg


def test_warning_when_more_than_three_times_shortage():
    msg = ui_api.order_qty_warning(14, 4, "lít")
    assert msg and "gấp hơn 3 lần" in msg


@pytest.mark.parametrize("qty", [4, 8, 12])
def test_no_warning_in_normal_range(qty):
    # từ đúng lượng cần bổ sung đến đúng 3 lần: không nhắc
    assert ui_api.order_qty_warning(qty, 4, "lít") is None


@pytest.mark.parametrize("qty, shortage", [(0, 4), (5, 0), (5, None)])
def test_no_warning_when_input_not_usable(qty, shortage):
    assert ui_api.order_qty_warning(qty, shortage) is None


# ---- Đăng nhập qua services/auth.py (YE2) -----------------------------------
@pytest.fixture()
def pins(monkeypatch):
    from services import auth
    monkeypatch.setenv("MANAGER_PINS", "Duyên:2468,Yến:1357")
    monkeypatch.delenv("MANAGER_PIN", raising=False)
    auth.reset_failures()
    yield
    auth.reset_failures()


def test_login_manager_ok_uses_name_from_config(pins):
    user, msg = ui_api.login_manager("  yến ", "1357")
    assert user == {"username": "Yến", "role": "MANAGER"}
    assert "Xin chào" in msg


def test_login_manager_wrong_pin_counts_down(pins):
    user, msg = ui_api.login_manager("Yến", "0000")
    assert user is None and "Còn 4 lần thử" in msg


def test_login_manager_locked_after_5_wrong_pins(pins):
    for _ in range(5):
        ui_api.login_manager("Yến", "0000")
    user, msg = ui_api.login_manager("Yến", "1357")      # đúng PIN nhưng đang bị khóa
    assert user is None and "Thử lại sau" in msg
    user, _ = ui_api.login_manager("Duyên", "2468")      # khóa theo từng tên
    assert user is not None


@pytest.mark.parametrize("name", ["", "   "])
def test_login_requires_name(pins, name):
    assert ui_api.login_manager(name, "1357")[0] is None
    assert ui_api.login_staff(name)[0] is None


def test_login_staff_ok():
    user, _ = ui_api.login_staff(" Lan ")
    assert user == {"username": "Lan", "role": "STAFF"}


# ---- Chat gửi kèm lịch sử (YE2) -----------------------------------------------
def _history(n):
    return [{"question": f"Câu {i}", "answer": f"Đáp {i}", "refused": False, "fallback": False}
            for i in range(n)]


def test_ask_chat_sends_last_turns_when_supported(monkeypatch):
    seen = {}

    def fake(question, user, history=None, llm=None):
        seen["history"] = history
        return {"answer": "ok", "refused": False, "fallback": True}

    monkeypatch.setattr(ui_api._chat, "ask_chat", fake)
    result = ui_api.ask_chat("Còn bao nhiêu?", {"username": "Yến", "role": "STAFF"}, history=_history(6))
    assert result == {"answer": "ok", "refused": False, "fallback": True, "sources": []}
    assert [t["question"] for t in seen["history"]] == ["Câu 2", "Câu 3", "Câu 4", "Câu 5"]
    assert set(seen["history"][0]) == {"question", "answer", "refused"}


def test_ask_chat_old_chat_service_without_history(monkeypatch):
    monkeypatch.setattr(ui_api._chat, "ask_chat",
                        lambda question, user: {"answer": "ok", "refused": False})
    result = ui_api.ask_chat("Còn bao nhiêu?", {"username": "Yến", "role": "STAFF"}, history=_history(2))
    assert result == {"answer": "ok", "refused": False, "fallback": False, "sources": []}


# ---- Giao diện v2 (YE7) ------------------------------------------------------------
USER_STAFF = {"username": "Lan", "role": "STAFF"}


def test_ask_chat_passes_sources(monkeypatch):
    monkeypatch.setattr(ui_api._chat, "ask_chat", lambda question, user, history=None: {
        "answer": "Bảo quản tủ mát 2–6°C.", "refused": False, "fallback": False,
        "sources": [{"doc": "bao_quan.md", "title": "Quy định bảo quản"}, {"title": "thiếu doc"}, "rác"]})
    result = ui_api.ask_chat("Kem tươi bảo quản thế nào?", USER_STAFF)
    assert result["sources"] == [{"doc": "bao_quan.md", "title": "Quy định bảo quản"}]


@pytest.mark.parametrize("value, expected", [("mcp", "mcp"), (" MCP ", "mcp"), ("local", "local"),
                                             ("", "local"), ("khac", "local"), (None, "local")])
def test_tool_mode(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AGENT_TOOLS", raising=False)
    else:
        monkeypatch.setenv("AGENT_TOOLS", value)
    assert ui_api.tool_mode() == expected


@pytest.mark.parametrize("value, expected", [("true", True), ("TRUE", True), ("1", True),
                                             ("false", False), ("", False), (None, False)])
def test_tracing_enabled(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    else:
        monkeypatch.setenv("LANGSMITH_TRACING", value)
    assert ui_api.tracing_enabled() is expected


G7_SUMMARY = {
    "run_at": "2026-11-28T09:30:00", "tag": "mcp", "mode": "live", "agent_tools": "mcp", "n": 4,
    "pass_rate": 0.75, "numbers_ok": 1.0, "refusal_ok": None, "tools_ok": 0.5, "source_ok": 0.5,
    "recall_at_3": 1.0, "fallback_rate": 0.25, "latency_ms_avg": 2350.0, "tokens_avg": None,
    "by_group": {"data": 1.0, "policy": 0.5},
}
G7_CSV = (
    "id,group,passed,numbers_ok,refusal_ok,tools_ok,source_ok,retrieval_hit,main_cause_ok,fallback,"
    "retriever,latency_ms,tokens,answer,note\n"
    "D01,data,True,True,True,True,,,,False,,2100,350,Kem tươi còn 10 lít.,\n"
    "P01,policy,True,,True,True,True,True,,False,bm25,2400,,\"Tủ mát 2–6°C, dùng trong 7 ngày.\",\n"
    "P02,policy,False,,True,False,False,False,,False,bm25,2600,,Chưa có quy định.,thiếu: tủ mát\n"
)


@pytest.fixture()
def eval_dir(tmp_path, monkeypatch):
    path = tmp_path / "results"
    monkeypatch.setattr(ui_api, "EVALS_RESULTS_DIR", path)
    return path


def write_eval_files(path, summary=None, csv_text=G7_CSV):
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps(summary or G7_SUMMARY, ensure_ascii=False), encoding="utf-8")
    (path / "latest.csv").write_text(csv_text, encoding="utf-8-sig")


def test_eval_summary_none_without_file(eval_dir):
    assert ui_api.get_eval_summary() is None
    assert ui_api.get_eval_rows() == []


def test_eval_summary_none_with_broken_file(eval_dir):
    eval_dir.mkdir()
    (eval_dir / "summary.json").write_text("{không phải json", encoding="utf-8")
    assert ui_api.get_eval_summary() is None


def test_eval_summary_reads_g7_keys(eval_dir):
    write_eval_files(eval_dir)
    summary = ui_api.get_eval_summary()
    assert list(summary) == list(ui_api.EVAL_SUMMARY_KEYS)
    assert summary["pass_rate"] == 0.75 and summary["agent_tools"] == "mcp"
    assert summary["by_group"] == {"data": 1.0, "policy": 0.5}


def test_eval_summary_missing_keys_are_none(eval_dir):
    write_eval_files(eval_dir, summary={"pass_rate": 0.5, "by_group": None})
    summary = ui_api.get_eval_summary()
    assert summary["pass_rate"] == 0.5
    assert summary["recall_at_3"] is None and summary["mode"] is None
    assert summary["by_group"] == {}


def test_eval_rows_parse_types(eval_dir):
    write_eval_files(eval_dir)
    rows = ui_api.get_eval_rows()
    assert [r["id"] for r in rows] == ["D01", "P01", "P02"]
    assert rows[0]["passed"] is True and rows[0]["numbers_ok"] is True and rows[0]["source_ok"] is None
    assert rows[0]["latency_ms"] == 2100 and rows[0]["tokens"] == 350 and rows[1]["tokens"] is None
    assert rows[1]["answer"] == "Tủ mát 2–6°C, dùng trong 7 ngày."


def test_eval_rows_filter_by_group_and_passed(eval_dir):
    write_eval_files(eval_dir)
    assert [r["id"] for r in ui_api.get_eval_rows(group="policy")] == ["P01", "P02"]
    assert [r["id"] for r in ui_api.get_eval_rows(passed=False)] == ["P02"]
    assert [r["id"] for r in ui_api.get_eval_rows(group="policy", passed=True)] == ["P01"]
    assert ui_api.get_eval_rows(group="audit") == []


# ---- AppTest: chạy cả app.py như người dùng mở trình duyệt (cần streamlit) ---------------
APP_PATH = Path(__file__).resolve().parents[2] / "app.py"


@pytest.fixture()
def app_env(db, eval_dir, monkeypatch):
    """DB mẫu tạm + thư mục kết quả Evals tạm. app.py đọc .env thật của máy: chạy xong trả lại
    biến môi trường như cũ để không ảnh hưởng test khác."""
    monkeypatch.setenv("AGENT_TOOLS", "local")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    saved = os.environ.copy()
    yield eval_dir
    os.environ.clear()
    os.environ.update(saved)


def run_app(role="MANAGER", **state):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["user"] = {"username": "Yến" if role == "MANAGER" else "Lan", "role": role}
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(elements):
    return [str(e.value) for e in elements]


def test_app_5_tabs_without_eval_files(app_env):
    at = run_app()
    assert [t.label for t in at.tabs][-1] == "📊 Đánh giá Agent"
    assert len(at.tabs) == 5
    assert any("Chưa có kết quả đánh giá" in t for t in texts(at.info))
    assert any("python -m evals.run" in t for t in texts(at.code))


def test_app_shows_eval_numbers(app_env):
    write_eval_files(app_env)
    at = run_app()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["✅ Tỉ lệ đạt"] == "75%"
    assert metrics["🔢 Số liệu đúng"] == "100%"
    assert metrics["🛡️ Từ chối đúng"] == "—"          # null -> "—", không báo lỗi
    assert metrics["📄 Nguồn đúng"] == "50%"
    assert metrics["🔍 Tìm đúng tài liệu"] == "100%"
    assert metrics["⚠️ Dùng câu dự phòng"] == "25%"
    assert metrics["⏱️ Thời gian trung bình"] == "2.4 giây"
    run_line = next(t for t in texts(at.caption) if t.startswith("Lần chạy"))
    assert "Gemini thật" in run_line and "Công cụ: MCP" in run_line
    page = "\n".join(texts(at.markdown))
    assert "Hỏi quy định" in page and "P02" in page and "thiếu: tủ mát" in page


def test_app_missing_summary_keys_show_dash(app_env):
    write_eval_files(app_env, summary={"pass_rate": 0.5})
    at = run_app()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["✅ Tỉ lệ đạt"] == "50%" and metrics["🔍 Tìm đúng tài liệu"] == "—"


def test_app_chat_answer_shows_sources(app_env):
    history = [{"question": "Kem tươi bảo quản thế nào?", "answer": "Tủ mát 2–6°C.", "refused": False,
                "fallback": False, "sources": [{"doc": "bao_quan.md", "title": "Quy định bảo quản"},
                                               {"doc": "nhan_hang.md", "title": "Quy định nhận hàng"}]}]
    at = run_app(chat_history=history)
    captions = texts(at.caption)
    assert "📄 Nguồn: Quy định bảo quản" in captions
    assert "📄 Nguồn: Quy định nhận hàng" in captions
    assert any("Kem tươi bảo quản thế nào?" in t for t in captions)     # câu ví dụ hỏi quy định


def test_app_log_shows_trace_link(app_env):
    from services.audit_log import write_audit_log
    write_audit_log("Yến", "CHAT_ANSWERED", entity_type="chat",
                    detail={"question": "Kem?", "trace_url": "https://smith.langchain.com/r/abc123"})
    write_audit_log("Yến", "CHAT_ANSWERED", entity_type="chat",
                    detail={"question": "Bơ?", "trace_url": "javascript:alert(1)"})
    at = run_app()
    page = "\n".join(texts(at.markdown))
    assert 'href="https://smith.langchain.com/r/abc123"' in page and "Xem chi tiết AI" in page
    assert "javascript:" not in page


def test_app_sidebar_shows_tool_mode_and_tracing(app_env, monkeypatch):
    monkeypatch.setenv("AGENT_TOOLS", "mcp")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    at = run_app()
    sidebar = texts(at.sidebar.caption)
    assert "Công cụ: MCP" in sidebar and "Theo dõi AI: Bật" in sidebar


def test_app_staff_still_view_only(app_env):
    at = run_app(role="STAFF")
    labels = [b.label for b in at.button]
    assert "▶️ Chạy rà soát" not in labels
    assert "🔒 Chỉ quản lý chạy rà soát" in texts(at.caption)
    sidebar = texts(at.sidebar.caption)
    assert "Công cụ: Gọi trực tiếp" in sidebar and "Theo dõi AI: Tắt" in sidebar
