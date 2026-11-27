"""Unit test cho mcp_server/server.py (QA4). Chạy: pytest tests/unit/test_mcp_server.py -v

Gọi server qua MCP client thật (mcp.Client): trong cùng tiến trình cho phần lớn test, và một lần
bằng tiến trình con `python -m mcp_server.server` để kiểm cách chạy stdio. Mỗi lần gọi có giới hạn
thời gian để test không treo.
"""
import inspect
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import Client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import tools as agent_tools  # noqa: E402
from data.init_db import init_db  # noqa: E402
from mcp_server import server  # noqa: E402

NOW = datetime(2026, 11, 20, 8, 0)
TIMEOUT = 10  # giây cho mỗi lần gọi MCP
FOUR = ("check_inventory_fefo", "calc_preorder_demand", "check_delayed_po", "compare_suppliers")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    path = init_db(tmp_path / "test.db", "standard", now=NOW)
    monkeypatch.setenv("BAKERY_DB_PATH", str(path))
    return path


def _run(coro_fn):
    async def wrapped():
        with anyio.fail_after(TIMEOUT):
            async with Client(server.mcp) as client:
                return await coro_fn(client)
    return anyio.run(wrapped)


def _call(name, args=None):
    """Gọi tool qua MCP, trả (is_error, dict kết quả đọc từ nội dung JSON)."""
    async def go(client):
        return await client.call_tool(name, args or {})
    result = _run(go)
    text = result.content[0].text
    return result.is_error, (json.loads(text) if not result.is_error else text)


def _same_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


# ---------- Danh sách tool ----------

def test_list_tools_exactly_five_read_tools():
    tools = _run(lambda c: c.list_tools()).tools
    names = [t.name for t in tools]
    assert sorted(names) == sorted(server.TOOL_NAMES) and len(names) == 5
    assert server.TOOL_NAMES == FOUR + ("search_policy",)
    for forbidden in ("create_draft_po", "write_audit_log", "issue_stock_fefo"):
        assert forbidden not in names
    assert all(t.description and any(ch in t.description for ch in "ồệăưđ") for t in tools)  # mô tả tiếng Việt


