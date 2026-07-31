"""StaffDeck 后端模块：observability 子系统的公共导出入口，调用方可从这里导入稳定接口；具体实现位于 「app.observability.event_log」。

该模块主要提供包级导出或常量，阅读具体行为时请继续进入同目录实现文件。
"""

from app.observability.event_log import EventLog

__all__ = ["EventLog"]

