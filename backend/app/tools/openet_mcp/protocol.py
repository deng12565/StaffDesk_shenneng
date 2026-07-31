"""Minimal line-delimited JSON-RPC MCP protocol for the local stdio server."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from app.tools.openet_mcp.service import TOOL_DEFINITIONS, OpenETMCPError, OpenETService


PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "staffdeck-openet", "version": "1.0.0"}


def main(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    service: OpenETService | None = None,
) -> None:
    source = input_stream or sys.stdin
    target = output_stream or sys.stdout
    openet = service or OpenETService()
    for line in source:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write_error(target, None, -32700, "Parse error")
            continue
        if not isinstance(request, dict):
            _write_error(target, None, -32600, "Invalid Request")
            continue
        _handle_request(request, target, openet)


def _handle_request(request: dict[str, Any], target: TextIO, service: OpenETService) -> None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        _write_result(
            target,
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
        return
    if method == "notifications/initialized":
        return
    if method == "tools/list":
        _write_result(target, request_id, {"tools": list(TOOL_DEFINITIONS)})
        return
    if method == "tools/call":
        _handle_tool_call(request_id, request.get("params"), target, service)
        return
    if request_id is not None:
        _write_error(target, request_id, -32601, f"Unsupported method: {method}")


def _handle_tool_call(
    request_id: Any,
    params: object,
    target: TextIO,
    service: OpenETService,
) -> None:
    if not isinstance(params, dict):
        _tool_error(
            target,
            request_id,
            "",
            OpenETMCPError("VALIDATION_ERROR", "tools/call params must be an object."),
        )
        return
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        _tool_error(
            target,
            request_id,
            "",
            OpenETMCPError("VALIDATION_ERROR", "tools/call name must be a string."),
        )
        return
    if not isinstance(arguments, dict):
        _tool_error(
            target,
            request_id,
            name,
            OpenETMCPError("VALIDATION_ERROR", "tools/call arguments must be an object."),
        )
        return
    try:
        result = service.call(name, arguments)
    except OpenETMCPError as exc:
        _tool_error(target, request_id, name, exc)
        return
    except Exception:
        _tool_error(
            target,
            request_id,
            name,
            OpenETMCPError("UPSTREAM_ERROR", "OpenET tool execution failed unexpectedly."),
        )
        return
    _write_result(
        target,
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result,
            "isError": False,
        },
    )


def _tool_error(
    target: TextIO,
    request_id: Any,
    tool_name: str,
    error: OpenETMCPError,
) -> None:
    payload = {"tool": tool_name, "error": error.as_dict()}
    _write_result(
        target,
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": True,
        },
    )


def _write_result(target: TextIO, request_id: Any, result: object) -> None:
    _write_json(target, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _write_error(target: TextIO, request_id: Any, code: int, message: str) -> None:
    _write_json(
        target,
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def _write_json(target: TextIO, payload: dict[str, Any]) -> None:
    target.write(json.dumps(payload, ensure_ascii=False) + "\n")
    target.flush()