def test_input_schema_matches_original_signatures():
    schemas = {t.name: t.input_schema for t in _run(lambda c: c.list_tools()).tools}
    for name in FOUR:
        params = inspect.signature(agent_tools.READ_TOOLS[name]).parameters
        props = schemas[name]["properties"]
        assert list(props) == list(params)  # cùng tên, cùng thứ tự
        required = [k for k, p in params.items() if p.default is inspect.Parameter.empty]
        assert schemas[name].get("required", []) == required
        for k, p in params.items():
            if p.default is not inspect.Parameter.empty:
                assert props[k]["default"] == p.default
    inv = schemas["check_inventory_fefo"]["properties"]
    assert inv["ingredient_id"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "type" not in inv["at"] and "type" not in inv["days"]  # Any như hàm gốc
    assert schemas["compare_suppliers"]["properties"]["ingredient_id"]["type"] == "string"
    sp = schemas["search_policy"]
    assert sp["properties"]["query"]["type"] == "string" and sp["required"] == ["query"]
    assert sp["properties"]["k"] == {"default": 3, "title": "K", "type": "integer"}


# ---------- Kết quả giống hệt gọi trực tiếp ----------

def test_check_inventory_fefo_via_mcp_equals_direct_call(db):
    is_error, via_mcp = _call("check_inventory_fefo", {"ingredient_id": "KEM_TUOI"})
    assert is_error is False
    assert via_mcp == _same_json(agent_tools.check_inventory_fefo("KEM_TUOI"))
    assert via_mcp["ok"] is True and [b["batch_id"] for b in via_mcp["batches"]] == ["KEM-L1", "KEM-L2"]


@pytest.mark.parametrize("name, args", [
    ("check_inventory_fefo", {"ingredient_id": "KEM_TUOI", "days": 3}),
    ("check_inventory_fefo", {}),
    ("calc_preorder_demand", {"ingredient_id": "BO_LAT"}),
    ("check_delayed_po", {}),
    ("compare_suppliers", {"ingredient_id": "BOT_MI"}),
])
def test_every_read_tool_matches_direct_call(db, name, args):
    is_error, via_mcp = _call(name, args)
    assert is_error is False
    assert via_mcp == _same_json(agent_tools.READ_TOOLS[name](**args))


def test_search_policy_not_ready():
    assert not hasattr(agent_tools, "search_policy")  # đổi khi agent/tools.py có hàm thật
    is_error, result = _call("search_policy", {"query": "bảo quản kem tươi"})
    assert is_error is False
    assert result["ok"] is False and result["error_code"] == "NOT_READY" and result["message"]


# ---------- Tham số sai: ok:false, không raise, không treo ----------

@pytest.mark.parametrize("name, args, code", [
    ("check_inventory_fefo", {"ingredient_id": "KEM_TUOI", "days": -1}, "INVALID_ARGUMENT"),
    ("check_inventory_fefo", {"ingredient_id": "KEM_TUOI", "at": "ngày mai"}, "INVALID_ARGUMENT"),
    ("calc_preorder_demand", {"days": "ba"}, "INVALID_ARGUMENT"),
    ("check_inventory_fefo", {"ingredient_id": "KHONG_CO"}, "INGREDIENT_NOT_FOUND"),
    ("calc_preorder_demand", {"ingredient_id": "KHONG_CO"}, "INGREDIENT_NOT_FOUND"),
    ("check_delayed_po", {"ingredient_id": "KHONG_CO"}, "INGREDIENT_NOT_FOUND"),
    ("compare_suppliers", {"ingredient_id": "KHONG_CO"}, "INGREDIENT_NOT_FOUND"),
    # Hai lỗi bị chặn ở bước kiểm kiểu của MCP, server đổi thành dict:
    ("check_inventory_fefo", {"ingredient_id": 123}, "INVALID_ARGUMENT"),
    ("compare_suppliers", {}, "INVALID_ARGUMENT"),
    ("search_policy", {"query": "x", "k": "nhiều"}, "INVALID_ARGUMENT"),
])
def test_bad_arguments_return_ok_false(db, name, args, code):
    is_error, result = _call(name, args)
    assert is_error is False
    assert result["ok"] is False and result["error_code"] == code and result["message"]


def test_validation_message_does_not_echo_value(db):
    _, result = _call("check_inventory_fefo", {"ingredient_id": 987654})
    assert "ingredient_id" in result["message"] and "987654" not in result["message"]


def test_unexpected_tool_failure_returns_tool_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATE", NOW.isoformat(timespec="minutes"))
    monkeypatch.setenv("BAKERY_DB_PATH", str(tmp_path))  # là thư mục, sqlite không mở được
    is_error, result = _call("compare_suppliers", {"ingredient_id": "BO_LAT"})
    assert is_error is False
    assert result == {"ok": False, "error_code": "TOOL_ERROR", "message": result["message"]}


# ---------- Chạy thật: python -m mcp_server.server ----------

def _env(db_path):
    env = dict(os.environ)
    env.update({"DEMO_DATE": NOW.isoformat(timespec="minutes"), "BAKERY_DB_PATH": str(db_path),
                "PYTHONIOENCODING": "utf-8"})
    return env


def test_server_module_does_not_load_write_services():
    code = ("import sys, mcp_server.server; "
            "bad = [m for m in ('services.po_service', 'services.inventory_service', 'services.audit_log', "
            "'services.ui_api') if m in sys.modules]; sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_stdio_startup_writes_nothing_to_stdout(db):
    # stdin đóng ngay → server tự thoát; stdout phải trống, log khởi động nằm ở stderr.
    proc = subprocess.run([sys.executable, "-m", "mcp_server.server"], cwd=ROOT, env=_env(db),
                          input=b"", capture_output=True, timeout=60)
    assert proc.stdout == b""
    assert b"bakery-tools" in proc.stderr


def test_stdio_client_end_to_end(db):
    from mcp import StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"],
                                   cwd=str(ROOT), env=_env(db))

    async def go():
        with anyio.fail_after(60):
            async with Client(params) as client:
                names = {t.name for t in (await client.list_tools()).tools}
                result = await client.call_tool("check_inventory_fefo", {"ingredient_id": "KEM_TUOI"})
                return names, json.loads(result.content[0].text)

    names, via_stdio = anyio.run(go)
    assert names == set(server.TOOL_NAMES)
    assert via_stdio == _same_json(agent_tools.check_inventory_fefo("KEM_TUOI"))
