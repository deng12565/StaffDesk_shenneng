from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


JOB_SOURCES = ("resume", "email_subject", "email_body")
DIMENSIONS = (
    "relevant_work_experience",
    "relevant_project_experience",
    "relevant_internship_experience",
    "other_supporting_evidence",
)
STAGE_WEIGHTS: dict[str, dict[str, Decimal]] = {
    "campus": {
        "relevant_work_experience": Decimal("0.10"),
        "relevant_project_experience": Decimal("0.40"),
        "relevant_internship_experience": Decimal("0.40"),
        "other_supporting_evidence": Decimal("0.10"),
    },
    "junior": {
        "relevant_work_experience": Decimal("0.40"),
        "relevant_project_experience": Decimal("0.35"),
        "relevant_internship_experience": Decimal("0.15"),
        "other_supporting_evidence": Decimal("0.10"),
    },
    "experienced": {
        "relevant_work_experience": Decimal("0.55"),
        "relevant_project_experience": Decimal("0.35"),
        "relevant_internship_experience": Decimal("0.00"),
        "other_supporting_evidence": Decimal("0.10"),
    },
}


class JobTitleCandidate(BaseModel):
    raw_title: str = Field(min_length=1, max_length=160)
    source: Literal["resume", "email_subject", "email_body"]
    evidence_ref: str = Field(min_length=1, max_length=500)

    @field_validator("raw_title", "evidence_ref")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CapabilityAssessment(BaseModel):
    capability_id: str
    status: Literal["strong", "supported", "partial", "missing_evidence", "contradicted"]
    evidence: list[str] = Field(default_factory=list)


class EvaluationPayload(BaseModel):
    candidate_record_id: str
    role_profile_id: str
    role_profile_version: int = Field(ge=1)
    evaluation_stage: Literal["campus", "junior", "experienced"]
    dimension_scores: dict[str, float]
    match_evidence: dict[str, list[str]]
    core_capability_assessments: list[CapabilityAssessment]
    recommendation_reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "EvaluationPayload":
        expected = set(DIMENSIONS)
        if set(self.dimension_scores) != expected or set(self.match_evidence) != expected:
            raise ValueError("all four scoring dimensions and evidence fields are required")
        if any(float(value) < 0 or float(value) > 10 for value in self.dimension_scores.values()):
            raise ValueError("dimension scores must be between 0 and 10")
        return self


@dataclass(frozen=True)
class ResolvedRole:
    status: str
    raw_title: str | None = None
    source: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedRole:
    normalized_function_name: str
    specialization: str
    explicit_level: str
    standard_role_key: str
    confidence: float
    method: str = "deterministic_v1"


@dataclass(frozen=True)
class ScoreResult:
    weights: dict[str, float]
    major_experience_subtotal: float
    recommendation_index: float
    critical_gaps: tuple[str, ...]


_LEVEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("campus", ("实习", "校招", "应届", "intern")),
    ("junior", ("初级", "助理", "junior")),
    ("experienced", ("高级", "资深", "专家", "负责人", "经理", "总监", "senior", "lead")),
)
_ROLE_SUFFIXES = (
    "开发工程师",
    "研发工程师",
    "工程师",
    "开发",
    "专员",
    "岗位",
    "职位",
)


def normalize_role_title(raw_title: str) -> NormalizedRole | None:
    normalized = unicodedata.normalize("NFKC", raw_title or "").strip().lower()
    normalized = re.sub(r"[\s\-_—/]+", " ", normalized)
    normalized = re.sub(r"[（）()]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ,，。:：")
    if not normalized:
        return None
    level = "unspecified"
    for candidate_level, words in _LEVEL_PATTERNS:
        if any(word in normalized for word in words):
            level = candidate_level
            for word in words:
                normalized = normalized.replace(word, " ")
            break
    normalized = re.sub(r"\s+", " ", normalized).strip()
    specialization = ""
    function_name = normalized
    if "java" in normalized and ("后端" in normalized or "服务端" in normalized):
        function_name, specialization = "后端开发工程师", "java"
    elif "后端" in normalized or "服务端" in normalized:
        function_name = "后端开发工程师"
    elif "前端" in normalized:
        function_name = "前端开发工程师"
    elif "产品经理" in normalized:
        function_name = "产品经理"
    elif "项目经理" in normalized:
        function_name = "项目经理"
    else:
        function_name = normalized
        for suffix in _ROLE_SUFFIXES:
            if function_name.endswith(suffix) and len(function_name) > len(suffix):
                function_name = f"{function_name.removesuffix(suffix).strip()}{suffix}"
                break
    confidence = 0.95 if len(function_name) >= 2 else 0.0
    if confidence < 0.90:
        return None
    key = "|".join((function_name, specialization or "_", level))
    return NormalizedRole(function_name, specialization, level, key, confidence)


