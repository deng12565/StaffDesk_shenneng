from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.channels.crypto import decrypt_channel_secret
from app.config import get_settings
from app.db.models import (
    ChannelBinding,
    ChannelDelivery,
    EmailInboxBinding,
    MailboxCheckpoint,
    ModelConfig,
    RecruitingApplication,
    RecruitingAttachmentArtifact,
    RecruitingDigestBatch,
    RecruitingDigestConfig,
    RecruitingEvaluation,
    RecruitingRoleAlias,
    RecruitingRoleProfile,
    ScheduledTask,
    ScheduledTaskRun,
    utc_now,
)
from app.recruiting.analysis import (
    EVALUATION_PROMPT_VERSION,
    evaluate_candidate,
    extract_evidence,
    get_or_create_role_profile,
    normalized_role_for_title,
)
from app.recruiting.artifacts import (
    ARCHIVE_FORMATS,
    ArtifactError,
    ArtifactStore,
    convert_word_document,
    detect_format,
    extract_archive_entries,
    extract_text,
    probe_document_capabilities,
)
from app.recruiting.domain import (
    calculate_score,
    evaluation_stage,
    rank_evaluations,
    resolve_applied_job,
)
from app.recruiting.mailbox import MailboxError, ParsedMail, ReadOnlyIMAPClient, parse_mail
from app.recruiting.reporting import build_report


_CANDIDATE_TERMS = re.compile(r"(应聘|申请.{0,8}(?:岗位|职位)|投递.{0,8}(?:简历|岗位)|求职|自荐)")
_NON_CANDIDATE_TERMS = re.compile(r"(退信|投递失败|系统通知|广告|账单|验证码)")


def execute_recruiting_digest(
    db: Session,
    task: ScheduledTask,
    run: ScheduledTaskRun,
    *,
    manual: bool = False,
    mailbox_factory=ReadOnlyIMAPClient,
) -> str:
    config_id = str((task.execution_config_json or {}).get("digest_config_id") or "")
    config = db.get(RecruitingDigestConfig, config_id)
    if not config or config.tenant_id != task.tenant_id:
        raise RuntimeError("RECRUITING_CONFIG_NOT_FOUND")
    if config.status != "active" and not manual:
        raise RuntimeError("RECRUITING_CONFIG_DISABLED")
    batch = _get_or_create_batch(db, config, run)
    if batch.status in {"waiting_delivery", "delivered", "no_new", "partial_success"} and batch.message_text:
        return f"招聘日报批次 {batch.id} 已生成"
    if not manual and _past_misfire_deadline(config, run.scheduled_for):
        return _finish_missed_batch(db, config, batch)
    inbox = _require_inbox(db, config)
    model = _require_model(db, config)
    try:
        with mailbox_factory(
            inbox.imap_host,
            inbox.imap_port,
            inbox.email_address,
            decrypt_channel_secret(inbox.credentials_enc or ""),
            inbox.mailbox_name,
        ) as mailbox:
            uid_validity = mailbox.uid_validity()
            highest_uid = mailbox.highest_uid()
            checkpoint = _checkpoint(db, inbox)
            if checkpoint is None:
                checkpoint = MailboxCheckpoint(
                    tenant_id=inbox.tenant_id,
                    binding_id=inbox.id,
                    mailbox_name=inbox.mailbox_name,
                    uid_validity=uid_validity,
                    highest_processed_uid=highest_uid,
                    baseline_at=utc_now(),
                    last_success_at=utc_now(),
                )
                db.add(checkpoint)
                db.commit()
                return _finish_batch(db, config, batch, [], [], new_email_count=0, baseline=True)
            if checkpoint.uid_validity != uid_validity:
                inbox.status = "error"
                inbox.last_error_code = "UIDVALIDITY_CHANGED"
                db.add(inbox)
                db.commit()
                raise RuntimeError("UIDVALIDITY_CHANGED")
            batch.window_started_at = checkpoint.last_success_at
            batch.snapshot_uid_start = checkpoint.highest_processed_uid + 1
            batch.snapshot_uid_end = highest_uid
            batch.status = "syncing"
            db.add(batch)
            db.commit()
            uids = mailbox.uids_between(batch.snapshot_uid_start, highest_uid)
            if uids and not _model_privacy_gate(config, model):
                raise RuntimeError("MODEL_PRIVACY_UNVERIFIED")
            for uid in uids:
                _receive_and_process_mail(db, config, batch, inbox, checkpoint, uid_validity, uid, mailbox, model)
            checkpoint.last_success_at = utc_now()
            checkpoint.updated_at = utc_now()
            db.add(checkpoint)
            db.commit()
    except MailboxError as exc:
        inbox.status = "auth_required" if exc.code == "AUTH_REQUIRED" else "error"
        inbox.last_error_code = exc.code
        inbox.updated_at = utc_now()
        db.add(inbox)
        db.commit()
        raise RuntimeError(exc.code) from exc
    evaluations = _batch_evaluation_rows(db, batch.id)
    ranked = rank_evaluations(evaluations)
    for item in ranked:
        evaluation = db.get(RecruitingEvaluation, item["evaluation_id"])
        if evaluation:
            evaluation.batch_rank = int(item["batch_rank"])
            db.add(evaluation)
    review = _batch_review_rows(db, batch.id)
    db.commit()
    return _finish_batch(db, config, batch, ranked, review, new_email_count=batch.new_email_count)


