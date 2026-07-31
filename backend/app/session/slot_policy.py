"""StaffDeck 后端模块：槽位清理策略，移除路由阶段自动生成且不应跨阶段保留的消息字段。

主要入口：strip_router_generated_message_slots。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ROUTER_GENERATED_MESSAGE_SLOT_KEYS = {
    "message_content",
    "user_message",
    "rewritten_message",
    "normalized_message",
    "current_message",
    "source_message",
}


def strip_router_generated_message_slots(slots: Mapping[str, Any] | None) -> dict[str, Any]:
    """Router must not persist rewritten user text as skill slot values."""
    if not isinstance(slots, Mapping):
        return {}
    return {
        str(key): value
        for key, value in slots.items()
        if str(key).strip() not in ROUTER_GENERATED_MESSAGE_SLOT_KEYS
    }
