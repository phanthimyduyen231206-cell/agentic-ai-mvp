"""MCP server "bakery-tools": công bố đúng 5 tool đọc, chạy stdio.

Chủ file: Quỳnh Anh (QA4). Chạy: python -m mcp_server.server

- Tool đọc lấy THẲNG hàm trong agent.tools.READ_TOOLS (không viết lại), nên tên, tham số, kiểu,
  giá trị mặc định và dict trả về giống hệt hàm gốc (contracts Mục 6). Mô tả tiếng Việt lấy từ
  agent.tools.TOOL_DECLARATIONS để chỉ có một nguồn.
- search_policy: dùng agent.tools.search_policy nếu đã có; chưa có thì bản tạm trả NOT_READY.
- Không import po_service, inventory_service, audit_log hay hàm ghi nào; server không có tool ghi.
- Tham số sai không raise: lỗi kiểm kiểu của MCP (sai kiểu, thiếu tham số bắt buộc) được đổi thành
  {"ok": False, "error_code": "INVALID_ARGUMENT", "message"} ở tầng server, schema giữ nguyên;
  lỗi bất ngờ khi chạy tool → {"ok": False, "error_code": "TOOL_ERROR", "message"}.
- stdout dành riêng cho giao thức MCP: log chỉ ra stderr, không có print.

Ghi chú: ở thư viện mcp 2.x, lớp FastMCP đã đổi tên thành MCPServer (mcp.server.mcpserver).
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.mcpserver.tools import Tool
from pydantic import ValidationError

from agent import tools as agent_tools

logger = logging.getLogger("mcp_server")

SERVER_NAME = "bakery-tools"
TOOL_NAMES = (
    "check_inventory_fefo",
    "calc_preorder_demand",
    "check_delayed_po",
    "compare_suppliers",
    "search_policy",
)

_SEARCH_POLICY_DESCRIPTION = (
    "Tìm đoạn quy định, chính sách của tiệm (bảo quản, nhập hàng, xử lý hàng trễ…) liên quan tới câu hỏi. "
    "query là câu hỏi hoặc từ khóa tiếng Việt; k là số đoạn trả về, mặc định 3."
)


def search_policy(query: str, k: int = 3) -> dict:
    """Bản tạm (giao ước G4): agent.tools chưa có search_policy nên chưa tra cứu được."""
    return {"ok": False, "error_code": "NOT_READY",
            "message": "Chức năng tra cứu chính sách (search_policy) chưa sẵn sàng."}


def _tool_functions() -> dict[str, Any]:
    """{tên: hàm} theo đúng thứ tự TOOL_NAMES. Thiếu tool đọc nào thì báo lỗi ngay lúc khởi động."""
    fns = dict(agent_tools.READ_TOOLS)
    fns["search_policy"] = getattr(agent_tools, "search_policy", None) or search_policy
    missing = [name for name in TOOL_NAMES if not callable(fns.get(name))]
    if missing:
        raise RuntimeError(f"agent/tools.py thiếu tool: {missing}")
    return {name: fns[name] for name in TOOL_NAMES}


def _description(name: str) -> str:
    spec = agent_tools.TOOL_DECLARATIONS.get(name)
    return spec["description"] if spec else _SEARCH_POLICY_DESCRIPTION


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error_code": code, "message": message}


def _validation_message(name: str, err: ValidationError) -> str:
    # Chỉ nêu tên tham số và loại lỗi, không lặp lại giá trị người gọi gửi lên.
    parts = sorted({f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()})
    return f"Tham số của {name} không hợp lệ – " + "; ".join(parts)


class BakeryMCPServer(MCPServer):
    """MCPServer đổi lỗi tham số / lỗi chạy tool thành dict ok:false thay vì kết quả isError."""

    def __init__(self, tool_objects: list[Tool]) -> None:
        super().__init__(SERVER_NAME, tools=tool_objects)
        self._tool_objects = {t.name: t for t in tool_objects}

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError:
            logger.exception("Tool %r lỗi bất ngờ", name)
            result = _error("TOOL_ERROR", f"Tool {name} gặp lỗi khi chạy.")
        except ToolError as exc:
            tool = self._tool_objects.get(name)
            if tool is None or not isinstance(exc.__cause__, ValidationError):
                raise  # tên tool không tồn tại: để MCP báo lỗi như thường
            logger.info("Tool %r: tham số không hợp lệ", name)
            result = _error("INVALID_ARGUMENT", _validation_message(name, exc.__cause__))
        # Đi qua cùng bộ chuyển kết quả của tool nên dạng trả về giống hệt khi tool chạy bình thường.
        return self._tool_objects[name].fn_metadata.convert_result(result)


def build_server() -> BakeryMCPServer:
    tool_objects = [Tool.from_function(fn, name=name, description=_description(name))
                    for name, fn in _tool_functions().items()]
    return BakeryMCPServer(tool_objects)


mcp = build_server()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from dotenv import load_dotenv
    load_dotenv(override=False)  # chỉ nạp biến môi trường, không in hay log nội dung .env
    logger.info("Khởi động MCP server %s (stdio), tool: %s", SERVER_NAME, ", ".join(TOOL_NAMES))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