def _receive_and_process_mail(
    db: Session,
    config: RecruitingDigestConfig,
    batch: RecruitingDigestBatch,
    inbox: EmailInboxBinding,
    checkpoint: MailboxCheckpoint,
    uid_validity: str,
    uid: int,
    mailbox: ReadOnlyIMAPClient,
    model: ModelConfig,
) -> None:
    existing = db.exec(
        select(RecruitingApplication).where(
            RecruitingApplication.inbox_binding_id == inbox.id,
            RecruitingApplication.uid_validity == uid_validity,
            RecruitingApplication.mail_uid == uid,
        )
    ).first()
    if existing:
        checkpoint.highest_processed_uid = max(checkpoint.highest_processed_uid, uid)
        checkpoint.updated_at = utc_now()
        db.add(checkpoint)
        db.commit()
        return
    raw = mailbox.fetch_peek(uid)
    now = utc_now()
    settings = get_settings()
    application = RecruitingApplication(
        tenant_id=inbox.tenant_id,
        batch_id=batch.id,
        inbox_binding_id=inbox.id,
        uid_validity=uid_validity,
        mail_uid=uid,
        message_sha256="pending",
        raw_expires_at=now + timedelta(days=config.raw_retention_days),
        result_expires_at=now + timedelta(days=config.result_retention_days),
    )
    try:
        parsed = parse_mail(
            raw,
            max_message_bytes=settings.recruiting_max_email_bytes,
            max_attachment_bytes=settings.recruiting_max_attachment_bytes,
        )
        application.message_sha256 = parsed.sha256
        application.message_id_normalized = parsed.message_id
        application.sender_display = parsed.sender[:300]
        application.received_at = _naive_utc(parsed.received_at) or now
        application.attachment_sha256_json = [item.sha256 for item in parsed.attachments]
        application.encrypted_source_ref = ArtifactStore().put(inbox.tenant_id, application.id, raw)
        duplicate = _duplicate_application(db, application)
        if duplicate:
            application.status = "unsupported"
            application.error_code = "DUPLICATE_APPLICATION"
            application.evidence_json = {"duplicate_of": duplicate.id}
        else:
            application.status = "parsing"
    except (MailboxError, ArtifactError) as exc:
        application.status = "needs_review"
        application.error_code = exc.code
        parsed = None
    db.add(application)
    db.commit()
    db.refresh(application)
    checkpoint.highest_processed_uid = uid
    checkpoint.updated_at = utc_now()
    db.add(checkpoint)
    batch.new_email_count += 1
    db.add(batch)
    db.commit()
    if parsed is None or application.error_code == "DUPLICATE_APPLICATION":
        return
    _process_application(db, application, parsed, model)


