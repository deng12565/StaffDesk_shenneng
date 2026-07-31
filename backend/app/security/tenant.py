"""StaffDeck 后端模块：租户存在性校验的共享依赖。

主要入口：ensure_tenant；主要协作模块：app.db.models。阅读时先从这些入口跟踪调用关系。
"""

from fastapi import HTTPException
from sqlmodel import Session

from app.db.models import Tenant


def ensure_tenant(session: Session, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")
    return tenant

