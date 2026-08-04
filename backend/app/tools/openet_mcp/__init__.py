"""OpenET 天气能力的 Streamable HTTP MCP Server。

外部入口是 ``/api/mcp/openet``；协议层将 ``tools/call`` 交给
``OpenETService``，服务层再处理地点解析、数据集选择和 OpenET HTTP 请求。
"""

from app.tools.openet_mcp.catalog import CATALOG_VERSION, DATASETS
from app.tools.openet_mcp.service import OpenETClient, OpenETMCPError, OpenETService

__all__ = [
    "CATALOG_VERSION",
    "DATASETS",
    "OpenETClient",
    "OpenETMCPError",
    "OpenETService",
]