def _process_application(
    db: Session,
    application: RecruitingApplication,
    parsed: ParsedMail,
    model: ModelConfig,
) -> None:
    classification = _candidate_classification(parsed)
    if classification != "candidate":
        application.status = "unsupported" if classification == "non_candidate" else "needs_review"
        application.error_code = "NOT_CANDIDATE" if classification == "non_candidate" else "PARSE_FAILED"
        application.updated_at = utc_now()
        db.add(application)
        db.commit()
        return
    documents: list[dict[str, str]] = []
    errors: list[str] = []
    if parsed.body_text:
        documents.append({"source": "message.body", "text": parsed.body_text})
    for attachment in parsed.attachments:
        try:
            documents.extend(_attachment_documents(db, application, attachment.filename, attachment.content_type, attachment.content))
        except ArtifactError as exc:
            errors.append(exc.code)
    if not documents:
        application.status = "needs_review"
        application.error_code = errors[0] if errors else "PARSE_FAILED"
        application.warnings_json = errors
        db.add(application)
        db.commit()
        return
    try:
        evidence = extract_evidence(
            model,
            application,
            subject=parsed.subject,
            email_body=parsed.body_text,
            documents=documents,
        )
        application.candidate_display_name = evidence.candidate_display_name
        application.extracted_text = evidence.basic_experience
        application.evidence_json = evidence.model_dump(mode="json")
        application.job_title_candidates_json = [
            item.model_dump(mode="json") for item in evidence.job_title_candidates
        ]
        application.full_time_experience_months = evidence.full_time_experience_months
        resolution = resolve_applied_job(evidence.job_title_candidates)
        application.job_resolution_status = resolution.status
        application.warnings_json = [*errors, *resolution.warnings, *evidence.extraction_warnings]
        if resolution.status != "resolved" or not resolution.raw_title or not resolution.source:
            raise RuntimeError(resolution.status)
        normalized = normalized_role_for_title(resolution.raw_title)
        application.applied_job_title_raw = resolution.raw_title
        application.applied_job_title_source = resolution.source
        application.normalized_function_name = normalized.normalized_function_name
        application.specialization = normalized.specialization
        application.explicit_level = normalized.explicit_level
        application.standard_role_key = normalized.standard_role_key
        application.normalization_method = normalized.method
        application.normalization_confidence = normalized.confidence
        application.normalization_version = "1"
        profile = get_or_create_role_profile(
            db,
            application.tenant_id,
            normalized,
            resolution.raw_title,
            resolution.source,
            model,
        )
        stage, warning = evaluation_stage(
            normalized.explicit_level,
            evidence.full_time_experience_months,
        )
        if warning:
            application.warnings_json = [*application.warnings_json, warning]
        payload = evaluate_candidate(model, application, profile, evidence, stage)
        score = calculate_score(payload, profile.core_capabilities_json)
        evaluation = RecruitingEvaluation(
            tenant_id=application.tenant_id,
            application_id=application.id,
            role_profile_id=profile.id,
            role_profile_version=profile.version,
            evaluation_stage=stage,
            weights_json=score.weights,
            dimension_scores_json=payload.dimension_scores,
            match_evidence_json=payload.match_evidence,
            core_capability_assessments_json=[
                item.model_dump(mode="json") for item in payload.core_capability_assessments
            ],
            critical_gaps_json=list(score.critical_gaps),
            major_experience_subtotal=score.major_experience_subtotal,
            recommendation_index=score.recommendation_index,
            summary=evidence.basic_experience,
            recommendation_reasons_json=payload.recommendation_reasons,
            risks_json=payload.risks,
            missing_information_json=payload.missing_information,
            model_version=model.model,
            model_protocol=model.api_protocol,
            prompt_version=EVALUATION_PROMPT_VERSION,
        )
        application.status = "analyzed"
        application.error_code = None
        db.add(evaluation)
    except RuntimeError as exc:
        code = str(exc)
        application.status = "needs_review"
        application.error_code = code if code.isupper() else "MODEL_FAILED"
    application.updated_at = utc_now()
    db.add(application)
    db.commit()


