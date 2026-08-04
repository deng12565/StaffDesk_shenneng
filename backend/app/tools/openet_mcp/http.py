"""OpenET MCP 的受保护 Streamable HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.security.internal_service import require_internal_service
from app.tools.openet_mcp.protocol import error_response, handle_request
from app.tools.openet_mcp.service import OpenETService

router = APIRouter(
    prefix="/api/mcp/openet",
    tags=["mcp:openet"],
    dependencies=[Depends(require_internal_service)],
)


@router.post("")
async def openet_mcp_http(request: Request) -> Response:
    """在单一 POST 端点处理无状态 MCP initialize、通知、发现和调用。"""
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(error_response(None, -32700, "Parse error"), status_code=400)

    response = await run_in_threadpool(handle_request, payload, OpenETService())
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)
