"""StaffDeck 后端模块：llm 子系统的公共导出入口，调用方可从这里导入稳定接口；具体实现位于 「app.llm.client」。

该模块主要提供包级导出或常量，阅读具体行为时请继续进入同目录实现文件。
"""

from app.llm.client import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]
