from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.channels.crypto import decrypt_channel_secret, encrypt_channel_secret
from app.db import get_session
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelDelivery,
    EmailInboxBinding,
    ModelConfig,
    RecruitingApplication,
    RecruitingAttachmentArtifact,
    RecruitingDigestBatch,
    RecruitingDigestConfig,
    RecruitingEvaluation,
    RecruitingRoleAlias,
    RecruitingRoleProfile,
    ScheduledTask,
    User,
    utc_now,
)
from app.recruiting.analysis import get_or_create_role_profile
from app.recruiting.artifacts import probe_document_capabilities
from app.recruiting.domain import NormalizedRole
from app.recruiting.mailbox import MailboxError, ReadOnlyIMAPClient
from app.scheduled_tasks.service import compute_next_run_at
from app.security.auth import get_current_user
from app.security.permissions import ensure_tenant_admin
from app.security.tenant import ensure_tenant


router = APIRouter(prefix="/api/enterprise", tags=["enterprise:recruiting"])


class EmailInboxCreate(BaseModel):
    tenant_id: str
    email_address: str = "hr@dlang.ai"
    imap_host: str = "imap.feishu.cn"
    imap_port: int = 993
    mailbox_name: str = "INBOX"


class InboxCredentialsRequest(BaseModel):
    tenant_id: str
    password: str = Field(min_length=1, max_length=1000)


class RecruitingDigestConfigCreate(BaseModel):
    tenant_id: str
    agent_id: str
    inbox_binding_id: str
    model_config_id: str
    feishu_binding_id: str
    recipient_open_id: str = Field(min_length=1, max_length=200)
    timezone: str = "Asia/Shanghai"
    snapshot_time: str = "07:00"
    earliest_delivery_time: str = "08:00"
    misfire_deadline_time: str = "10:00"
    raw_retention_days: int = Field(default=7, ge=1, le=90)
    result_retention_days: int = Field(default=90, ge=7, le=365)
    status: Literal["active", "disabled"] = "disabled"


class RecruitingDigestConfigPatch(BaseModel):
    tenant_id: str
    agent_id: str | None = None
    inbox_binding_id: str | None = None
    model_config_id: str | None = None
    feishu_binding_id: str | None = None
    recipient_open_id: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    snapshot_time: str | None = None
    earliest_delivery_time: str | None = None
    misfire_deadline_time: str | None = None
    raw_retention_days: int | None = Field(default=None, ge=1, le=90)
    result_retention_days: int | None = Field(default=None, ge=7, le=365)
    model_privacy_verified: bool | None = None
    status: Literal["active", "disabled"] | None = None


class TenantRequest(BaseModel):
    tenant_id: str


class RegenerateRoleProfileRequest(BaseModel):
    tenant_id: str
    model_config_id: str


