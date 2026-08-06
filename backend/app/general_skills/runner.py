"""StaffDeck 后端模块：通用技能选择与执行器，物化技能包、准备隔离环境并运行脚本。

主要类型：GeneralSkillSelector, GeneralSkillRunner；主要协作模块：app.db.models、app.general_skills.runtime_env、app.general_skills.schema。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import codecs
import json
import os
import queue
import selectors
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from app import paths
from app.db.models import GeneralSkill, ModelConfig, Tool
from app.general_skills.runtime_env import (
    GeneralSkillRuntimeError,
    ensure_runtime_python,
    runtime_environment,
)
from app.general_skills.schema import (
    GeneralSkillExecutionPlan,
    GeneralSkillExecutionReview,
    GeneralSkillReply,
    GeneralSkillRunResponse,
    GeneralSkillSelection,
)
from app.llm import LLMClient, LLMError
from app.llm.model_config_resolver import snapshot_model_config
from app.llm.stage_protocol import stage_payload, unified_system_prompt
from app.observability.spans import llm_operation

PROMPT_DIR = paths.resource_dir() / "app" / "llm" / "prompts"
SELECTOR_PROMPT = PROMPT_DIR / "general_skill_selector_prompt.md"
RUNNER_PROMPT = PROMPT_DIR / "general_skill_runner_prompt.md"
REPAIR_PROMPT = PROMPT_DIR / "general_skill_repair_prompt.md"
REVIEW_PROMPT = PROMPT_DIR / "general_skill_review_prompt.md"
REPLY_PROMPT = PROMPT_DIR / "general_skill_reply_prompt.md"
RUN_TIMEOUT_SECONDS = 12
MAX_OUTPUT_CHARS = 20000
GENERAL_SKILL_MAX_TOKENS = 8192
GENERAL_SKILL_MAX_ATTEMPTS = 3
GENERAL_SKILL_MODEL_TIMEOUT_SECONDS = 90.0
GENERAL_SKILL_TOTAL_TIMEOUT_SECONDS = 180.0
TraceSink = Callable[[dict[str, Any]], None]
GENERAL_SKILL_SELECTION_OUTPUT = {
    "use_tool": "boolean",
    "tool_call": {"name": "string", "arguments": "object"},
    "use_general_skill": "boolean",
    "selected_slug": "string?",
    "use_knowledge": "boolean",
    "knowledge_query": "string?",
    "confidence": "number",
    "reason": "string?",
}
GENERAL_SKILL_PLAN_OUTPUT = {
    "execution_mode": "direct | runner",
    "code": "string",
    "runtime": "bash | python",
    "rationale": "string?",
    "expected_output": "string?",
    "structured_result": "object",
    "reply": "string?",
}
GENERAL_SKILL_REVIEW_OUTPUT = {
    "result_sufficient": "boolean",
    "needs_retry": "boolean",
    "terminal": "boolean",
    "reason": "string",
    "repair_hint": "string?",
}
GENERAL_SKILL_REPLY_OUTPUT = {"reply": "string"}


@dataclass(frozen=True)
class _RunControl:
    deadline: float
    cancel_event: threading.Event
    runtime_timeout_seconds: float


_ACTIVE_RUN_CONTROL: ContextVar[_RunControl | None] = ContextVar(
    "general_skill_run_control", default=None
)


class GeneralSkillRunCancelled(RuntimeError):
    pass


class GeneralSkillSelector:
    # 阅读提示：这是 AgentLoop 的能力选择模型。输入只包含当前员工可见的
    # Tool 和已发布通用技能；输出是“选择什么”，不包含 transport 路由或执行权。
    def decide(
        self,
        query: str,
        general_skills: list[GeneralSkill],
        model_config: ModelConfig,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
        available_tools: list[Tool] | None = None,
    ) -> GeneralSkillSelection:
        enabled_tools = [tool for tool in (available_tools or []) if tool.enabled]
        payload = stage_payload(
            phase="Router / General Skill Selector",
            user_message=query,
            conversation_context=conversation_context,
            memory_context=memory_context,
            instructions=SELECTOR_PROMPT.read_text(encoding="utf-8"),
            stage_data={
                "available_tools": [
                    {
                        "name": tool.name,
                        "display_name": tool.display_name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                    for tool in enabled_tools
                ],
                "general_skills": [
                    {
                        "slug": skill.slug,
                        "name": skill.name,
                        "description": skill.description,
                        "homepage": skill.homepage,
                        "status": skill.status,
                    }
                    for skill in general_skills
                    if skill.status == "published"
                ],
            },
            output_contract=GENERAL_SKILL_SELECTION_OUTPUT,
        )
        with llm_operation("general_skill.select"):
            raw = LLMClient(model_config).generate_json(
                unified_system_prompt(), payload
            )
        decision = GeneralSkillSelection.model_validate(raw)
        # 模型只能返回 StaffDeck 本地 Tool 全名。即使它编造了一个工具名，
        # 这里也会清空选择；MCP Server 和远端叶子工具名稍后由后端数据库关系解析。
        tool_names = {tool.name for tool in enabled_tools}
        if (
            not decision.use_tool
            or decision.tool_call is None
            or decision.tool_call.name not in tool_names
        ):
            decision = decision.model_copy(update={"use_tool": False, "tool_call": None})
        slugs = {skill.slug for skill in general_skills if skill.status == "published"}
        if decision.use_general_skill and decision.selected_slug in slugs:
            return decision
        return decision.model_copy(update={"use_general_skill": False, "selected_slug": None})


class GeneralSkillRunner:
    # 阅读提示：物化技能包并在受控运行环境执行，统一收集输出、错误和超时。
    def run(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        user_id: str = "",
        max_attempts: int = GENERAL_SKILL_MAX_ATTEMPTS,
        event_sink: TraceSink | None = None,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> GeneralSkillRunResponse:
        control = _RunControl(
            deadline=time.monotonic() + GENERAL_SKILL_TOTAL_TIMEOUT_SECONDS,
            cancel_event=cancel_event or threading.Event(),
            runtime_timeout_seconds=_runtime_timeout_seconds(skill),
        )
        token = _ACTIVE_RUN_CONTROL.set(control)
        try:
            return self._run(
                skill,
                query,
                model_config,
                user_id,
                max_attempts,
                event_sink,
                conversation_context,
                memory_context,
            )
        finally:
            _ACTIVE_RUN_CONTROL.reset(token)

    def _run(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        user_id: str,
        max_attempts: int,
        event_sink: TraceSink | None,
        conversation_context: dict[str, object] | None,
        memory_context: list[dict[str, object]] | None,
    ) -> GeneralSkillRunResponse:
        trace: list[dict[str, Any]] = []
        max_attempts = max(1, min(max_attempts, GENERAL_SKILL_MAX_ATTEMPTS))
        _emit(trace, {"phase": "skill_loaded", "message": f"已加载通用技能 {skill.name}", "slug": skill.slug}, event_sink)
        try:
            _raise_if_run_stopped("生成执行方案")
            plan, planning_attempts = self._generate_plan_with_reflection(
                skill,
                query,
                model_config,
                trace,
                event_sink,
                max_attempts,
                conversation_context,
                memory_context,
            )
        except GeneralSkillRunCancelled as exc:
            structured_result = _cancelled_result(str(exc))
            _emit(
                trace,
                {
                    "phase": "run_cancelled",
                    "message": str(exc),
                    "structured_result": structured_result,
                },
                event_sink,
            )
            return GeneralSkillRunResponse(
                skill_slug=skill.slug,
                execution_trace=trace,
                generated_code="",
                stdout="",
                stderr=str(exc),
                structured_result=structured_result,
                reply=_fallback_reply(structured_result),
            )
        except LLMError as exc:
            _emit(trace, {"phase": "plan_failed", "message": "模型生成执行方案失败", "error": str(exc)}, event_sink)
            return GeneralSkillRunResponse(
                skill_slug=skill.slug,
                execution_trace=trace,
                generated_code="",
                stdout="",
                stderr=str(exc),
                structured_result={"success": False, "error": "execution_plan_failed", "message": str(exc)},
                reply="抱歉，当前通用技能执行方案生成失败，暂时无法完成这次运行。",
            )

        if plan.execution_mode == "direct":
            try:
                _raise_if_run_stopped("直接执行技能")
            except GeneralSkillRunCancelled as exc:
                structured_result = _cancelled_result(str(exc))
                _emit(
                    trace,
                    {
                        "phase": "run_cancelled",
                        "message": str(exc),
                        "structured_result": structured_result,
                    },
                    event_sink,
                )
                return GeneralSkillRunResponse(
                    skill_slug=skill.slug,
                    execution_trace=trace,
                    generated_code="",
                    stdout="",
                    stderr=str(exc),
                    structured_result=structured_result,
                    reply=_fallback_reply(structured_result),
                )
            structured_result = dict(plan.structured_result or {})
            structured_result.setdefault("success", True)
            _emit(
                trace,
                {
                    "phase": "direct_execution_started",
                    "message": "正在直接执行文本技能",
                    "execution_mode": "direct",
                },
                event_sink,
            )
            _emit(
                trace,
                {
                    "phase": "direct_execution_finished",
                    "message": "文本技能执行完成",
                    "execution_mode": "direct",
                    "structured_result": structured_result,
                },
                event_sink,
            )
            return GeneralSkillRunResponse(
                skill_slug=skill.slug,
                execution_trace=trace,
                generated_code="",
                stdout="",
                stderr="",
                structured_result=structured_result,
                reply=str(plan.reply or "").strip(),
            )

        attempts: list[dict[str, Any]] = planning_attempts
        stdout = ""
        stderr = ""
        structured_result: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            try:
                _raise_if_run_stopped(f"第 {attempt} 次运行")
            except GeneralSkillRunCancelled as exc:
                structured_result = _cancelled_result(str(exc), structured_result)
                stderr = str(exc)
                _emit(
                    trace,
                    {
                        "phase": "run_cancelled",
                        "message": str(exc),
                        "attempt": attempt,
                        "structured_result": structured_result,
                    },
                    event_sink,
                )
                break
            _emit(
                trace,
                {"phase": "attempt_started", "message": f"开始第 {attempt} 次运行", "attempt": attempt},
                event_sink,
            )
            stdout, stderr, structured_result = self._execute_plan(
                skill,
                query,
                plan,
                user_id,
                trace,
                event_sink,
                attempt,
            )
            _normalize_failure_diagnostics(structured_result)
            if structured_result.get("error") == "runner_syntax_error":
                review = {
                    "result_sufficient": False,
                    "needs_retry": True,
                    "terminal": False,
                    "reason": str(structured_result.get("message") or "Runner 语法检查失败"),
                    "repair_hint": "根据准确的语法错误行号修复代码，不要重复上一版代码。",
                }
                _emit(
                    trace,
                    {
                        "phase": "reflection_reviewed",
                        "message": "语法错误已直接进入代码修复",
                        "attempt": attempt,
                        "review": review,
                    },
                    event_sink,
                )
            else:
                review = self._review_execution_result(
                    skill,
                    query,
                    model_config,
                    plan,
                    stdout,
                    stderr,
                    structured_result,
                    trace,
                    event_sink,
                    attempt,
                    conversation_context,
                    memory_context,
                )
            attempts.append(
                {
                    "attempt": attempt,
                    "code": _truncate(plan.code),
                    "stdout": _truncate(stdout),
                    "stderr": _truncate(stderr),
                    "structured_result": structured_result,
                    "execution_review": review,
                }
            )
            needs_retry = bool(review.get("needs_retry"))
            if not needs_retry:
                if structured_result.get("success") is False or review.get("result_sufficient") is False:
                    _emit(
                        trace,
                        {
                            "phase": "reflection_stopped",
                            "message": f"第 {attempt} 次运行结果不足，但模型判断不可继续自动修复",
                            "attempt": attempt,
                            "structured_result": structured_result,
                            "review": review,
                        },
                        event_sink,
                    )
                else:
                    _emit(
                        trace,
                        {"phase": "reflection_passed", "message": f"第 {attempt} 次运行结果可用", "attempt": attempt},
                        event_sink,
                    )
                break
            if attempt >= max_attempts:
                _emit(
                    trace,
                    {
                        "phase": "reflection_stopped",
                        "message": f"已达到最多 {max_attempts} 次尝试，停止自动修复",
                        "attempt": attempt,
                    },
                    event_sink,
                )
                break
            _emit(
                trace,
                {
                    "phase": "reflection_retrying",
                    "message": f"第 {attempt} 次运行未达预期，模型正在根据结果反思修复",
                    "attempt": attempt,
                    "stdout_preview": stdout[:600],
                    "stderr_preview": stderr[:600],
                    "structured_result": structured_result,
                    "review": review,
                },
                event_sink,
            )
            try:
                plan = self._repair_plan(
                    skill,
                    query,
                    model_config,
                    trace,
                    attempts,
                    event_sink,
                    attempt + 1,
                    conversation_context,
                    memory_context,
                )
            except (LLMError, GeneralSkillRunCancelled) as exc:
                _emit(
                    trace,
                    {"phase": "repair_failed", "message": "模型反思修复代码失败", "attempt": attempt, "error": str(exc)},
                    event_sink,
                )
                break

        try:
            _raise_if_run_stopped("生成最终回复")
            reply = self._generate_reply(
                skill,
                query,
                model_config,
                trace,
                stdout,
                stderr,
                structured_result,
                event_sink,
                conversation_context,
                memory_context,
            )
        except (LLMError, GeneralSkillRunCancelled) as exc:
            _emit(trace, {"phase": "reply_failed", "message": "模型生成最终回复失败", "error": str(exc)}, event_sink)
            reply = _fallback_reply(structured_result)
        return GeneralSkillRunResponse(
            skill_slug=skill.slug,
            execution_trace=trace,
            generated_code=plan.code,
            stdout=stdout,
            stderr=stderr,
            structured_result=structured_result,
            reply=reply,
        )

    def _generate_plan(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        trace: list[dict[str, Any]],
        event_sink: TraceSink | None = None,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
    ) -> GeneralSkillExecutionPlan:
        _emit(trace, {"phase": "planning", "message": "正在根据 SKILL.md 选择执行方式"}, event_sink)
        stage_data = {
            "skill": {
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
                "homepage": skill.homepage,
                "markdown": skill.skill_markdown,
                "package": _skill_package_payload(skill),
            },
            "runtime": {
                "languages": ["bash", "python"],
                "stdin_json": {
                    "query": query,
                    "skill_slug": skill.slug,
                    "skill_name": skill.name,
                    "skill_workspace": "<runtime absolute path to the restored skill folder>",
                    "skill_files": [file["path"] for file in _skill_files(skill)],
                },
                "timeout_seconds": _active_runtime_timeout_seconds(),
            },
        }
        payload = stage_payload(
            phase="Step Agent / General Skill Plan",
            user_message=query,
            conversation_context=conversation_context,
            memory_context=memory_context,
            instructions=RUNNER_PROMPT.read_text(encoding="utf-8"),
            stage_data=stage_data,
            output_contract=GENERAL_SKILL_PLAN_OUTPUT,
        )
        with llm_operation("general_skill.plan"):
            raw = LLMClient(_bounded_model_config(model_config, GENERAL_SKILL_MAX_TOKENS)).generate_json(
                unified_system_prompt(),
                payload,
            )
        try:
            plan = GeneralSkillExecutionPlan.model_validate(raw)
        except Exception as exc:
            raise LLMError(f"General skill execution plan returned invalid schema: {exc}") from exc
        plan.runtime = _plan_runtime(plan)
        if plan.execution_mode == "direct":
            _emit(
                trace,
                {
                    "phase": "plan_created",
                    "message": "已生成直接执行方案",
                    "execution_mode": "direct",
                    "rationale": plan.rationale,
                    "expected_output": plan.expected_output,
                },
                event_sink,
            )
            return plan
        runtime_label = _runtime_label(plan.runtime)
        _emit(
            trace,
            {
                "phase": "plan_created",
                "message": f"已生成 {runtime_label} runner",
                "execution_mode": "runner",
                "runtime": plan.runtime,
                "rationale": plan.rationale,
                "code": plan.code,
                "expected_output": plan.expected_output,
            },
            event_sink,
        )
        return plan

    def _generate_plan_with_reflection(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        trace: list[dict[str, Any]],
        event_sink: TraceSink | None,
        max_attempts: int,
        conversation_context: dict[str, object] | None,
        memory_context: list[dict[str, object]] | None,
    ) -> tuple[GeneralSkillExecutionPlan, list[dict[str, Any]]]:
        planning_failures: list[dict[str, Any]] = []
        last_error: LLMError | None = None
        for plan_attempt in range(1, max_attempts + 1):
            try:
                if plan_attempt == 1:
                    return (
                        self._generate_plan(
                            skill,
                            query,
                            model_config,
                            trace,
                            event_sink,
                            conversation_context,
                            memory_context,
                        ),
                        planning_failures,
                    )
                return (
                    self._repair_plan(
                        skill,
                        query,
                        model_config,
                        trace,
                        planning_failures,
                        event_sink,
                        plan_attempt,
                        conversation_context,
                        memory_context,
                    ),
                    planning_failures,
                )
            except LLMError as exc:
                last_error = exc
                failure = {
                    "attempt": f"planning-{plan_attempt}",
                    "code": "",
                    "stdout": "",
                    "stderr": str(exc),
                    "structured_result": {
                        "success": False,
                        "error": "plan_generation_failed",
                        "message": str(exc),
                        "retryable": True,
                    },
                    "execution_review": {
                        "result_sufficient": False,
                        "needs_retry": plan_attempt < max_attempts,
                        "terminal": False,
                        "reason": "模型未能生成可执行 runner 计划，需要重新输出合法 JSON、runtime 和完整代码。",
                        "repair_hint": "保留原始 skill 与 query，重新输出包含 runtime、code、rationale、expected_output 的合法 JSON。",
                    },
                }
                planning_failures.append(failure)
                _emit(
                    trace,
                    {
                        "phase": "plan_failed",
                        "message": f"第 {plan_attempt} 次 runner 计划生成失败",
                        "attempt": plan_attempt,
                        "error": str(exc),
                    },
                    event_sink,
                )
                if plan_attempt >= max_attempts:
                    break
                _emit(
                    trace,
                    {
                        "phase": "reflection_retrying",
                        "message": f"第 {plan_attempt} 次计划生成失败，模型正在反思并重新输出代码",
                        "attempt": plan_attempt,
                        "structured_result": failure["structured_result"],
                        "review": failure["execution_review"],
                    },
                    event_sink,
                )
        raise LLMError(str(last_error) if last_error else "General skill runner plan generation failed")

    def _repair_plan(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        trace: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        event_sink: TraceSink | None,
        next_attempt: int,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
    ) -> GeneralSkillExecutionPlan:
        _emit(
            trace,
            {"phase": "repair_planning", "message": f"正在生成第 {next_attempt} 次运行代码", "attempt": next_attempt},
            event_sink,
        )
        stage_data = {
            "skill": {
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
                "homepage": skill.homepage,
                "markdown": skill.skill_markdown,
                "package": _skill_package_payload(skill),
            },
            "runtime": {
                "languages": ["bash", "python"],
                "stdin_json": {
                    "query": query,
                    "skill_slug": skill.slug,
                    "skill_name": skill.name,
                    "skill_workspace": "<runtime absolute path to the restored skill folder>",
                    "skill_files": [file["path"] for file in _skill_files(skill)],
                },
                "timeout_seconds": _active_runtime_timeout_seconds(),
            },
            "previous_attempts": attempts[-3:],
        }
        payload = stage_payload(
            phase="Step Agent / General Skill Repair",
            user_message=query,
            conversation_context=conversation_context,
            memory_context=memory_context,
            instructions=REPAIR_PROMPT.read_text(encoding="utf-8"),
            stage_data=stage_data,
            output_contract=GENERAL_SKILL_PLAN_OUTPUT,
        )
        with llm_operation("general_skill.repair", attempt=next_attempt):
            raw = LLMClient(_bounded_model_config(model_config, GENERAL_SKILL_MAX_TOKENS)).generate_json(
                unified_system_prompt(),
                payload,
            )
        try:
            plan = GeneralSkillExecutionPlan.model_validate(raw)
        except Exception as exc:
            raise LLMError(f"General skill repaired plan returned invalid schema: {exc}") from exc
        plan.runtime = _plan_runtime(plan)
        if plan.execution_mode == "direct":
            _emit(
                trace,
                {
                    "phase": "plan_created",
                    "message": f"已生成第 {next_attempt} 次直接执行方案",
                    "attempt": next_attempt,
                    "execution_mode": "direct",
                    "rationale": plan.rationale,
                    "expected_output": plan.expected_output,
                },
                event_sink,
            )
            return plan
        runtime_label = _runtime_label(plan.runtime)
        _emit(
            trace,
            {
                "phase": "plan_created",
                "message": f"已生成第 {next_attempt} 次 {runtime_label} runner",
                "attempt": next_attempt,
                "execution_mode": "runner",
                "runtime": plan.runtime,
                "rationale": plan.rationale,
                "code": plan.code,
                "expected_output": plan.expected_output,
            },
            event_sink,
        )
        return plan

    def _execute_plan(
        self,
        skill: GeneralSkill,
        query: str,
        plan: GeneralSkillExecutionPlan,
        user_id: str,
        trace: list[dict[str, Any]],
        event_sink: TraceSink | None = None,
        attempt: int = 1,
    ) -> tuple[str, str, dict[str, Any]]:
        _raise_if_run_stopped(f"第 {attempt} 次运行")
        run_dir = Path(mkdtemp(prefix="ultrarag_general_skill_"))
        skill_dir = run_dir / "skill"
        _materialize_skill_package(skill, skill_dir)
        runtime = _plan_runtime(plan)
        runner_path = run_dir / ("runner.sh" if runtime == "bash" else "runner.py")
        runner_path.write_text(plan.code, encoding="utf-8")
        syntax_failure = _runner_syntax_failure(runtime, runner_path, plan.code)
        if syntax_failure is not None:
            message = str(syntax_failure["message"])
            _emit(
                trace,
                {
                    "phase": "code_validation_failed",
                    "message": message,
                    "attempt": attempt,
                    "runtime": runtime,
                    "structured_result": syntax_failure,
                },
                event_sink,
            )
            return "", message, syntax_failure
        stdin_payload = {
            "query": query,
            "skill_slug": skill.slug,
            "skill_name": skill.name,
            "user_id": user_id,
            "skill_workspace": str(skill_dir),
            "skill_files": [file["path"] for file in _skill_files(skill)],
        }
        _emit(
            trace,
            {
                "phase": "running_code",
                "message": f"正在运行第 {attempt} 次 {_runtime_label(runtime)} runner",
                "run_id": run_dir.name,
                "attempt": attempt,
                "runtime": runtime,
            },
            event_sink,
        )
        try:
            runtime_python = ensure_runtime_python()
            env = runtime_environment(os.environ.copy())
        except GeneralSkillRuntimeError as exc:
            structured = {
                "success": False,
                "error": "runtime_environment_error",
                "message": str(exc),
                "retryable": False,
            }
            _emit(
                trace,
                {
                    "phase": "runtime_environment_failed",
                    "message": "通用技能运行环境准备失败",
                    "attempt": attempt,
                    "runtime": runtime,
                    "structured_result": structured,
                },
                event_sink,
            )
            return "", str(exc), structured
        env.update(
            {
                "ARGUMENTS": query,
                "QUERY": query,
                "SKILL_WORKSPACE": str(skill_dir),
                "SKILL_SLUG": skill.slug,
                "SKILL_NAME": skill.name,
                "USER_ID": user_id,
                "SKILL_FILES_JSON": json.dumps([file["path"] for file in _skill_files(skill)], ensure_ascii=False),
            }
        )
        if runtime == "bash" and not _bash_supported():
            structured = {
                "success": False,
                "error": "bash_runtime_unsupported",
                "message": "当前运行环境不支持 bash 技能（Windows 或打包版），请改用 Python 技能。",
                "retryable": False,
            }
            _emit(trace, {"phase": "runtime_environment_failed",
                          "message": "bash runtime 不受支持", "attempt": attempt,
                          "runtime": runtime, "structured_result": structured}, event_sink)
            return "", structured["message"], structured
        command = ["/bin/bash", str(runner_path)] if runtime == "bash" else [str(runtime_python), str(runner_path)]
        cwd = str(skill_dir if runtime == "bash" else run_dir)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            text=False,
        )
        if process.stdin:
            process.stdin.write(json.dumps(stdin_payload, ensure_ascii=False).encode("utf-8"))
            process.stdin.close()

        try:
            control = _ACTIVE_RUN_CONTROL.get()
            stdout, stderr, timed_out = _stream_process_output(
                process,
                trace,
                event_sink,
                attempt,
                timeout_seconds=_active_runtime_timeout_seconds(),
                cancel_event=control.cancel_event if control else None,
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        control = _ACTIVE_RUN_CONTROL.get()
        if control and control.cancel_event.is_set():
            message = "通用技能运行已取消"
            structured = _cancelled_result(message)
            _emit(
                trace,
                {
                    "phase": "run_cancelled",
                    "message": message,
                    "attempt": attempt,
                    "runtime": runtime,
                    "structured_result": structured,
                },
                event_sink,
            )
            return _truncate(stdout), _truncate(stderr or message), structured

        if timed_out:
            stdout = _truncate(stdout)
            stderr = _truncate(stderr)
            timeout_seconds = _active_runtime_timeout_seconds()
            structured = {
                "success": False,
                "error": "runner_timeout",
                "message": f"通用技能运行超过 {timeout_seconds:g} 秒",
                "retryable": True,
            }
            _emit(
                trace,
                {
                    "phase": "code_timeout",
                    "message": f"{_runtime_label(runtime)} runner 执行超时",
                    "attempt": attempt,
                    "runtime": runtime,
                    "stdout_preview": stdout[:600],
                    "stderr_preview": stderr[:600],
                    "structured_result": structured,
                },
                event_sink,
            )
            return stdout, stderr, structured

        return_code = process.wait()
        stdout = _truncate(stdout)
        stderr = _truncate(stderr)
        structured = _parse_stdout_json(stdout)
        if return_code != 0:
            structured.setdefault("success", False)
            structured.setdefault("error", f"runner exited with code {return_code}")
        _emit(
            trace,
            {
                "phase": "code_finished",
                "message": f"{_runtime_label(runtime)} runner 执行完成",
                "attempt": attempt,
                "runtime": runtime,
                "return_code": return_code,
                "stdout_preview": stdout[:600],
                "stderr_preview": stderr[:600],
                "structured_result": structured,
            },
            event_sink,
        )
        return stdout, stderr, structured

    def _generate_reply(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        trace: list[dict[str, Any]],
        stdout: str,
        stderr: str,
        structured_result: dict[str, Any],
        event_sink: TraceSink | None = None,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
    ) -> str:
        _emit(trace, {"phase": "replying", "message": "正在根据运行结果生成回复"}, event_sink)
        stage_data = {
            "skill": {
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
            },
            "execution_trace": trace,
            "stdout": stdout,
            "stderr": stderr,
            "structured_result": structured_result,
        }
        payload = stage_payload(
            phase="Response Generator / General Skill Reply",
            user_message=query,
            conversation_context=conversation_context,
            memory_context=memory_context,
            instructions=REPLY_PROMPT.read_text(encoding="utf-8"),
            stage_data=stage_data,
            output_contract=GENERAL_SKILL_REPLY_OUTPUT,
        )
        try:
            with llm_operation("general_skill.reply"):
                raw = LLMClient(_bounded_model_config(model_config)).generate_json(
                    unified_system_prompt(), payload
                )
            reply = GeneralSkillReply.model_validate(raw).reply.strip()
        except (LLMError, GeneralSkillRunCancelled):
            raise
        except Exception as exc:
            raise LLMError(f"General skill reply returned invalid JSON schema: {exc}") from exc
        if not reply:
            raise LLMError("General skill reply is empty")
        _emit(trace, {"phase": "reply_created", "message": "已生成最终回复"}, event_sink)
        return reply

    def _review_execution_result(
        self,
        skill: GeneralSkill,
        query: str,
        model_config: ModelConfig,
        plan: GeneralSkillExecutionPlan,
        stdout: str,
        stderr: str,
        structured_result: dict[str, Any],
        trace: list[dict[str, Any]],
        event_sink: TraceSink | None,
        attempt: int,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        _emit(
            trace,
            {
                "phase": "reflection_reviewing",
                "message": f"正在校验第 {attempt} 次运行结果",
                "attempt": attempt,
            },
            event_sink,
        )
        stage_data = {
            "skill": {
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
                "homepage": skill.homepage,
                "markdown": _truncate(skill.skill_markdown, 6000),
                "package": _skill_package_payload(skill, preview_limit=6000),
            },
            "runner": {
                "rationale": plan.rationale,
                "expected_output": plan.expected_output,
                "code_preview": _truncate(plan.code, 6000),
            },
            "attempt": attempt,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "structured_result": structured_result,
        }
        payload = stage_payload(
            phase="Reflection / General Skill Review",
            user_message=query,
            conversation_context=conversation_context,
            memory_context=memory_context,
            instructions=REVIEW_PROMPT.read_text(encoding="utf-8"),
            stage_data=stage_data,
            output_contract=GENERAL_SKILL_REVIEW_OUTPUT,
        )
        try:
            with llm_operation("general_skill.review", attempt=attempt):
                raw = LLMClient(_bounded_model_config(model_config)).generate_json(
                    unified_system_prompt(), payload
                )
            review = GeneralSkillExecutionReview.model_validate(raw).model_dump(mode="json")
        except GeneralSkillRunCancelled:
            raise
        except Exception as exc:
            fallback_needs_retry = _execution_needs_retry(stdout, stderr, structured_result)
            review = {
                "result_sufficient": not fallback_needs_retry,
                "needs_retry": fallback_needs_retry,
                "terminal": False,
                "reason": f"模型校验失败，使用运行信号兜底判断：{exc}",
                "repair_hint": "补充运行诊断或调整 runner 输出结构",
            }
        if review.get("terminal") is True:
            review["needs_retry"] = False
        _emit(
            trace,
            {
                "phase": "reflection_reviewed",
                "message": "已完成运行结果校验",
                "attempt": attempt,
                "review": review,
            },
            event_sink,
        )
        return review


def _truncate(value: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...<truncated>"


def _plan_runtime(plan: GeneralSkillExecutionPlan) -> str:
    runtime = str(getattr(plan, "runtime", "") or "python").strip().lower()
    if runtime in {"bash", "shell", "sh"}:
        return "bash"
    return "python"


def _runtime_label(runtime: str) -> str:
    return "Bash" if runtime == "bash" else "Python"


def _skill_files(skill: GeneralSkill) -> list[dict[str, Any]]:
    raw_files = getattr(skill, "skill_files_json", None)
    files = (
        raw_files
        if isinstance(raw_files, Sequence) and not isinstance(raw_files, (str, bytes))
        else []
    )
    normalized: list[dict[str, Any]] = []
    for raw_file in files:
        if not isinstance(raw_file, Mapping):
            continue
        path = _safe_package_path(str(raw_file.get("path") or ""))
        content = str(raw_file.get("content") or "")
        if not path:
            continue
        normalized.append(
            {
                "path": path,
                "content": content,
                "size": int(raw_file.get("size") or len(content.encode("utf-8"))),
                "mime_type": raw_file.get("mime_type"),
            }
        )
    if normalized:
        return normalized
    markdown = str(getattr(skill, "skill_markdown", "") or "")
    return [{"path": "SKILL.md", "content": markdown, "size": len(markdown.encode("utf-8")), "mime_type": "text/markdown"}]


def _skill_package_payload(skill: GeneralSkill, preview_limit: int = 12000) -> dict[str, Any]:
    files = _skill_files(skill)
    previews: list[dict[str, Any]] = []
    remaining = preview_limit
    for file in files:
        content = str(file.get("content") or "")
        preview = content[: max(0, min(len(content), remaining))]
        remaining -= len(preview)
        previews.append(
            {
                "path": file["path"],
                "size": file.get("size"),
                "mime_type": file.get("mime_type"),
                "content_preview": preview,
                "truncated": len(preview) < len(content),
            }
        )
    return {
        "entrypoint": "SKILL.md",
        "file_count": len(files),
        "files": previews,
    }


def _materialize_skill_package(skill: GeneralSkill, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for file in _skill_files(skill):
        relative_path = _safe_package_path(str(file["path"]))
        output_path = target_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(str(file.get("content") or ""), encoding="utf-8")


def _safe_package_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip().strip("/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        return {"success": False, "message": "runner produced no stdout"}
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
        return {"success": True, "data": value}
    except json.JSONDecodeError:
        return {"success": True, "text": stripped}


def _stream_process_output_selectors(
    process: subprocess.Popen[bytes],
    trace: list[dict[str, Any]],
    event_sink: TraceSink | None,
    attempt: int,
    timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str, bool]:
    selector = selectors.DefaultSelector()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    decoders = _utf8_stream_decoders()
    streams: list[tuple[Any, str]] = []
    if process.stdout:
        streams.append((process.stdout, "stdout"))
    if process.stderr:
        streams.append((process.stderr, "stderr"))
    for stream, name in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, data=name)

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while selector.get_map():
            if cancel_event and cancel_event.is_set():
                process.kill()
                break
            if time.monotonic() > deadline:
                timed_out = True
                process.kill()
                break
            events = selector.select(timeout=0.1)
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in list(selector.get_map().values())]
            for key, _ in events:
                name = str(key.data)
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    _append_decoded_output(
                        name,
                        b"",
                        decoders,
                        stdout_parts,
                        stderr_parts,
                        trace,
                        event_sink,
                        attempt,
                        final=True,
                    )
                    try:
                        selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    continue
                _append_decoded_output(
                    name,
                    chunk,
                    decoders,
                    stdout_parts,
                    stderr_parts,
                    trace,
                    event_sink,
                    attempt,
                )
    finally:
        selector.close()
    return "".join(stdout_parts), "".join(stderr_parts), timed_out


def _use_thread_reader() -> bool:
    return sys.platform == "win32"


def _stream_process_output(
    process,
    trace,
    event_sink,
    attempt,
    timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
):
    if _use_thread_reader():
        return _stream_process_output_threaded(
            process,
            trace,
            event_sink,
            attempt,
            timeout_seconds,
            cancel_event,
        )
    return _stream_process_output_selectors(
        process,
        trace,
        event_sink,
        attempt,
        timeout_seconds,
        cancel_event,
    )


def _stream_process_output_threaded(
    process,
    trace,
    event_sink,
    attempt,
    timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
):
    q: "queue.Queue[tuple[str, bytes]]" = queue.Queue()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    decoders = _utf8_stream_decoders()

    def _reader(stream, name: str) -> None:
        try:
            for chunk in iter(lambda: stream.read(4096), b""):
                q.put((name, chunk))
        finally:
            q.put((name, b""))  # EOF 标记

    stream_map = [(process.stdout, "stdout"), (process.stderr, "stderr")]
    threads: list[threading.Thread] = []
    for stream, name in stream_map:
        if stream is None:
            continue
        t = threading.Thread(target=_reader, args=(stream, name), daemon=True)
        t.start()
        threads.append(t)

    open_streams = sum(1 for s, _ in stream_map if s is not None)
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    eof_count = 0
    while eof_count < open_streams:
        if cancel_event and cancel_event.is_set():
            process.kill()
            break
        if time.monotonic() > deadline:
            timed_out = True
            process.kill()
            break
        try:
            name, chunk = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if chunk == b"":
            _append_decoded_output(
                name,
                b"",
                decoders,
                stdout_parts,
                stderr_parts,
                trace,
                event_sink,
                attempt,
                final=True,
            )
            eof_count += 1
            continue
        _append_decoded_output(
            name,
            chunk,
            decoders,
            stdout_parts,
            stderr_parts,
            trace,
            event_sink,
            attempt,
        )

    for t in threads:
        t.join(timeout=1.0)
    return "".join(stdout_parts), "".join(stderr_parts), timed_out


def _utf8_stream_decoders() -> dict[str, Any]:
    decoder = codecs.getincrementaldecoder("utf-8")
    return {
        "stdout": decoder(errors="replace"),
        "stderr": decoder(errors="replace"),
    }


def _append_decoded_output(
    name: str,
    chunk: bytes,
    decoders: dict[str, Any],
    stdout_parts: list[str],
    stderr_parts: list[str],
    trace: list[dict[str, Any]],
    event_sink: TraceSink | None,
    attempt: int,
    *,
    final: bool = False,
) -> None:
    text = decoders[name].decode(chunk, final=final)
    if not text:
        return
    if name == "stdout":
        stdout_parts.append(text)
        phase, message = "stdout_chunk", "收到运行输出"
    else:
        stderr_parts.append(text)
        phase, message = "stderr_chunk", "收到错误输出"
    _emit(
        trace,
        {"phase": phase, "message": message, "attempt": attempt, "text": text},
        event_sink,
    )


def _bash_supported() -> bool:
    if sys.platform == "win32":
        return False
    if paths.is_frozen():
        return False
    return Path("/bin/bash").exists()


def _emit(trace: list[dict[str, Any]], item: dict[str, Any], event_sink: TraceSink | None = None) -> None:
    trace.append(item)
    if event_sink:
        event_sink(item)


def _execution_needs_retry(stdout: str, stderr: str, structured_result: dict[str, Any]) -> bool:
    if structured_result.get("success") is False:
        if structured_result.get("retryable") is False or structured_result.get("terminal") is True:
            return False
        return True
    if structured_result.get("error") or structured_result.get("error_code"):
        return True
    if stderr.strip():
        return True
    if not stdout.strip():
        return True
    return False


def _normalize_failure_diagnostics(structured_result: dict[str, Any]) -> None:
    if structured_result.get("success") is not False:
        return
    diagnostic_keys = {
        "diagnostics",
        "attempted_urls",
        "status_code",
        "exception",
        "exception_type",
        "response_preview",
        "parse_strategy",
    }
    if any(key in structured_result for key in diagnostic_keys):
        return
    structured_result.setdefault("diagnostics_missing", True)
    structured_result.setdefault(
        "diagnostics_required",
        [
            "attempted_urls",
            "status_code",
            "exception_type",
            "exception_message",
            "response_preview",
            "parse_strategy",
            "retryable",
        ],
    )


def _runtime_timeout_seconds(skill: GeneralSkill) -> float:
    config = getattr(skill, "runtime_config_json", None)
    raw_value = config.get("timeout_seconds") if isinstance(config, Mapping) else None
    try:
        timeout_seconds = float(raw_value)
    except (TypeError, ValueError):
        return float(RUN_TIMEOUT_SECONDS)
    if not 1 <= timeout_seconds <= 300:
        return float(RUN_TIMEOUT_SECONDS)
    return timeout_seconds


def _active_runtime_timeout_seconds() -> float:
    control = _ACTIVE_RUN_CONTROL.get()
    return control.runtime_timeout_seconds if control else float(RUN_TIMEOUT_SECONDS)


def _bounded_model_config(model_config: ModelConfig, min_output_tokens: int = 0):
    control = _ACTIVE_RUN_CONTROL.get()
    snapshot = snapshot_model_config(model_config, min_output_tokens=min_output_tokens)
    configured_timeout = getattr(snapshot, "timeout_seconds", None)
    timeout_seconds = GENERAL_SKILL_MODEL_TIMEOUT_SECONDS
    if configured_timeout is not None:
        timeout_seconds = min(timeout_seconds, float(configured_timeout))
    if control:
        remaining = control.deadline - time.monotonic()
        if remaining <= 0:
            raise GeneralSkillRunCancelled("通用技能已达到 180 秒总运行时限")
        timeout_seconds = min(timeout_seconds, remaining)
    return replace(snapshot, timeout_seconds=max(1.0, timeout_seconds))


def _raise_if_run_stopped(stage: str) -> None:
    control = _ACTIVE_RUN_CONTROL.get()
    if not control:
        return
    if control.cancel_event.is_set():
        raise GeneralSkillRunCancelled(f"通用技能已取消，停止在：{stage}")
    if time.monotonic() >= control.deadline:
        raise GeneralSkillRunCancelled(f"通用技能已达到 180 秒总运行时限，停止在：{stage}")


def _cancelled_result(
    message: str,
    previous_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_message = ""
    if previous_result and previous_result.get("success") is False:
        previous_message = str(
            previous_result.get("message") or previous_result.get("error") or ""
        ).strip()
    combined = f"{previous_message}；{message}" if previous_message else message
    return {
        "success": False,
        "error": "general_skill_cancelled",
        "message": combined,
        "retryable": False,
    }


def _runner_syntax_failure(
    runtime: str,
    runner_path: Path,
    code: str,
) -> dict[str, Any] | None:
    if runtime == "python":
        try:
            compile(code, str(runner_path), "exec")
        except SyntaxError as exc:
            location = f"第 {exc.lineno or '?'} 行"
            if exc.offset:
                location += f"，第 {exc.offset} 列"
            message = f"SyntaxError: {exc.msg}（{location}）"
            return {
                "success": False,
                "error": "runner_syntax_error",
                "message": message,
                "exception_type": "SyntaxError",
                "exception_message": exc.msg,
                "line": exc.lineno,
                "offset": exc.offset,
                "parse_strategy": "python_compile",
                "retryable": True,
            }
        return None
    if runtime != "bash" or not _bash_supported():
        return None
    result = subprocess.run(
        ["/bin/bash", "-n", str(runner_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        check=False,
    )
    if result.returncode == 0:
        return None
    message = (result.stderr or result.stdout or "Bash 语法检查失败").strip()
    return {
        "success": False,
        "error": "runner_syntax_error",
        "message": message,
        "exception_type": "BashSyntaxError",
        "exception_message": message,
        "parse_strategy": "bash_n",
        "retryable": True,
    }


def _fallback_reply(structured_result: dict[str, Any]) -> str:
    if structured_result.get("success") is False:
        message = str(structured_result.get("message") or structured_result.get("error") or "").strip()
        return f"抱歉，通用技能运行失败。{message}" if message else "抱歉，通用技能运行失败。"
    return "通用技能已运行完成，结果已展示在运行输出中。"
