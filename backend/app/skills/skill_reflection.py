"""StaffDeck 后端模块：技能生成结果反思器，按规则和模型审查迭代修复技能结构。

主要入口：reflect_skill_response, reflect_skill_response_stream；主要协作模块：app.llm、app.skills.skill_schema。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any, TypeVar

from app import paths
from app.llm import LLMClient, LLMError
from app.skills.skill_schema import SkillCard, ToolSuggestion

PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "skill_reflection_prompt.md"
MAX_REFLECTION_ROUNDS = 3
RUBRIC_LABELS: dict[str, str] = {
    "source_alignment": "来源一致性",
    "closed_loop": "闭环能力",
    "adaptive_progression": "自适应推进",
    "tool_grounding": "工具依据",
    "tool_call_format": "工具调用格式",
    "side_effect_confirmation": "副作用确认",
    "interruption_and_recovery": "中断恢复",
}
RUBRICS = [
    {
        "name": name,
        "label": label,
    }
    for name, label in RUBRIC_LABELS.items()
]

ResponseT = TypeVar("ResponseT")
StatusCallback = Callable[[str], None]
NormalizeResponse = Callable[[dict[str, Any]], ResponseT]


def reflect_skill_response(
    *,
    client: LLMClient,
    source_kind: str,
    source_payload: dict[str, Any],
    response: ResponseT,
    candidate_skill: SkillCard,
    current_warnings: list[str],
    tool_suggestions: list[ToolSuggestion],
    normalize_response: NormalizeResponse[ResponseT],
    status_callback: StatusCallback | None = None,
) -> ResponseT:
    events = reflect_skill_response_stream(
        client=client,
        source_kind=source_kind,
        source_payload=source_payload,
        response=response,
        candidate_skill=candidate_skill,
        current_warnings=current_warnings,
        tool_suggestions=tool_suggestions,
        normalize_response=normalize_response,
    )
    while True:
        try:
            event = next(events)
            if event.get("event") == "status":
                text = event.get("data", {}).get("text") if isinstance(event.get("data"), dict) else None
                _emit(status_callback, str(text or ""))
        except StopIteration as stop:
            return stop.value


def reflect_skill_response_stream(
    *,
    client: LLMClient,
    source_kind: str,
    source_payload: dict[str, Any],
    response: ResponseT,
    candidate_skill: SkillCard,
    current_warnings: list[str],
    tool_suggestions: list[ToolSuggestion],
    normalize_response: NormalizeResponse[ResponseT],
):
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    reviewed = response
    reviewed_skill = candidate_skill
    candidate_warnings = list(current_warnings)
    source_warnings: list[str] = []
    suggestions = list(tool_suggestions)
    reflection_history: list[dict[str, Any]] = []

    for round_index in range(1, MAX_REFLECTION_ROUNDS + 1):
        yield _status_event(f"正在校验技能结果（{round_index}/{MAX_REFLECTION_ROUNDS}）")
        yield _status_event("校验范围：来源一致性、闭环能力、自适应推进、工具依据、工具调用格式、副作用确认、中断恢复")
        try:
            review = _model_review(
                client,
                prompt,
                {
                    "source_kind": source_kind,
                    "source": source_payload,
                    "candidate_skill": reviewed_skill.model_dump(mode="json"),
                    "current_warnings": _dedupe([*source_warnings, *candidate_warnings]),
                    "tool_suggestions": [item.model_dump(mode="json") for item in suggestions],
                    "rubrics": RUBRICS,
                    "reflection_round": round_index,
                    "max_reflection_rounds": MAX_REFLECTION_ROUNDS,
                    "reflection_history": reflection_history,
                },
            )
        except (LLMError, json.JSONDecodeError, TypeError, ValueError) as exc:
            yield _status_event("校验失败，保留当前技能草稿")
            return normalize_response(
                {
                    "draft_skill": reviewed_skill.model_dump(mode="json"),
                    "warnings": _merge_current_warnings(
                        source_warnings,
                        candidate_warnings,
                        f"模型校验未能完成，已保留当前技能草稿：{exc}",
                    ),
                    "tool_mentions": [item.model_dump(mode="json") for item in suggestions],
                }
            )

        reflection_history.append(_reflection_history_item(review))
        source_warnings = _dedupe(
            [*source_warnings, *_source_warnings_from_review(review, source_kind)]
        )

        failed = _failed_rubrics(review)
        if failed:
            for item in failed[:4]:
                yield _status_event(f"校验发现：{_rubric_label(item)} - {_finding_text(item)}")
        summary = str(review.get("summary") or "").strip()
        if summary:
            yield _status_event(f"校验结论：{summary}")

        if bool(review.get("passed")):
            yield _status_event("校验通过，技能草稿满足当前要求")
            return normalize_response(
                {
                    "draft_skill": reviewed_skill.model_dump(mode="json"),
                    "warnings": _merge_current_warnings(source_warnings, candidate_warnings),
                    "tool_mentions": [
                        *[item.model_dump(mode="json") for item in suggestions],
                        *_list_of_dicts(review.get("tool_mentions")),
                    ],
                }
            )

        revised_skill = review.get("draft_skill")
        if not isinstance(revised_skill, dict):
            yield _status_event("校验未通过，但模型未返回可修正草稿")
            return normalize_response(
                {
                    "draft_skill": reviewed_skill.model_dump(mode="json"),
                    "warnings": _merge_current_warnings(
                        source_warnings,
                        candidate_warnings,
                        *_unresolved_warnings_from_review(review),
                        "模型校验未通过，但未返回可修正 Skill Card，已保留当前草稿。",
                    ),
                    "tool_mentions": [
                        *[item.model_dump(mode="json") for item in suggestions],
                        *_list_of_dicts(review.get("tool_mentions")),
                    ],
                }
            )

        yield _status_event(f"校验未通过，正在应用第 {round_index} 轮修正")
        next_reviewed = normalize_response(
            {
                "draft_skill": revised_skill,
                "warnings": [],
                "tool_mentions": [
                    *[item.model_dump(mode="json") for item in suggestions],
                    *_list_of_dicts(review.get("tool_mentions")),
                ],
            }
        )
        next_skill = getattr(next_reviewed, "draft_skill")
        next_warnings = list(getattr(next_reviewed, "warnings", []))
        next_suggestions = list(getattr(next_reviewed, "tool_suggestions", suggestions))

        if _skill_fingerprint(next_skill) == _skill_fingerprint(reviewed_skill):
            yield _status_event("修正未产生实际变化，提前结束校验")
            return next_reviewed.model_copy(
                update={
                    "warnings": _merge_current_warnings(
                        source_warnings,
                        next_warnings,
                        *_unresolved_warnings_from_review(review),
                        "模型修正未改变当前技能草稿，已提前结束重复校验。",
                    )
                }
            )

        reviewed = next_reviewed
        reviewed_skill = next_skill
        candidate_warnings = next_warnings
        suggestions = next_suggestions

        if round_index == MAX_REFLECTION_ROUNDS:
            yield _status_event("校验达到上限，保留最后一版未复核修正版")
            return reviewed.model_copy(
                update={
                    "warnings": _merge_current_warnings(
                        source_warnings,
                        candidate_warnings,
                        f"模型校验已达到 {MAX_REFLECTION_ROUNDS} 轮上限，最后一版修正尚未再次模型复核。",
                    )
                }
            )

    return reviewed


def _model_review(client: LLMClient, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
    text = client.generate_text(prompt, payload)
    raw = json.loads(_extract_json(text))
    if not isinstance(raw, dict):
        raise ValueError("反思模型输出不是 JSON object")
    return raw


def _source_warnings_from_review(review: dict[str, Any], source_kind: str) -> list[str]:
    warnings: list[str] = []
    for item in _string_list(review.get("source_warnings")):
        warnings.append(f"{_source_label(source_kind)}本身可能存在问题：{item}")
    for item in _failed_rubrics(review):
        origin = str(item.get("origin") or "").strip()
        if origin != "source_input":
            continue
        finding = _finding_text(item)
        if finding:
            warnings.append(f"{_source_label(source_kind)}本身可能存在问题：{_rubric_label(item)} - {finding}")
    return _dedupe(warnings)


def _unresolved_warnings_from_review(review: dict[str, Any]) -> list[str]:
    warnings = _string_list(review.get("warnings"))
    for item in _failed_rubrics(review):
        if str(item.get("origin") or "").strip() == "source_input":
            continue
        finding = _finding_text(item)
        if finding:
            warnings.append(f"{_rubric_label(item)} - {finding}")
    return _dedupe(warnings)


def _skill_fingerprint(skill: SkillCard) -> str:
    return json.dumps(
        skill.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _failed_rubrics(review: dict[str, Any]) -> list[dict[str, Any]]:
    results = review.get("rubric_results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict) and not bool(item.get("passed"))]


def _reflection_history_item(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": bool(review.get("passed")),
        "summary": str(review.get("summary") or ""),
        "failed_rubrics": [
            {
                "name": str(item.get("name") or ""),
                "finding": _finding_text(item),
                "origin": str(item.get("origin") or ""),
            }
            for item in _failed_rubrics(review)
        ],
    }


def _source_label(source_kind: str) -> str:
    if source_kind == "rewrite":
        return "原始技能"
    return "原始文档"


def _rubric_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    return RUBRIC_LABELS.get(name, name or "未知 Rubric")


def _finding_text(item: dict[str, Any]) -> str:
    return str(item.get("finding") or item.get("issue") or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _merge_current_warnings(source_warnings: list[str], *current_warnings: str | list[str]) -> list[str]:
    sources = _dedupe(source_warnings)
    merged = list(sources)
    for value in current_warnings:
        values = value if isinstance(value, list) else [value]
        for warning in values:
            text = str(warning).strip()
            if text and not any(_warnings_overlap(text, source) for source in sources):
                merged.append(text)
    return _dedupe(merged)


def _warnings_overlap(left: str, right: str) -> bool:
    left_text = _warning_comparison_text(left)
    right_text = _warning_comparison_text(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 12 and shorter in longer:
        return True
    return SequenceMatcher(None, left_text, right_text).ratio() >= 0.72


def _warning_comparison_text(value: str) -> str:
    text = value.strip()
    for prefix in ("原始文档本身可能存在问题：", "原始技能本身可能存在问题："):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return "".join(character.casefold() for character in text if character.isalnum() or character == "_")


def _emit(status_callback: StatusCallback | None, text: str) -> None:
    if status_callback is not None:
        status_callback(text)


def _status_event(text: str) -> dict[str, object]:
    return {"event": "status", "data": {"text": text}}


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped
