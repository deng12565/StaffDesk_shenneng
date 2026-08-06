"""StaffDeck 后端模块：FastAPI 应用装配入口，注册生命周期、健康检查和全部后端路由。

主要入口：on_startup, on_shutdown, health；主要协作模块：app.api、app.async_jobs、app.channels。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api import (
    agents,
    auth,
    channels,
    chat,
    feedback,
    general_skills,
    knowledge,
    knowledge_bases,
    memories,
    mock,
    model_configs,
    persona,
    recruiting,
    scheduled_tasks,
    sessions,
    skills,
    tools,
    traces,
    ui_config,
)
from app.async_jobs import shutdown_async_jobs
from app.channels import start_channel_services, stop_channel_services
from app.config import get_settings
from app.db import engine, init_db
from app.db.seed import seed_demo_data
from app.scheduled_tasks.worker import start_background_worker, stop_background_worker
from app.tools.openet_mcp.http import router as openet_mcp_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 阅读提示：应用启动钩子：先初始化数据库和种子数据，再启动后台任务与渠道服务。
@app.on_event("startup")
def on_startup() -> None:
    init_db()
    with Session(engine) as db:
        seed_demo_data(db)
    start_background_worker()
    start_channel_services()


# 阅读提示：应用关闭钩子：按相反顺序停止渠道、任务队列和日志线程，避免进程残留。
@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_channel_services()
    stop_background_worker()
    shutdown_async_jobs()


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": "StaffDeck"}


app.include_router(chat.router)
app.include_router(agents.chat_router)
app.include_router(ui_config.chat_router)
app.include_router(auth.router)
app.include_router(agents.scope_router)
app.include_router(agents.enterprise_router)
app.include_router(general_skills.router)
app.include_router(knowledge_bases.router)
app.include_router(knowledge.router)
app.include_router(skills.router)
app.include_router(model_configs.router)
app.include_router(memories.router)
app.include_router(feedback.router)
app.include_router(persona.router)
app.include_router(recruiting.router)
app.include_router(scheduled_tasks.enterprise_router)
app.include_router(scheduled_tasks.chat_router)
app.include_router(scheduled_tasks.chat_draft_router)
app.include_router(ui_config.enterprise_router)
app.include_router(channels.router)
app.include_router(tools.router)
app.include_router(tools.mcp_router)
app.include_router(sessions.router)
app.include_router(traces.router)
app.include_router(mock.router)
app.include_router(openet_mcp_router)