def _attachment_documents(
    db: Session,
    application: RecruitingApplication,
    filename: str,
    content_type: str,
    content: bytes,
    *,
    parent_artifact_id: str | None = None,
    depth: int = 0,
    processing_type: str | None = None,
) -> list[dict[str, str]]:
    file_format = detect_format(filename, content_type, content)
    store = ArtifactStore()
    artifact = RecruitingAttachmentArtifact(
        tenant_id=application.tenant_id,
        application_id=application.id,
        parent_artifact_id=parent_artifact_id,
        original_filename=filename if parent_artifact_id is None else None,
        archive_entry_name=filename if parent_artifact_id else None,
        detected_format=file_format,
        nesting_depth=depth,
        source_sha256=_sha256(content),
        processing_type=processing_type or ("extracted" if parent_artifact_id else "original"),
        status="processing",
        encrypted_file_ref=store.put(application.tenant_id, application.id, content),
        expires_at=application.raw_expires_at,
    )
    db.add(artifact)
    db.flush()
    documents: list[dict[str, str]] = []
    try:
        if file_format in ARCHIVE_FORMATS:
            artifact.processor_name = "7-Zip"
            artifact.processor_version = str(
                probe_document_capabilities()["seven_zip"].get("version") or "unknown"
            )[:120]
            entries = extract_archive_entries(content, filename, depth=depth)
            for entry_name, entry_content in entries:
                documents.extend(
                    _attachment_documents(
                        db,
                        application,
                        entry_name,
                        "application/octet-stream",
                        entry_content,
                        parent_artifact_id=artifact.id,
                        depth=depth + 1,
                    )
                )
        elif file_format in {"doc", "docm"}:
            started = time.monotonic()
            converted = convert_word_document(content, filename)
            artifact.derived_sha256 = _sha256(converted)
            artifact.processor_name = "Microsoft Word"
            artifact.processor_version = str(
                probe_document_capabilities()["word"].get("version") or "unknown"
            )[:120]
            artifact.processing_duration_ms = round((time.monotonic() - started) * 1000)
            converted_name = f"{Path(filename).stem}.docx"
            documents.extend(
                _attachment_documents(
                    db,
                    application,
                    converted_name,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    converted,
                    parent_artifact_id=artifact.id,
                    depth=depth,
                    processing_type="converted",
                )
            )
        else:
            text = extract_text(file_format, content, filename=filename)
            if text:
                documents.append({"source": filename, "text": text})
        artifact.status = "processed"
    except ArtifactError as exc:
        artifact.status = "failed"
        artifact.error_code = exc.code
        db.add(artifact)
        db.commit()
        raise
    artifact.updated_at = utc_now()
    db.add(artifact)
    db.commit()
    return documents


def _finish_batch(
    db: Session,
    config: RecruitingDigestConfig,
    batch: RecruitingDigestBatch,
    ranked: list[dict[str, Any]],
    review: list[dict[str, Any]],
    *,
    new_email_count: int,
    baseline: bool = False,
) -> str:
    timezone = ZoneInfo(config.timezone)
    now = utc_now()
    local_now = now.replace(tzinfo=UTC).astimezone(timezone)
    report, text = build_report(
        batch.id,
        local_now.date().isoformat(),
        f"{batch.window_started_at.isoformat() if batch.window_started_at else '首次基线'} - {config.snapshot_time}",
        ranked,
        review,
        new_email_count=new_email_count,
    )
    batch.scored_count = len(ranked)
    batch.review_count = len(review)
    batch.failed_count = sum(1 for item in review if item.get("status") == "failed")
    batch.full_report_json = report
    batch.message_text = text
    batch.completed_at = now
    batch.status = "no_new" if new_email_count == 0 else ("partial_success" if review else "waiting_delivery")
    delivery_at = _delivery_time(local_now, config.earliest_delivery_time)
    batch.scheduled_delivery_at = delivery_at
    db.add(batch)
    db.commit()
    _stage_digest_delivery(db, config, batch, delivery_at)
    return f"招聘日报批次 {batch.id} 已生成" + ("（首次基线）" if baseline else "")