@router.post("/email-inboxes")
def create_email_inbox(
    request: EmailInboxCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    if (
        request.email_address.strip().lower() != "hr@dlang.ai"
        or request.imap_host.strip().lower() != "imap.feishu.cn"
        or request.imap_port != 993
        or request.mailbox_name.strip().upper() != "INBOX"
    ):
        raise HTTPException(status_code=422, detail="v1 仅支持固定飞书企业邮箱 INBOX")
    existing = db.exec(
        select(EmailInboxBinding).where(
            EmailInboxBinding.tenant_id == request.tenant_id,
            EmailInboxBinding.email_address == "hr@dlang.ai",
            EmailInboxBinding.mailbox_name == "INBOX",
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="邮箱绑定已存在")
    row = EmailInboxBinding(
        tenant_id=request.tenant_id,
        email_address="hr@dlang.ai",
        imap_host="imap.feishu.cn",
        imap_port=993,
        mailbox_name="INBOX",
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _inbox_read(row)


@router.get("/email-inboxes")
def list_email_inboxes(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _admin(db, tenant_id, current_user)
    return [
        _inbox_read(row)
        for row in db.exec(
            select(EmailInboxBinding)
            .where(EmailInboxBinding.tenant_id == tenant_id)
            .order_by(EmailInboxBinding.created_at.desc())
        ).all()
    ]


@router.get("/email-inboxes/{inbox_id}")
def get_email_inbox(
    inbox_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, tenant_id, current_user)
    return _inbox_read(_inbox(db, tenant_id, inbox_id))


@router.put("/email-inboxes/{inbox_id}/credentials")
def set_email_inbox_credentials(
    inbox_id: str,
    request: InboxCredentialsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    row = _inbox(db, request.tenant_id, inbox_id)
    row.credentials_enc = encrypt_channel_secret(request.password)
    row.status = "pending"
    row.last_error_code = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _inbox_read(row)


@router.post("/email-inboxes/{inbox_id}/test")
def test_email_inbox(
    inbox_id: str,
    request: TenantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    row = _inbox(db, request.tenant_id, inbox_id)
    if not row.credentials_enc:
        raise HTTPException(status_code=400, detail="SECRET_NOT_CONFIGURED")
    try:
        with ReadOnlyIMAPClient(
            row.imap_host,
            row.imap_port,
            row.email_address,
            decrypt_channel_secret(row.credentials_enc),
            row.mailbox_name,
        ) as mailbox:
            uid_validity = mailbox.uid_validity()
            highest_uid = mailbox.highest_uid()
    except MailboxError as exc:
        row.status = "auth_required" if exc.code == "AUTH_REQUIRED" else "error"
        row.last_error_code = exc.code
        row.last_tested_at = utc_now()
        row.updated_at = utc_now()
        db.add(row)
        db.commit()
        raise HTTPException(status_code=400, detail=exc.code) from exc
    row.status = "active"
    row.last_error_code = None
    row.last_tested_at = utc_now()
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return {**_inbox_read(row), "uid_validity_present": bool(uid_validity), "highest_uid": highest_uid}


@router.post("/email-inboxes/{inbox_id}/disable")
def disable_email_inbox(
    inbox_id: str,
    request: TenantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    row = _inbox(db, request.tenant_id, inbox_id)
    row.status = "disabled"
    row.credentials_enc = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return _inbox_read(row)


@router.get("/recruiting-capabilities")
def recruiting_capabilities(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, tenant_id, current_user)
    return probe_document_capabilities()


@router.post("/recruiting-digest-configs")
def create_recruiting_digest_config(
    request: RecruitingDigestConfigCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    _validate_config_references(db, request.tenant_id, request)
    _validate_times(request.snapshot_time, request.earliest_delivery_time, request.misfire_deadline_time)
    if request.status == "active":
        raise HTTPException(status_code=422, detail="请先保存停用配置并完成模型隐私门禁")
    row = RecruitingDigestConfig(
        tenant_id=request.tenant_id,
        agent_id=request.agent_id,
        inbox_binding_id=request.inbox_binding_id,
        model_config_id=request.model_config_id,
        feishu_binding_id=request.feishu_binding_id,
        recipient_open_id=request.recipient_open_id.strip(),
        recipient_allowlist_json=[request.recipient_open_id.strip()],
        timezone=request.timezone,
        snapshot_time=request.snapshot_time,
        earliest_delivery_time=request.earliest_delivery_time,
        misfire_deadline_time=request.misfire_deadline_time,
        raw_retention_days=request.raw_retention_days,
        result_retention_days=request.result_retention_days,
        status=request.status,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.flush()
    task = _create_digest_task(row, current_user.id)
    db.add(task)
    db.flush()
    row.scheduled_task_id = task.id
    db.add(row)
    db.commit()
    db.refresh(row)
    return _config_read(row)


@router.get("/recruiting-digest-configs")
def list_recruiting_digest_configs(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _admin(db, tenant_id, current_user)
    return [
        _config_read(row)
        for row in db.exec(
            select(RecruitingDigestConfig)
            .where(RecruitingDigestConfig.tenant_id == tenant_id)
            .order_by(RecruitingDigestConfig.created_at.desc())
        ).all()
    ]


@router.patch("/recruiting-digest-configs/{config_id}")
def update_recruiting_digest_config(
    config_id: str,
    request: RecruitingDigestConfigPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    row = _config(db, request.tenant_id, config_id)
    for field in (
        "agent_id",
        "inbox_binding_id",
        "model_config_id",
        "feishu_binding_id",
        "timezone",
        "snapshot_time",
        "earliest_delivery_time",
        "misfire_deadline_time",
        "raw_retention_days",
        "result_retention_days",
        "status",
    ):
        value = getattr(request, field)
        if value is not None:
            setattr(row, field, value)
    if request.recipient_open_id is not None:
        target = request.recipient_open_id.strip()
        row.recipient_open_id = target
        row.recipient_allowlist_json = [target]
    if request.model_privacy_verified is not None:
        model = db.get(ModelConfig, row.model_config_id)
        if request.model_privacy_verified and (not model or not model.verified_fingerprint):
            raise HTTPException(status_code=422, detail="模型必须先完成连通性验证")
        row.model_privacy_verified = request.model_privacy_verified
        row.model_privacy_fingerprint = (
            model.verified_fingerprint if request.model_privacy_verified and model else None
        )
    _validate_config_references(db, row.tenant_id, row)
    _validate_times(row.snapshot_time, row.earliest_delivery_time, row.misfire_deadline_time)
    if row.status == "active":
        inbox = db.get(EmailInboxBinding, row.inbox_binding_id)
        model = db.get(ModelConfig, row.model_config_id)
        if not inbox or inbox.status != "active" or not inbox.credentials_enc:
            raise HTTPException(status_code=422, detail="启用前必须通过邮箱只读连接测试")
        if not model or not row.model_privacy_verified or row.model_privacy_fingerprint != model.verified_fingerprint:
            raise HTTPException(status_code=422, detail="MODEL_PRIVACY_UNVERIFIED")
    row.updated_at = utc_now()
    task = db.get(ScheduledTask, row.scheduled_task_id or "")
    if task:
        task.agent_id = row.agent_id
        task.timezone = row.timezone
        task.schedule_json = {"time": row.snapshot_time}
        task.rrule = _daily_rrule(row.snapshot_time)
        task.status = "active" if row.status == "active" else "paused"
        task.execution_config_json = {"digest_config_id": row.id}
        task.delivery_config_json = {
            "binding_id": row.feishu_binding_id,
            "receive_id": row.recipient_open_id,
            "receive_id_type": "open_id",
        }
        task.next_run_at = compute_next_run_at(task, after=utc_now()) if task.status == "active" else None
        task.updated_at = utc_now()
        db.add(task)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _config_read(row)


@router.get("/recruiting-digest-batches")
def list_recruiting_digest_batches(
    tenant_id: str = Query(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _admin(db, tenant_id, current_user)
    rows = db.exec(
        select(RecruitingDigestBatch)
        .where(RecruitingDigestBatch.tenant_id == tenant_id)
        .order_by(RecruitingDigestBatch.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [_batch_read(row, include_report=False) for row in rows]


@router.get("/recruiting-digest-batches/{batch_id}")
def get_recruiting_digest_batch(
    batch_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, tenant_id, current_user)
    row = db.get(RecruitingDigestBatch, batch_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="招聘日报批次不存在")
    return _batch_read(row, include_report=True)


@router.post("/recruiting-digest-batches/{batch_id}/retry-delivery")
def retry_recruiting_digest_delivery(
    batch_id: str,
    request: TenantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    batch = db.get(RecruitingDigestBatch, batch_id)
    if not batch or batch.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="招聘日报批次不存在")
    delivery = db.get(ChannelDelivery, batch.delivery_id or "")
    if not delivery or delivery.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="投递记录不存在")
    if delivery.status == "delivered":
        return {"ok": True, "delivery_status": "delivered"}
    delivery.status = "pending"
    delivery.next_attempt_at = utc_now()
    delivery.sending_since = None
    delivery.last_error = None
    delivery.updated_at = utc_now()
    db.add(delivery)
    db.commit()
    return {"ok": True, "delivery_status": "pending"}


@router.get("/recruiting-applications/{application_id}")
def get_recruiting_application(
    application_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, tenant_id, current_user)
    row = db.get(RecruitingApplication, application_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="候选人记录不存在")
    evaluation = db.exec(
        select(RecruitingEvaluation).where(RecruitingEvaluation.application_id == row.id)
    ).first()
    artifacts = db.exec(
        select(RecruitingAttachmentArtifact).where(
            RecruitingAttachmentArtifact.application_id == row.id
        )
    ).all()
    return {
        **_application_read(row),
        "evaluation": _evaluation_read(evaluation) if evaluation else None,
        "artifacts": [_artifact_read(item) for item in artifacts],
    }


@router.get("/recruiting-role-profiles")
def list_recruiting_role_profiles(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _admin(db, tenant_id, current_user)
    rows = db.exec(
        select(RecruitingRoleProfile)
        .where(
            RecruitingRoleProfile.tenant_id == tenant_id,
            RecruitingRoleProfile.is_current == True,  # noqa: E712
        )
        .order_by(RecruitingRoleProfile.display_name)
    ).all()
    return [_profile_read(row, db) for row in rows]


@router.get("/recruiting-role-profiles/{profile_id}")
def get_recruiting_role_profile(
    profile_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, tenant_id, current_user)
    row = db.get(RecruitingRoleProfile, profile_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="岗位画像不存在")
    versions = db.exec(
        select(RecruitingRoleProfile)
        .where(
            RecruitingRoleProfile.tenant_id == tenant_id,
            RecruitingRoleProfile.standard_role_key == row.standard_role_key,
        )
        .order_by(RecruitingRoleProfile.version.desc())
    ).all()
    return {**_profile_read(row, db), "versions": [_profile_read(item, db) for item in versions]}


@router.post("/recruiting-role-profiles/{profile_id}/regenerate")
def regenerate_recruiting_role_profile(
    profile_id: str,
    request: RegenerateRoleProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _admin(db, request.tenant_id, current_user)
    row = db.get(RecruitingRoleProfile, profile_id)
    model = db.get(ModelConfig, request.model_config_id)
    if not row or row.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="岗位画像不存在")
    if not model or model.tenant_id != request.tenant_id or not model.enabled:
        raise HTTPException(status_code=422, detail="模型配置不可用")
    normalized = NormalizedRole(
        row.normalized_function_name,
        row.specialization,
        row.explicit_level,
        row.standard_role_key,
        1.0,
        "admin_regeneration",
    )
    created = get_or_create_role_profile(
        db,
        row.tenant_id,
        normalized,
        row.display_name,
        "admin",
        model,
        force_new_version=True,
    )
    return _profile_read(created, db)


def _admin(db: Session, tenant_id: str, user: User) -> None:
    ensure_tenant_admin(tenant_id, user)
    ensure_tenant(db, tenant_id)


def _inbox(db: Session, tenant_id: str, inbox_id: str) -> EmailInboxBinding:
    row = db.get(EmailInboxBinding, inbox_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="邮箱绑定不存在")
    return row


def _config(db: Session, tenant_id: str, config_id: str) -> RecruitingDigestConfig:
    row = db.get(RecruitingDigestConfig, config_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="招聘日报配置不存在")
    return row


def _validate_config_references(db: Session, tenant_id: str, value: object) -> None:
    references = (
        (AgentProfile, str(getattr(value, "agent_id")), "招聘 HR 员工不存在"),
        (EmailInboxBinding, str(getattr(value, "inbox_binding_id")), "邮箱绑定不存在"),
        (ModelConfig, str(getattr(value, "model_config_id")), "模型配置不存在"),
        (ChannelBinding, str(getattr(value, "feishu_binding_id")), "飞书绑定不存在"),
    )
    rows: list[object] = []
    for model_type, identifier, message in references:
        row = db.get(model_type, identifier)
        if not row or getattr(row, "tenant_id", None) != tenant_id:
            raise HTTPException(status_code=422, detail=message)
        rows.append(row)
    agent, inbox, model, feishu = rows
    if getattr(agent, "status", None) != "active" or getattr(agent, "is_overall", False):
        raise HTTPException(status_code=422, detail="招聘 HR 员工必须是已激活的独立员工")
    if getattr(inbox, "status", None) == "disabled":
        raise HTTPException(status_code=422, detail="邮箱绑定已停用")
    if not getattr(model, "enabled", False):
        raise HTTPException(status_code=422, detail="模型配置未启用")
    if getattr(feishu, "channel", None) != "feishu" or getattr(feishu, "status", None) != "active":
        raise HTTPException(status_code=422, detail="必须选择已激活的飞书绑定")


def _create_digest_task(config: RecruitingDigestConfig, user_id: str) -> ScheduledTask:
    task = ScheduledTask(
        tenant_id=config.tenant_id,
        agent_id=config.agent_id,
        created_by_user_id=user_id,
        title="招聘 HR 日报",
        prompt="类型化招聘日报工作流",
        description="只读同步飞书企业邮箱并生成招聘候选人日报",
        schedule_type="daily",
        schedule_json={"time": config.snapshot_time},
        timezone=config.timezone,
        rrule=_daily_rrule(config.snapshot_time),
        status="active" if config.status == "active" else "paused",
        concurrency_policy="forbid",
        misfire_policy="coalesce",
        execution_kind="recruiting_digest",
        execution_config_json={"digest_config_id": config.id},
        delivery_config_json={
            "binding_id": config.feishu_binding_id,
            "receive_id": config.recipient_open_id,
            "receive_id_type": "open_id",
        },
    )
    task.next_run_at = compute_next_run_at(task, after=utc_now()) if task.status == "active" else None
    return task


def _validate_times(snapshot: str, delivery: str, deadline: str) -> None:
    parsed = [_parse_time(value) for value in (snapshot, delivery, deadline)]
    if not parsed[0] < parsed[1] < parsed[2]:
        raise HTTPException(status_code=422, detail="时间必须满足快照 < 最早投递 < 补跑截止")


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="时间格式需要为 HH:mm") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise HTTPException(status_code=422, detail="时间格式需要为 HH:mm")
    return hour, minute


def _daily_rrule(value: str) -> str:
    hour, minute = _parse_time(value)
    return f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute};BYSECOND=0"


def _inbox_read(row: EmailInboxBinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "email_address": row.email_address,
        "imap_host": row.imap_host,
        "imap_port": row.imap_port,
        "mailbox_name": row.mailbox_name,
        "status": row.status,
        "has_credentials": bool(row.credentials_enc),
        "last_tested_at": row.last_tested_at,
        "last_error_code": row.last_error_code,
        "updated_at": row.updated_at,
    }


def _config_read(row: RecruitingDigestConfig) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "agent_id": row.agent_id,
        "inbox_binding_id": row.inbox_binding_id,
        "model_config_id": row.model_config_id,
        "feishu_binding_id": row.feishu_binding_id,
        "recipient_open_id": row.recipient_open_id,
        "timezone": row.timezone,
        "snapshot_time": row.snapshot_time,
        "earliest_delivery_time": row.earliest_delivery_time,
        "misfire_deadline_time": row.misfire_deadline_time,
        "raw_retention_days": row.raw_retention_days,
        "result_retention_days": row.result_retention_days,
        "model_privacy_verified": row.model_privacy_verified,
        "status": row.status,
        "scheduled_task_id": row.scheduled_task_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _batch_read(row: RecruitingDigestBatch, *, include_report: bool) -> dict[str, Any]:
    result = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "digest_config_id": row.digest_config_id,
        "status": row.status,
        "snapshot_at": row.snapshot_at,
        "window_started_at": row.window_started_at,
        "new_email_count": row.new_email_count,
        "scored_count": row.scored_count,
        "review_count": row.review_count,
        "failed_count": row.failed_count,
        "completed_at": row.completed_at,
        "scheduled_delivery_at": row.scheduled_delivery_at,
        "delivered_at": row.delivered_at,
        "delivery_id": row.delivery_id,
        "error_code": row.error_code,
    }
    if include_report:
        result["report"] = row.full_report_json
    return result


def _application_read(row: RecruitingApplication) -> dict[str, Any]:
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "received_at": row.received_at,
        "sender_display": row.sender_display,
        "candidate_display_name": row.candidate_display_name,
        "status": row.status,
        "evidence": row.evidence_json,
        "job_title_candidates": row.job_title_candidates_json,
        "applied_job_title_raw": row.applied_job_title_raw,
        "applied_job_title_source": row.applied_job_title_source,
        "standard_role_key": row.standard_role_key,
        "normalized_function_name": row.normalized_function_name,
        "specialization": row.specialization,
        "explicit_level": row.explicit_level,
        "normalization_method": row.normalization_method,
        "normalization_confidence": row.normalization_confidence,
        "job_resolution_status": row.job_resolution_status,
        "warnings": row.warnings_json,
        "error_code": row.error_code,
        "raw_available": bool(row.encrypted_source_ref),
    }


def _evaluation_read(row: RecruitingEvaluation) -> dict[str, Any]:
    return {
        "id": row.id,
        "role_profile_id": row.role_profile_id,
        "role_profile_version": row.role_profile_version,
        "evaluation_stage": row.evaluation_stage,
        "weights": row.weights_json,
        "dimension_scores": row.dimension_scores_json,
        "match_evidence": row.match_evidence_json,
        "core_capability_assessments": row.core_capability_assessments_json,
        "critical_gaps": row.critical_gaps_json,
        "major_experience_subtotal": row.major_experience_subtotal,
        "recommendation_index": row.recommendation_index,
        "batch_rank": row.batch_rank,
        "recommendation_reasons": row.recommendation_reasons_json,
        "risks": row.risks_json,
        "missing_information": row.missing_information_json,
        "model_version": row.model_version,
        "model_protocol": row.model_protocol,
        "prompt_version": row.prompt_version,
    }


def _artifact_read(row: RecruitingAttachmentArtifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "parent_artifact_id": row.parent_artifact_id,
        "original_filename": row.original_filename,
        "archive_entry_name": row.archive_entry_name,
        "detected_format": row.detected_format,
        "nesting_depth": row.nesting_depth,
        "source_sha256": row.source_sha256,
        "derived_sha256": row.derived_sha256,
        "processing_type": row.processing_type,
        "processor_name": row.processor_name,
        "processor_version": row.processor_version,
        "processing_duration_ms": row.processing_duration_ms,
        "status": row.status,
        "error_code": row.error_code,
        "available": bool(row.encrypted_file_ref),
    }


def _profile_read(row: RecruitingRoleProfile, db: Session) -> dict[str, Any]:
    aliases = db.exec(
        select(RecruitingRoleAlias).where(RecruitingRoleAlias.role_profile_id == row.id)
    ).all()
    return {
        "id": row.id,
        "standard_role_key": row.standard_role_key,
        "normalized_function_name": row.normalized_function_name,
        "specialization": row.specialization,
        "explicit_level": row.explicit_level,
        "display_name": row.display_name,
        "responsibilities": row.responsibilities_json,
        "core_capabilities": row.core_capabilities_json,
        "evidence_guidance": row.evidence_guidance_json,
        "warnings": row.warnings_json,
        "version": row.version,
        "is_current": row.is_current,
        "model_version": row.model_version,
        "prompt_version": row.prompt_version,
        "schema_version": row.schema_version,
        "aliases": [
            {
                "raw_title": item.raw_title,
                "source": item.source,
                "confidence": item.confidence,
                "confirmation_method": item.confirmation_method,
            }
            for item in aliases
        ],
        "created_at": row.created_at,
    }
