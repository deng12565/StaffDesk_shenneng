"""StaffDeck 后端模块：adapters 子系统的公共导出入口，调用方可从这里导入稳定接口；具体实现位于 「app.channels.adapters.base」。

该模块主要提供包级导出或常量，阅读具体行为时请继续进入同目录实现文件。
"""

from app.channels.adapters.base import (
    ChannelAdapter,
    ChannelInbound,
    ChannelReactionAdapter,
    channel_reaction_token,
    get_channel_adapter,
    register_channel_adapter,
    split_channel_text,
)

__all__ = [
    "ChannelAdapter",
    "ChannelInbound",
    "ChannelReactionAdapter",
    "channel_reaction_token",
    "get_channel_adapter",
    "register_channel_adapter",
    "split_channel_text",
]
