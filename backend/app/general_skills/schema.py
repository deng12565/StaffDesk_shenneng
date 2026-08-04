"""StaffDeck 后端模块：通用技能导入、文件包、执行和选择结果的数据契约。

主要类型：GeneralSkillFile, GeneralSkillImportRequest, GeneralSkillClawHubImportRequest, GeneralSkillPackageUploadRequest, GeneralSkillRead, GeneralSkillRunRequest。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.tools.tool_schema import ToolCall


class GeneralSkillFile(BaseModel):
    path: str
    content: str
    size: Optional[int] = None
    mime_type: Optional[str] = None


class GeneralSkillImportRequest(BaseModel):
    tenant_id: str
    agent_id: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    markdown: Optional[str] = None
    files: list[GeneralSkillFile] = Field(default_factory=list)
    status: str = "published"
    original_slug: Optional[str] = None


class GeneralSkillClawHubImportRequest(BaseModel):
    tenant_id: str
    agent_id: Optional[str] = None
    source: str
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    status: str = "published"


class GeneralSkillPackageUploadRequest(BaseModel):
    tenant_id: str
    agent_id: Optional[str] = None
    filename: str
    content_base64: str
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    status: str = "published"


class GeneralSkillRead(BaseModel):
    id: str
    tenant_id: str
    slug: str
    name: str
    description: Optional[str] = None
    homepage: Optional[str] = None
    skill_markdown: str
    skill_files: list[GeneralSkillFile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    permissions: dict[str, Any] = Field(default_factory=dict)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class GeneralSkillRunRequest(BaseModel):
    tenant_id: str
    agent_id: Optional[str] = None
    user_id: str = ""
    query: str
    session_id: Optional[str] = None
    model_config_id: Optional[str] = None
    max_attempts: int = Field(default=10, ge=1, le=10)


class GeneralSkillRunResponse(BaseModel):
    skill_slug: str
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    generated_code: str = ""
    stdout: str = ""
    stderr: str = ""
    structured_result: dict[str, Any] = Field(default_factory=dict)
    reply: str


class GeneralSkillSelection(BaseModel):
    """能力选择模型的结构化结果；仅表达意图，不授予工具执行权限。"""

    use_tool: bool = False
    tool_call: Optional[ToolCall] = None
    use_general_skill: bool = False
    selected_slug: Optional[str] = None
    use_knowledge: bool = False
    knowledge_query: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None


class GeneralSkillExecutionPlan(BaseModel):
    code: str
    runtime: str = "python"
    rationale: Optional[str] = None
    expected_output: Optional[str] = None


class GeneralSkillExecutionReview(BaseModel):
    result_sufficient: bool = False
    needs_retry: bool = False
    terminal: bool = False
    reason: str = ""
    repair_hint: Optional[str] = None


class GeneralSkillReply(BaseModel):
    reply: str
