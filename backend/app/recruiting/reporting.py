from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any


FEISHU_SAFE_BUDGET_BYTES = 120 * 1024


def build_report(
    batch_id: str,
    report_date: str,
    window_label: str,
    ranked: list[dict[str, Any]],
    review: list[dict[str, Any]],
    *,
    new_email_count: int,
) -> tuple[dict[str, Any], str]:
    report = {
        "batch_id": batch_id,
        "report_date": report_date,
        "window": window_label,
        "new_email_count": new_email_count,
        "scored_count": len(ranked),
        "review_count": len(review),
        "ranked_candidates": ranked,
        "needs_review": review,
        "generated_at": datetime.utcnow().isoformat(),
        "disclaimer": (
            "推荐指数基于报名岗位名称生成的通用行业岗位画像，仅用于人工复核优先级，"
            "不代表公司 JD 匹配度或录用概率。"
        ),
    }
    return report, render_feishu_digest(report)


def render_feishu_digest(report: dict[str, Any]) -> str:
    ranked = list(report.get("ranked_candidates") or [])
    review = list(report.get("needs_review") or [])
    modes = ("full", "compact", "minimal")
    for mode in modes:
        text = _render(report, ranked, review, mode=mode)
        if _serialized_text_bytes(text) <= FEISHU_SAFE_BUDGET_BYTES:
            return text
    included: list[dict[str, Any]] = []
    for candidate in ranked:
        candidate_text = _render(report, [*included, candidate], review, mode="minimal")
        if _serialized_text_bytes(candidate_text) > FEISHU_SAFE_BUDGET_BYTES:
            break
        included.append(candidate)
    omitted = len(ranked) - len(included)
    return _render(report, included, review, mode="minimal", omitted=omitted)


def _render(
    report: dict[str, Any],
    ranked: list[dict[str, Any]],
    review: list[dict[str, Any]],
    *,
    mode: str,
    omitted: int = 0,
) -> str:
    lines = [
        f"招聘 HR 日报｜{report['report_date']}",
        f"统计窗口：{report['window']}",
        (
            f"新增邮件：{report['new_email_count']}｜可评分候选人："
            f"{report['scored_count']}｜待确认：{report['review_count']}"
        ),
        "",
    ]
    if not ranked and not review:
        lines.append("今日无新增候选人投递。")
    for candidate in ranked:
        name = _safe_text(candidate.get("candidate_display_name") or "候选人记录", 80)
        role = _safe_text(candidate.get("applied_job") or "-", 120)
        index = float(candidate.get("recommendation_index") or 0)
        lines.append(f"{candidate['batch_rank']}. {name}｜报名岗位：{role}｜推荐指数：{index:.1f}/10")
        if mode == "full":
            evidence = candidate.get("match_evidence") or {}
            lines.extend(
                [
                    f"相关工作经历：{_join(evidence.get('relevant_work_experience'), 3)}",
                    f"相关项目经验：{_join(evidence.get('relevant_project_experience'), 3)}",
                    f"相关实习经历：{_join(evidence.get('relevant_internship_experience'), 3)}",
                    f"推荐理由：{_join(candidate.get('recommendation_reasons'), 3)}",
                    f"主要风险/缺口：{_join(candidate.get('risks'), 3)}",
                ]
            )
        elif mode == "compact":
            lines.append(f"推荐理由：{_join(candidate.get('recommendation_reasons'), 1)}")
            lines.append(f"主要风险/缺口：{_join(candidate.get('risks'), 1)}")
        else:
            lines.append(_join(candidate.get("recommendation_reasons"), 1))
        lines.append("")
    if omitted:
        lines.append(f"另有 {omitted} 名候选人因飞书消息容量限制未在消息中展开，请查看完整报告。")
        lines.append("")
    if review:
        lines.append("待人工确认：")
        for item in review:
            lines.append(
                f"- {_safe_text(item.get('candidate_display_name') or item.get('application_id'), 80)}"
                f"｜{_safe_text(item.get('error_code') or 'NEEDS_REVIEW', 80)}｜未生成推荐指数"
            )
        lines.append("")
    lines.append(f"完整报告：http://127.0.0.1:5173/recruiting/digests/{report['batch_id']}")
    lines.append(f"说明：{report['disclaimer']}")
    return "\n".join(lines).strip()


def _join(values: object, limit: int) -> str:
    items = list(values) if isinstance(values, list) else []
    clean = [_safe_text(item, 240) for item in items[:limit] if str(item).strip()]
    return "；".join(clean) or "未知"


def _safe_text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", text)
    text = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[电话已隐藏]", text)
    return text[:limit]


def _serialized_text_bytes(text: str) -> int:
    body = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
