"""StaffDeck 后端模块：技能生成与编辑所使用的模型配置和输出预算解析。

主要入口：skill_model_config；主要协作模块：app.db.models、app.llm.model_config_resolver。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from app.db.models import ModelConfig
from app.llm.model_config_resolver import snapshot_model_config


SKILL_MAX_OUTPUT_TOKENS = 8192


def skill_model_config(model_config: ModelConfig) -> ModelConfig:
    return snapshot_model_config(model_config, min_output_tokens=SKILL_MAX_OUTPUT_TOKENS)
