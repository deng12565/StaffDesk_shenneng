"""StaffDeck 后端模块：会话事件日志写入器，为 Agent 执行过程持久化可追踪事件。

主要类型：EventLog；主要协作模块：app.db.models。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import AgentEvent


class EventLog:
    def __init__(self, db: Session):
        self.db = db
        self._turn_id: str | None = None
        self._client_turn_id: str | None = None

    def bind_turn(self, turn_id: str, client_turn_id: str | None = None) -> None:
        self._turn_id = str(turn_id or "").strip() or None
        self._client_turn_id = str(client_turn_id or "").strip() or None

    def record(self, tenant_id: str, session_id: str, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        traced_payload = dict(payload)
        if self._turn_id:
            traced_payload.setdefault("turn_id", self._turn_id)
            traced_payload.setdefault("user_message_id", self._turn_id)
        if self._client_turn_id:
            traced_payload.setdefault("client_turn_id", self._client_turn_id)
        event = AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=event_type,
            payload_json=traced_payload,
        )
        self.db.add(event)
        return event