def _finish_missed_batch(
    db: Session,
    config: RecruitingDigestConfig,
    batch: RecruitingDigestBatch,
) -> str:
    now = utc_now()
    local_now = now.replace(tzinfo=UTC).astimezone(ZoneInfo(config.timezone))
    batch.status = "missed"
    batch.error_code = "MISFIRE_DEADLINE_EXCEEDED"
    batch.completed_at = now
    batch.scheduled_delivery_at = now
    batch.full_report_json = {
        "batch_id": batch.id,
        "report_date": local_now.date().isoformat(),
        "status": "missed",
        "error_code": batch.error_code,
    }
    batch.message_text = (
        f"招聘 HR 日报异常｜{local_now.date().isoformat()}\n"
        "StaffDeck 在 10:00 补跑截止后恢复，本次未读取邮箱、未推进检查点。"
        "未处理邮件将进入下一次成功批次。"
    )
    db.add(batch)
    db.commit()
    _stage_digest_delivery(db, config, batch, now)
    return f"招聘日报批次 {batch.id} 已标记 missed"


def stage_recruiting_failure_digest(
    db: Session,
    task: ScheduledTask,
    run: ScheduledTaskRun,
    error_code: str,
) -> None:
    config_id = str((task.execution_config_json or {}).get("digest_config_id") or "")
    config = db.get(RecruitingDigestConfig, config_id)
    if not config:
        return
    batch = _get_or_create_batch(db, config, run)
    if batch.delivery_id or batch.status == "delivered":
        return
    code = error_code.strip()[:100] or "RECRUITING_DIGEST_FAILED"
    batch.status = "failed"
    batch.error_code = code
    batch.completed_at = utc_now()
    batch.scheduled_delivery_at = utc_now()
    batch.full_report_json = {"batch_id": batch.id, "status": "failed", "error_code": code}
    batch.message_text = (
        "招聘 HR 日报异常\n"
        f"错误代码：{code}\n"
        "本次未生成候选人排名；请管理员在 StaffDeck 招聘日报页面检查配置和运行状态。"
    )
    db.add(batch)
    db.commit()
    try:
        _stage_digest_delivery(db, config, batch, utc_now())
    except RuntimeError:
        batch.status = "delivery_failed"
        db.add(batch)
        db.commit()


def _stage_digest_delivery(
    db: Session,
    config: RecruitingDigestConfig,
    batch: RecruitingDigestBatch,
    delivery_at: datetime,
) -> None:
    binding = db.get(ChannelBinding, config.feishu_binding_id)
    if not binding or binding.tenant_id != config.tenant_id or binding.channel != "feishu" or binding.status != "active":
        raise RuntimeError("DELIVERY_FAILED")
    allowlist = {str(item).strip() for item in config.recipient_allowlist_json if str(item).strip()}
    if not config.recipient_open_id or config.recipient_open_id not in allowlist:
        raise RuntimeError("TARGET_NOT_ALLOWLISTED")
    key = f"recruiting-digest:{batch.id}"
    delivery = db.exec(select(ChannelDelivery).where(ChannelDelivery.idempotency_key == key)).first()
    if delivery is None:
        delivery = ChannelDelivery(
            tenant_id=config.tenant_id,
            binding_id=binding.id,
            session_id=f"recruiting:{batch.id}",
            target_json={
                "receive_id": config.recipient_open_id,
                "receive_id_type": "open_id",
                "delivery_mode": "single_text",
            },
            kind="scheduled_digest",
            text=batch.message_text or "",
            status="pending",
            next_attempt_at=delivery_at,
            idempotency_key=key,
        )
        db.add(delivery)
        db.commit()
        db.refresh(delivery)
    batch.delivery_id = delivery.id
    db.add(batch)
    db.commit()


