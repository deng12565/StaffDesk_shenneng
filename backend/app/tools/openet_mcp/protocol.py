"""OpenET MCP 的 JSON-RPC 协议层。

本模块只处理 MCP 握手、``tools/list`` 和 ``tools/call``；天气业务、
Nominatim 地点解析与 OpenET 请求都由 ``OpenETService`` 负责。HTTP 路由
与兼容的 stdio 入口共享同一套方法分发和结果封装。
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from app.tools.openet_mcp.service import TOOL_DEFINITIONS, OpenETMCPError, OpenETService

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "staffdeck-openet", "version": "1.0.0"}


# ========== 1. 兼容的 stdio 进程主循环 ==========
def main(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    service: OpenETService | None = None,
) -> None:
    """逐行读取 stdin 请求并将一行 JSON-RPC 响应写回 stdout。"""
    source = input_stream or sys.stdin
    target = output_stream or sys.stdout
    openet = service or OpenETService()
    for line in source:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write_json(target, error_response(None, -32700, "Parse error"))
            continue
        response = handle_request(request, openet)
        if response is not None:
            _write_json(target, response)


# ========== 2. MCP 方法路由 ==========
#
# tools/list 只暴露 TOOL_DEFINITIONS 中的 7 个 OpenET 工具。Nominatim 是
# OpenETService 内部的 HTTP 依赖，不是 MCP 工具，也不会被 LLM 单独选择。
def handle_request(
    request: object,
    service: OpenETService,
) -> dict[str, Any] | None:
    """处理一个 JSON-RPC object；通知返回 ``None``。"""
    if not isinstance(request, dict):
        return error_response(None, -32600, "Invalid Request")
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return result_response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return result_response(request_id, {"tools": list(TOOL_DEFINITIONS)})
    if method == "tools/call":
        return _handle_tool_call(request_id, request.get("params"), service)
    if request_id is not None:
        return error_response(request_id, -32601, f"Unsupported method: {method}")
    return None


# ========== 3. tools/call 参数校验与业务转发 ==========
def _handle_tool_call(
    request_id: Any,
    params: object,
    service: OpenETService,
) -> dict[str, Any]:
    """校验 MCP 外层参数后，将叶子工具名和 arguments 交给服务层。"""
    if not isinstance(params, dict):
        return _tool_error(
            request_id,
            "",
            OpenETMCPError("VALIDATION_ERROR", "tools/call params must be an object."),
        )
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        return _tool_error(
            request_id,
            "",
            OpenETMCPError("VALIDATION_ERROR", "tools/call name must be a string."),
        )
    if not isinstance(arguments, dict):
        return _tool_error(
            request_id,
            name,
            OpenETMCPError("VALIDATION_ERROR", "tools/call arguments must be an object."),
        )
    try:
        result = service.call(name, arguments)
    except OpenETMCPError as exc:
        return _tool_error(request_id, name, exc)
    except Exception:
        return _tool_error(
            request_id,
            name,
            OpenETMCPError("UPSTREAM_ERROR", "OpenET tool execution failed unexpectedly."),
        )
    return result_response(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result,
            "isError": False,
        },
    )


# ========== 4. MCP 标准结果与结构化错误封装 ==========
def _tool_error(
    request_id: Any,
    tool_name: str,
    error: OpenETMCPError,
) -> dict[str, Any]:
    payload = {"tool": tool_name, "error": error.as_dict()}
    return result_response(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": True,
        },
    )


def result_response(request_id: Any, result: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write_json(target: TextIO, payload: dict[str, Any]) -> None:
    target.write(json.dumps(payload, ensure_ascii=False) + "\n")
    target.flush()
