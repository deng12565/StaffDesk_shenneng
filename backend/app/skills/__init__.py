"""StaffDeck 后端模块：skills 子系统的公共导出入口，调用方可从这里导入稳定接口；具体实现位于 「app.skills.skill_distiller」、「app.skills.skill_editor」。

该模块主要提供包级导出或常量，阅读具体行为时请继续进入同目录实现文件。
"""

from app.skills.skill_distiller import SkillDistiller
from app.skills.skill_editor import SkillEditor

__all__ = ["SkillDistiller", "SkillEditor"]