def _get_or_create_batch(
    db: Session,
    config: RecruitingDigestConfig,
    run: ScheduledTaskRun,
) -> RecruitingDigestBatch:
    existing = db.exec(
        select(RecruitingDigestBatch).where(RecruitingDigestBatch.scheduled_task_run_id == run.id)
    ).first()
    if existing:
        return existing
    batch = RecruitingDigestBatch(
        tenant_id=config.tenant_id,
        digest_config_id=config.id,
        scheduled_task_run_id=run.id,
        snapshot_at=utc_now(),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def _require_inbox(db: Session, config: RecruitingDigestConfig) -> EmailInboxBinding:
    inbox = db.get(EmailInboxBinding, config.inbox_binding_id)
    if not inbox or inbox.tenant_id != config.tenant_id or inbox.status == "disabled":
        raise RuntimeError("MAILBOX_NOT_FOUND")
    if not inbox.credentials_enc:
        raise RuntimeError("SECRET_NOT_CONFIGURED")
    return inbox


def _require_model(db: Session, config: RecruitingDigestConfig) -> ModelConfig:
    model = db.get(ModelConfig, config.model_config_id)
    if not model or model.tenant_id != config.tenant_id or not model.enabled or model.trust_status != "verified":
        raise RuntimeError("MODEL_FAILED")
    return model


def _model_privacy_gate(config: RecruitingDigestConfig, model: ModelConfig) -> bool:
    return bool(
        config.model_privacy_verified
        and config.model_privacy_fingerprint
        and config.model_privacy_fingerprint == model.verified_fingerprint
    )


def _checkpoint(db: Session, inbox: EmailInboxBinding) -> MailboxCheckpoint | None:
    return db.exec(
        select(MailboxCheckpoint).where(
            MailboxCheckpoint.binding_id == inbox.id,
            MailboxCheckpoint.mailbox_name == inbox.mailbox_name,
        )
    ).first()


def _candidate_classification(mail: ParsedMail) -> str:
    supported_attachment = any(_has_supported_suffix(item.filename) for item in mail.attachments)
    text = f"{mail.subject}\n{mail.body_text}"
    if supported_attachment or _CANDIDATE_TERMS.search(text):
        return "candidate"
    if _NON_CANDIDATE_TERMS.search(text):
        return "non_candidate"
    return "unknown"


def _has_supported_suffix(filename: str) -> bool:
    return filename.lower().endswith(
        (".pdf", ".doc", ".docm", ".docx", ".jpg", ".jpeg", ".png", ".webp", ".zip", ".rar", ".7z")
    )


def _duplicate_application(db: Session, application: RecruitingApplication) -> RecruitingApplication | None:
    if not application.attachment_sha256_json:
        return None
    candidates = db.exec(
        select(RecruitingApplication)
        .where(
            RecruitingApplication.tenant_id == application.tenant_id,
            RecruitingApplication.id != application.id,
        )
        .order_by(RecruitingApplication.created_at.desc())
        .limit(500)
    ).all()
    signature = sorted(application.attachment_sha256_json)
    return next(
        (item for item in candidates if signature == sorted(item.attachment_sha256_json or [])),
        None,
    )


def _batch_evaluation_rows(db: Session, batch_id: str) -> list[dict[str, Any]]:
    rows = db.exec(
        select(RecruitingEvaluation, RecruitingApplication, RecruitingRoleProfile)
        .join(RecruitingApplication, RecruitingEvaluation.application_id == RecruitingApplication.id)
        .join(RecruitingRoleProfile, RecruitingEvaluation.role_profile_id == RecruitingRoleProfile.id)
        .where(RecruitingApplication.batch_id == batch_id)
    ).all()
    return [
        {
            "evaluation_id": evaluation.id,
            "application_id": application.id,
            "candidate_display_name": application.candidate_display_name,
            "applied_job": profile.display_name,
            "received_at": application.received_at,
            "recommendation_index": evaluation.recommendation_index,
            "major_experience_subtotal": evaluation.major_experience_subtotal,
            "dimension_scores": evaluation.dimension_scores_json,
            "match_evidence": evaluation.match_evidence_json,
            "recommendation_reasons": evaluation.recommendation_reasons_json,
            "risks": evaluation.risks_json,
            "role_profile_id": profile.id,
            "role_profile_version": profile.version,
        }
        for evaluation, application, profile in rows
    ]


def _batch_review_rows(db: Session, batch_id: str) -> list[dict[str, Any]]:
    rows = db.exec(
        select(RecruitingApplication).where(
            RecruitingApplication.batch_id == batch_id,
            RecruitingApplication.status.in_({"needs_review", "failed"}),
        )
    ).all()
    return [
        {
            "application_id": item.id,
            "candidate_display_name": item.candidate_display_name,
            "status": item.status,
            "error_code": item.error_code,
            "warnings": item.warnings_json,
        }
        for item in rows
    ]


def _delivery_time(local_now: datetime, value: str) -> datetime:
    hour, minute = (int(item) for item in value.split(":"))
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    chosen = max(local_now, target)
    return chosen.astimezone(UTC).replace(tzinfo=None)


def _past_misfire_deadline(config: RecruitingDigestConfig, scheduled_for: datetime) -> bool:
    timezone = ZoneInfo(config.timezone)
    now_local = utc_now().replace(tzinfo=UTC).astimezone(timezone)
    scheduled_local = scheduled_for.replace(tzinfo=UTC).astimezone(timezone)
    hour, minute = (int(item) for item in config.misfire_deadline_time.split(":"))
    deadline = scheduled_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now_local > deadline


def _naive_utc(value: object | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def cleanup_expired_recruiting_data(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or utc_now()
    store = ArtifactStore()
    deleted_files = 0
    artifacts = db.exec(
        select(RecruitingAttachmentArtifact).where(
            RecruitingAttachmentArtifact.expires_at.is_not(None),
            RecruitingAttachmentArtifact.expires_at <= now,
            RecruitingAttachmentArtifact.encrypted_file_ref.is_not(None),
        )
    ).all()
    for artifact in artifacts:
        if artifact.encrypted_file_ref and store.delete(artifact.encrypted_file_ref):
            deleted_files += 1
        artifact.encrypted_file_ref = None
        db.add(artifact)
    applications = db.exec(
        select(RecruitingApplication).where(
            RecruitingApplication.raw_expires_at.is_not(None),
            RecruitingApplication.raw_expires_at <= now,
            RecruitingApplication.encrypted_source_ref.is_not(None),
        )
    ).all()
    for application in applications:
        if application.encrypted_source_ref and store.delete(application.encrypted_source_ref):
            deleted_files += 1
        application.encrypted_source_ref = None
        db.add(application)
    deleted_records = 0
    expired_applications = db.exec(
        select(RecruitingApplication).where(
            RecruitingApplication.result_expires_at.is_not(None),
            RecruitingApplication.result_expires_at <= now,
        )
    ).all()
    for application in expired_applications:
        for evaluation in db.exec(
            select(RecruitingEvaluation).where(
                RecruitingEvaluation.application_id == application.id
            )
        ).all():
            db.delete(evaluation)
            deleted_records += 1
        for artifact in db.exec(
            select(RecruitingAttachmentArtifact).where(
                RecruitingAttachmentArtifact.application_id == application.id
            )
        ).all():
            if artifact.encrypted_file_ref and store.delete(artifact.encrypted_file_ref):
                deleted_files += 1
            db.delete(artifact)
            deleted_records += 1
        if application.encrypted_source_ref and store.delete(application.encrypted_source_ref):
            deleted_files += 1
        db.delete(application)
        deleted_records += 1
    db.flush()

    configs = {row.id: row for row in db.exec(select(RecruitingDigestConfig)).all()}
    for batch in db.exec(select(RecruitingDigestBatch)).all():
        config = configs.get(batch.digest_config_id)
        retention_days = config.result_retention_days if config else 90
        completed_at = batch.completed_at or batch.created_at
        if completed_at > now - timedelta(days=retention_days):
            continue
        remaining = db.exec(
            select(RecruitingApplication.id)
            .where(RecruitingApplication.batch_id == batch.id)
            .limit(1)
        ).first()
        if remaining:
            continue
        if batch.delivery_id:
            delivery = db.get(ChannelDelivery, batch.delivery_id)
            if delivery and delivery.kind == "scheduled_digest":
                db.delete(delivery)
                deleted_records += 1
        db.delete(batch)
        deleted_records += 1
    db.flush()

    profiles = db.exec(
        select(RecruitingRoleProfile).where(RecruitingRoleProfile.is_current == False)  # noqa: E712
    ).all()
    for profile in profiles:
        referenced = db.exec(
            select(RecruitingEvaluation.id)
            .where(RecruitingEvaluation.role_profile_id == profile.id)
            .limit(1)
        ).first()
        if referenced:
            continue
        for alias in db.exec(
            select(RecruitingRoleAlias).where(RecruitingRoleAlias.role_profile_id == profile.id)
        ).all():
            db.delete(alias)
            deleted_records += 1
        db.delete(profile)
        deleted_records += 1
    db.commit()
    return {"deleted_files": deleted_files, "deleted_records": deleted_records}
