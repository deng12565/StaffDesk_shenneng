"""StaffDeck 后端模块：工具运行时执行器，先做权限检查，再分发 HTTP 或 MCP 调用并标准化结果。

主要类型：ToolExecutionPolicy, ToolExecutor；主要协作模块：app.agents.branching、app.config、app.db.models。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, select

from app.agents.branching import visible_tool_rows
from app.config import get_settings
from app.db.models import MCPServer, Tool
from app.security.internal_service import INTERNAL_SERVICE_HEADER, internal_service_token
from app.tools.http_request import prepare_get_request
from app.tools.mcp_client import MCPClientError, execute_mcp_tool
from app.tools.tool_schema import ToolCall, ToolError, ToolResult

SECRET_PATTERN = re.compile(r"\$\{secret\.([A-Z0-9_]+)\}")


@dataclass(frozen=True)
class ToolExecutionPolicy:
    timeout_seconds: float


class ToolExecutor:
    """所有运行时工具调用的统一边界。

    先按租户、员工和当前技能检查本地 Tool 权限，再按 tool_type 分发到 HTTP
    或 MCP。AgentLoop 不直接理解 MCP transport，这一层负责把统一 ToolCall
    转换成具体协议调用。
    """

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    # 阅读提示：所有模型工具调用最终进入这里，权限校验先于 HTTP 或 MCP 网络请求。
    def execute(
        self,
        tenant_id: str,
        tool_call: ToolCall,
        active_skill_id: str | None = None,
        agent_id: str | None = None,
    ) -> ToolResult:
        with self.db.no_autoflush:
            tool = self.db.exec(
                select(Tool).where(Tool.tenant_id == tenant_id, Tool.name == tool_call.name)
            ).first()
        if not tool:
            return self._error(tool_call.name, "NOT_FOUND", "工具不存在或未配置。")
        if not tool.enabled:
            return self._error(tool.name, "DISABLED", "工具当前未启用。")
        if agent_id and tool.id not in {
            row.id
            for row in visible_tool_rows(self.db, tenant_id, agent_id, include_inactive=False)
        }:
            return self._error(tool.name, "NOT_ALLOWED", "当前员工未启用该工具。")
        if (
            active_skill_id
            and tool.allowed_skills_json
            and active_skill_id not in tool.allowed_skills_json
        ):
            return self._error(tool.name, "NOT_ALLOWED", "当前技能不允许调用该工具。")

        if (tool.tool_type or "http") == "mcp":
            return self._execute_mcp_tool(tool, tool_call.arguments)
        if (tool.tool_type or "http") != "http":
            return self._error(
                tool.name, "UNSUPPORTED_TOOL_TYPE", f"不支持的工具类型：{tool.tool_type}"
            )

        headers = self._request_headers(
            tool.url,
            self._resolve_headers(tool.headers_json or {}, tool.auth_json or {}),
        )
        policy = self._execution_policy(tool)
        try:
            with httpx.Client(timeout=policy.timeout_seconds) as client:
                if tool.method.upper() == "GET":
                    request_url, request_kwargs = prepare_get_request(tool.url, tool_call.arguments)
                    response = client.request(
                        tool.method.upper(), request_url, headers=headers, **request_kwargs
                    )
                else:
                    response = client.request(
                        tool.method.upper(), tool.url, headers=headers, json=tool_call.arguments
                    )
                response.raise_for_status()
                return ToolResult(
                    tool_name=tool.name,
                    success=True,
                    data=self._response_data(response),
                    error=None,
                )
        except httpx.TimeoutException:
            return self._error(
                tool.name,
                "TIMEOUT",
                f"工具调用超过 {policy.timeout_seconds:g} 秒未返回。",
            )
        except httpx.HTTPStatusError as exc:
            return self._error(
                tool.name,
                "HTTP_ERROR",
                f"工具返回异常状态码：{exc.response.status_code}",
            )
        except Exception as exc:
            return self._error(tool.name, "EXECUTION_ERROR", str(exc))

    def _execute_mcp_tool(self, tool: Tool, arguments: dict[str, Any]) -> ToolResult:
        """把本地 Tool 映射成 MCP Server 连接和远端叶子工具后执行。"""
        try:
            config, tool_name = self._resolve_mcp_config(tool)
            policy = self._execution_policy(tool)
            data = execute_mcp_tool(
                config,
                arguments,
                timeout_seconds=policy.timeout_seconds,
                tool_name=tool_name,
            )
            return ToolResult(tool_name=tool.name, success=True, data=data, error=None)
        except MCPClientError as exc:
            return self._error(tool.name, "MCP_ERROR", str(exc))
        except Exception as exc:
            return self._error(tool.name, "MCP_EXECUTION_ERROR", str(exc))

    def _execution_policy(self, tool: Tool) -> ToolExecutionPolicy:
        execution = (tool.config_json or {}).get("execution")
        raw_timeout = execution.get("timeout_seconds") if isinstance(execution, dict) else None
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_seconds = self.settings.tool_timeout_seconds
        if not 1 <= timeout_seconds <= 300:
            timeout_seconds = self.settings.tool_timeout_seconds
        return ToolExecutionPolicy(timeout_seconds=timeout_seconds)

    def _resolve_mcp_config(self, tool: Tool) -> tuple[dict[str, Any], str | None]:
        """通过关联的 MCPServer 还原连接配置和远端工具名。

        MCPServer 保存共享的连接信息；Tool.config_json 只保存该 server 下的
        叶子工具名及执行策略。两者分开后，同一天气 server 的多个工具无需
        各自复制 URL、headers 或 stdio 启动参数。

        例如本地全名 ``openet.get_point_forecast`` 对应：
        ``Tool.mcp_server_id -> openet Server``，以及
        ``Tool.config_json.tool -> get_point_forecast``。这就是示例映射表在本
        项目中的持久化实现，不需要额外维护一份内存字典。
        """
        tool_config = tool.config_json or {}
        tool_name = (
            str(tool_config.get("tool") or tool_config.get("tool_name") or "").strip() or None
        )
        if not tool.mcp_server_id:
            raise MCPClientError("MCP 工具未关联 Server。")
        server = self.db.get(MCPServer, tool.mcp_server_id)
        if server is None:
            raise MCPClientError("MCP 工具关联的 Server 不存在或已删除。")
        if not server.enabled:
            raise MCPClientError("MCP Server 当前未启用。")
        return self._server_client_config(server), tool_name

    def _server_client_config(self, server: MCPServer) -> dict[str, Any]:
        """将数据库中的共享 Server 配置转换为 MCP 客户端连接参数。"""
        transport = server.transport or "streamable_http"
        config: dict[str, Any] = {"transport": transport}
        if transport in {"streamable_http", "sse"}:
            config["url"] = server.url or ""
            if server.headers_json:
                config["headers"] = dict(server.headers_json)
        elif transport == "stdio":
            config["command"] = server.command or ""
            config["args"] = list(server.args_json or [])
            if server.env_json:
                config["env"] = dict(server.env_json)
            if server.cwd:
                config["cwd"] = server.cwd
        elif transport == "builtin":
            config["server"] = "builtin.demo"
        return config

    def _response_data(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return response.text

    def _resolve_headers(self, headers: dict[str, Any], auth: dict[str, Any]) -> dict[str, str]:
        resolved = {key: self._resolve_secret(str(value)) for key, value in headers.items()}
        if auth.get("type") == "bearer" and auth.get("token"):
            resolved["Authorization"] = f"Bearer {self._resolve_secret(str(auth['token']))}"
        return resolved

    def _request_headers(self, url: str, headers: dict[str, str]) -> dict[str, str]:
        if not self._is_internal_mock_url(url):
            return headers
        resolved = dict(headers)
        resolved[INTERNAL_SERVICE_HEADER] = internal_service_token()
        return resolved

    def _is_internal_mock_url(self, url: str) -> bool:
        target = urlsplit(url)
        if not target.path.startswith("/api/mock/"):
            return False
        if not target.scheme and not target.netloc:
            return True
        configured = urlsplit(self.settings.normalized_tool_base_url)
        return (
            target.scheme.lower(),
            target.hostname,
            target.port or _default_port(target.scheme),
        ) == (
            configured.scheme.lower(),
            configured.hostname,
            configured.port or _default_port(configured.scheme),
        )

    def _resolve_secret(self, value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            return os.getenv(match.group(1), "")

        return SECRET_PATTERN.sub(repl, value)

    def _error(self, tool_name: str, code: str, message: str) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            data=None,
            error=ToolError(code=code, message=message),
        )


def _default_port(scheme: str) -> int | None:
    return 443 if scheme.lower() == "https" else 80 if scheme.lower() == "http" else None
