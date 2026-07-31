"""StaffDeck 后端模块：feedback 子系统的公共导出入口，调用方可从这里导入稳定接口；具体实现位于 「app.feedback.jobs」、「app.feedback.service」。

该模块主要提供包级导出或常量，阅读具体行为时请继续进入同目录实现文件。
"""

from app.feedback.jobs import enqueue_feedback_analysis
from app.feedback.service import (
    FEEDBACK_BUCKET_LABELS,
    FeedbackAnalysisService,
    feedback_analysis_read,
    feedback_summary,
)

__all__ = [
    "FEEDBACK_BUCKET_LABELS",
    "FeedbackAnalysisService",
    "enqueue_feedback_analysis",
    "feedback_analysis_read",
    "feedback_summary",
]