def resolve_applied_job(candidates: list[JobTitleCandidate]) -> ResolvedRole:
    grouped: dict[str, list[tuple[JobTitleCandidate, NormalizedRole]]] = {
        source: [] for source in JOB_SOURCES
    }
    for item in candidates:
        normalized = normalize_role_title(item.raw_title)
        if normalized:
            grouped[item.source].append((item, normalized))

    resume = _unique_roles(grouped["resume"])
    email = _unique_roles(grouped["email_subject"] + grouped["email_body"])
    if len(resume) > 1:
        return ResolvedRole("JOB_TITLE_AMBIGUOUS")
    if len(resume) == 1:
        item, role = next(iter(resume.values()))
        warnings = ("JOB_TITLE_CONFLICT",) if email and any(key != role.standard_role_key for key in email) else ()
        return ResolvedRole("resolved", item.raw_title, "resume", warnings)
    if len(email) > 1:
        return ResolvedRole("JOB_TITLE_AMBIGUOUS")
    if len(email) == 1:
        item, _role = next(iter(email.values()))
        return ResolvedRole("resolved", item.raw_title, item.source)
    return ResolvedRole("JOB_TITLE_MISSING")


def _unique_roles(
    values: list[tuple[JobTitleCandidate, NormalizedRole]],
) -> dict[str, tuple[JobTitleCandidate, NormalizedRole]]:
    result: dict[str, tuple[JobTitleCandidate, NormalizedRole]] = {}
    for item, role in values:
        result.setdefault(role.standard_role_key, (item, role))
    return result


def evaluation_stage(explicit_level: str | None, full_time_months: int | None) -> tuple[str, str | None]:
    if explicit_level in STAGE_WEIGHTS:
        return str(explicit_level), None
    if full_time_months is None:
        return "junior", "FULL_TIME_EXPERIENCE_UNKNOWN"
    if full_time_months < 12:
        return "campus", None
    if full_time_months < 36:
        return "junior", None
    return "experienced", None


def calculate_score(
    payload: EvaluationPayload,
    core_capabilities: list[dict[str, Any]],
) -> ScoreResult:
    expected_ids = [str(item.get("id") or item.get("capability_id") or "") for item in core_capabilities]
    if not expected_ids or any(not item for item in expected_ids) or len(expected_ids) != len(set(expected_ids)):
        raise ValueError("role profile core capabilities must have unique stable IDs")
    actual_ids = [item.capability_id for item in payload.core_capability_assessments]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
        raise ValueError("capability assessments must cover every role capability exactly once")
    assessments = {item.capability_id: item for item in payload.core_capability_assessments}
    critical_gaps = tuple(
        capability_id
        for capability_id, source in zip(expected_ids, core_capabilities, strict=True)
        if bool(source.get("is_critical"))
        and assessments[capability_id].status in {"missing_evidence", "contradicted"}
    )
    weights = STAGE_WEIGHTS[payload.evaluation_stage]
    values = {key: Decimal(str(payload.dimension_scores[key])) for key in DIMENSIONS}
    major = sum(values[key] * weights[key] for key in DIMENSIONS[:3])
    total = major + values[DIMENSIONS[3]] * weights[DIMENSIONS[3]]
    rounded = total.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    traceable = {
        evidence.strip()
        for key in DIMENSIONS[:3]
        for evidence in payload.match_evidence[key]
        if evidence.strip()
    }
    if rounded >= Decimal("9.0") and (critical_gaps or len(traceable) < 2):
        rounded = Decimal("8.9")
    return ScoreResult(
        weights={key: float(value) for key, value in weights.items()},
        major_experience_subtotal=float(major),
        recommendation_index=float(rounded),
        critical_gaps=critical_gaps,
    )


def rank_evaluations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -float(row["recommendation_index"]),
            -float(row["major_experience_subtotal"]),
            _datetime_key(row.get("received_at")),
            str(row["application_id"]),
        ),
    )
    return [{**row, "batch_rank": index} for index, row in enumerate(ranked, start=1)]


def _datetime_key(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
