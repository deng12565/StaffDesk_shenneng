"""保留给旧配置的 stdio 兼容入口；当前 OpenET 连接使用 HTTP 路由。"""

from app.tools.openet_mcp.protocol import main


if __name__ == "__main__":
    main()
