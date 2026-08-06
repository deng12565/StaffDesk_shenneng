from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
import io

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from docx import Document
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels.adapters.feishu import FeishuAdapter, FeishuPermanentError
from app.channels.crypto import encrypt_channel_secret
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelDelivery,
    RecruitingApplication,
    RecruitingAttachmentArtifact,
    RecruitingDigestBatch,
    RecruitingDigestConfig,
    ScheduledTask,
    Tenant,
    User,
    utc_now,
)
from app.recruiting.domain import (
    CapabilityAssessment,
    EvaluationPayload,
    JobTitleCandidate,
    calculate_score,
    evaluation_stage,
    normalize_role_title,
    rank_evaluations,
    resolve_applied_job,
)
from app.recruiting.mailbox import ReadOnlyIMAPClient, parse_mail
from app.recruiting.reporting import FEISHU_SAFE_BUDGET_BYTES, render_feishu_digest
from app.recruiting.service import _attachment_documents, cleanup_expired_recruiting_data
from app.scheduled_tasks.service import execute_scheduled_task


def test_role_resolution_prefers_one_resume_role_and_preserves_email_conflict() -> None:
    candidates = [
        JobTitleCandidate(
            raw_title="后端开发工程师（Java）",
            source="resume",
            evidence_ref="resume.pdf#page=1",
        ),
        JobTitleCandidate(
            raw_title="产品经理",
            source="email_subject",
            evidence_ref="message.subject",
        ),
    ]

    resolved = resolve_applied_job(candidates)

    assert resolved.status == "resolved"
    assert resolved.source == "resume"
    assert resolved.warnings == ("JOB_TITLE_CONFLICT",)
    assert normalize_role_title("Java后端开发工程师").standard_role_key == normalize_role_title(
        "JAVA 后端工程师"
    ).standard_role_key


def test_role_resolution_does_not_choose_between_two_resume_roles() -> None:
    resolved = resolve_applied_job(
        [
            JobTitleCandidate(raw_title="产品经理", source="resume", evidence_ref="r#1"),
            JobTitleCandidate(raw_title="项目经理", source="resume", evidence_ref="r#2"),
            JobTitleCandidate(raw_title="产品经理", source="email_body", evidence_ref="m.body"),
        ]
    )
    assert resolved.status == "JOB_TITLE_AMBIGUOUS"


@pytest.mark.parametrize(
    ("level", "months", "expected", "warning"),
    [
        ("campus", 60, "campus", None),
        ("unspecified", 11, "campus", None),
        ("unspecified", 12, "junior", None),
        ("unspecified", 36, "experienced", None),
        ("unspecified", None, "junior", "FULL_TIME_EXPERIENCE_UNKNOWN"),
    ],
)
def test_evaluation_stage_boundaries(level, months, expected, warning) -> None:
    assert evaluation_stage(level, months) == (expected, warning)


def test_score_uses_experienced_weights_and_caps_nine_without_two_traceable_evidence() -> None:
    payload = EvaluationPayload(
        candidate_record_id="candidate_1",
        role_profile_id="profile_1",
        role_profile_version=1,
        evaluation_stage="experienced",
        dimension_scores={
            "relevant_work_experience": 10,
            "relevant_project_experience": 10,
            "relevant_internship_experience": 0,
            "other_supporting_evidence": 10,
        },
        match_evidence={
            "relevant_work_experience": ["resume#work-1"],
            "relevant_project_experience": [],
            "relevant_internship_experience": [],
            "other_supporting_evidence": ["resume#skill-1"],
        },
        core_capability_assessments=[
            CapabilityAssessment(capability_id="cap_1", status="strong", evidence=["resume#work-1"]),
            CapabilityAssessment(capability_id="cap_2", status="supported", evidence=["resume#skill-1"]),
        ],
    )

    score = calculate_score(
        payload,
        [
            {"id": "cap_1", "is_critical": True},
            {"id": "cap_2", "is_critical": False},
        ],
    )

    assert score.weights["relevant_internship_experience"] == 0
    assert score.major_experience_subtotal == 9.0
    assert score.recommendation_index == 8.9


def test_rank_is_stable_by_unrounded_major_then_time_then_id() -> None:
    rows = [
        {
            "application_id": "b",
            "recommendation_index": 8.6,
            "major_experience_subtotal": 7.123,
            "received_at": datetime(2026, 8, 6, 1, 0),
        },
        {
            "application_id": "a",
            "recommendation_index": 8.6,
            "major_experience_subtotal": 7.124,
            "received_at": datetime(2026, 8, 6, 2, 0),
        },
    ]
    assert [item["application_id"] for item in rank_evaluations(rows)] == ["a", "b"]


def test_mailbox_uses_readonly_select_uid_search_and_body_peek() -> None:
    fake = FakeIMAP()
    with ReadOnlyIMAPClient(
        "imap.feishu.cn",
        993,
        "hr@dlang.ai",
        "secret",
        client_factory=lambda *_args, **_kwargs: fake,
    ) as mailbox:
        assert mailbox.uid_validity() == "123"
        assert mailbox.highest_uid() == 11
        assert mailbox.uids_between(10, 11) == [10, 11]
        assert mailbox.fetch_peek(10) == b"Subject: test\r\n\r\nbody"
    assert ("select", "INBOX", True) in fake.calls
    assert ("uid", "FETCH", "10", "(BODY.PEEK[])") in fake.calls
    assert not any("STORE" in str(call).upper() for call in fake.calls)


def test_mailbox_retries_transient_handshake_but_keeps_readonly_boundary() -> None:
    first = FakeIMAP()
    first.capability_result = "NO"
    second = FakeIMAP()
    clients = iter((first, second))
    delays: list[float] = []

    with ReadOnlyIMAPClient(
        "imap.feishu.cn",
        993,
        "hr@dlang.ai",
        "secret",
        client_factory=lambda *_args, **_kwargs: next(clients),
        sleep_function=delays.append,
        retry_delays=(5,),
    ) as mailbox:
        assert mailbox.highest_uid() == 11

    assert delays == [5]
    assert ("logout",) in first.calls
    assert ("select", "INBOX", True) in second.calls


def test_mime_parser_decodes_html_without_loading_external_resources() -> None:
    message = EmailMessage()
    message["Subject"] = "应聘后端开发"
    message["From"] = "candidate@example.com"
    message["Message-ID"] = "<Example@Mail>"
    message.set_content("<p>求职内容</p><img src='https://tracker.invalid/pixel'>", subtype="html")
    message.add_attachment(b"resume", maintype="application", subtype="pdf", filename="简历.pdf")

    parsed = parse_mail(message.as_bytes(), max_message_bytes=1024 * 1024, max_attachment_bytes=1024)

    assert parsed.message_id == "<example@mail>"
    assert "求职内容" in parsed.body_text
    assert "tracker.invalid" not in parsed.body_text
    assert parsed.attachments[0].filename == "简历.pdf"


def test_feishu_scheduled_digest_is_one_open_id_message() -> None:
    calls: list[tuple[str, dict]] = []

    def handler(url: str, kwargs: dict) -> httpx.Response:
        if "/auth/" in url:
            return _response(200, {"code": 0, "tenant_access_token": "token", "expire": 7200}, url)
        calls.append((url, kwargs))
        return _response(200, {"code": 0}, url)

    adapter = FeishuAdapter(client_factory=lambda: FakeClient(handler))
    target = {
        "receive_id": "ou_hr",
        "receive_id_type": "open_id",
        "delivery_mode": "single_text",
    }
    adapter.send(_binding(), target, "x" * 5000, idempotency_key="digest:1")

    assert len(calls) == 1
    assert calls[0][1]["params"] == {"receive_id_type": "open_id"}
    with pytest.raises(FeishuPermanentError, match="open_id"):
        adapter.send(
            _binding(),
            {"receive_id": "oc_group", "receive_id_type": "chat_id", "delivery_mode": "single_text"},
            "digest",
            idempotency_key="digest:2",
        )


def test_digest_redacts_contacts_and_stays_under_budget() -> None:
    text = render_feishu_digest(
        {
            "batch_id": "batch_1",
            "report_date": "2026-08-06",
            "window": "上次快照后 - 07:00",
            "new_email_count": 1,
            "scored_count": 1,
            "review_count": 0,
            "ranked_candidates": [
                {
                    "batch_rank": 1,
                    "candidate_display_name": "张三",
                    "applied_job": "后端开发工程师",
                    "recommendation_index": 8.6,
                    "match_evidence": {
                        "relevant_work_experience": ["联系 13800138000"],
                        "relevant_project_experience": [],
                        "relevant_internship_experience": [],
                    },
                    "recommendation_reasons": ["详情 user@example.com"],
                    "risks": [],
                }
            ],
            "needs_review": [],
            "disclaimer": "仅用于人工复核优先级。",
        }
    )
    assert "13800138000" not in text
    assert "user@example.com" not in text
    assert len(text.encode("utf-8")) < FEISHU_SAFE_BUDGET_BYTES


def test_typed_recruiting_task_bypasses_agent_session(monkeypatch) -> None:
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_admin", tenant_id="tenant_demo", username="admin", password_hash="x", role="admin"))
        db.add(AgentProfile(id="agent_hr", tenant_id="tenant_demo", name="招聘 HR"))
        task = ScheduledTask(
            id="sched_digest",
            tenant_id="tenant_demo",
            agent_id="agent_hr",
            created_by_user_id="user_admin",
            title="招聘日报",
            prompt="typed",
            execution_kind="recruiting_digest",
            execution_config_json={"digest_config_id": "cfg_1"},
            next_run_at=utc_now(),
        )
        db.add(task)
        db.commit()
        monkeypatch.setattr(
            "app.recruiting.service.execute_recruiting_digest",
            lambda *_args, **_kwargs: "done",
        )

        run = execute_scheduled_task(db, task)

        assert run.status == "succeeded"
        assert run.session_id is None
        assert run.trace_json["execution_kind"] == "recruiting_digest"


def test_doc_conversion_records_original_and_derived_artifact_lineage(monkeypatch) -> None:
    output = io.BytesIO()
    document = Document()
    document.add_paragraph("candidate resume")
    document.save(output)

    class FakeStore:
        def put(self, _tenant_id, _application_id, payload):
            return f"{len(payload)}.enc"

    monkeypatch.setattr("app.recruiting.service.ArtifactStore", FakeStore)
    monkeypatch.setattr(
        "app.recruiting.service.convert_word_document",
        lambda _content, _filename: output.getvalue(),
    )
    monkeypatch.setattr(
        "app.recruiting.service.probe_document_capabilities",
        lambda: {
            "word": {"version": "16.0"},
            "seven_zip": {"version": "7-Zip 26.02"},
        },
    )
    engine = _test_engine()
    with Session(engine) as db:
        application = RecruitingApplication(
            tenant_id="tenant_demo",
            batch_id="batch_1",
            inbox_binding_id="inbox_1",
            uid_validity="1",
            mail_uid=1,
            message_sha256="mail",
            raw_expires_at=utc_now() + timedelta(days=7),
        )
        db.add(application)
        db.commit()

        documents = _attachment_documents(
            db,
            application,
            "resume.doc",
            "application/msword",
            bytes.fromhex("D0CF11E0A1B11AE1") + b"legacy-doc",
        )

        artifacts = db.exec(
            select(RecruitingAttachmentArtifact).order_by(RecruitingAttachmentArtifact.created_at)
        ).all()
        assert documents == [{"source": "resume.docx", "text": "candidate resume"}]
        assert len(artifacts) == 2
        assert artifacts[0].processing_type == "original"
        assert artifacts[0].processor_name == "Microsoft Word"
        assert artifacts[0].processor_version == "16.0"
        assert artifacts[0].derived_sha256 == artifacts[1].source_sha256
        assert artifacts[1].parent_artifact_id == artifacts[0].id
        assert artifacts[1].processing_type == "converted"


def test_retention_cleanup_deletes_expired_results_report_and_outbox() -> None:
    engine = _test_engine()
    old = utc_now() - timedelta(days=91)
    with Session(engine) as db:
        config = RecruitingDigestConfig(
            id="cfg_1",
            tenant_id="tenant_demo",
            agent_id="agent_hr",
            inbox_binding_id="inbox_1",
            model_config_id="model_1",
            feishu_binding_id="binding_1",
            recipient_open_id="ou_hr",
            result_retention_days=90,
            created_by_user_id="user_admin",
        )
        delivery = ChannelDelivery(
            id="delivery_1",
            tenant_id="tenant_demo",
            binding_id="binding_1",
            session_id="recruiting:batch_1",
            kind="scheduled_digest",
            text="candidate report",
            idempotency_key="recruiting-digest:batch_1",
        )
        batch = RecruitingDigestBatch(
            id="batch_1",
            tenant_id="tenant_demo",
            digest_config_id=config.id,
            scheduled_task_run_id="run_1",
            completed_at=old,
            delivery_id=delivery.id,
            message_text="candidate report",
            full_report_json={"candidate": "record"},
            created_at=old,
        )
        application = RecruitingApplication(
            id="candidate_1",
            tenant_id="tenant_demo",
            batch_id=batch.id,
            inbox_binding_id="inbox_1",
            uid_validity="1",
            mail_uid=1,
            message_sha256="mail",
            result_expires_at=old,
            created_at=old,
        )
        db.add(config)
        db.add(delivery)
        db.add(batch)
        db.add(application)
        db.commit()

        result = cleanup_expired_recruiting_data(db)

        assert result["deleted_records"] == 3
        assert db.get(RecruitingApplication, application.id) is None
        assert db.get(RecruitingDigestBatch, batch.id) is None
        assert db.get(ChannelDelivery, delivery.id) is None


class FakeIMAP:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.capability_result = "OK"

    def login(self, username, password):
        self.calls.append(("login", username, password))
        return "OK", [b"logged in"]

    def capability(self):
        self.calls.append(("capability",))
        return self.capability_result, [b"IMAP4rev1"]

    def list(self):
        self.calls.append(("list",))
        return "OK", [b"INBOX"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"2"]

    def response(self, name):
        self.calls.append(("response", name))
        return name, [b"123"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"10 11"]
        return "OK", [(b"10 (BODY[] {21})", b"Subject: test\r\n\r\nbody")]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b""]


class FakeClient:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, **kwargs):
        return self.handler(url, kwargs)


def _response(status: int, payload: dict, url: str) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def _binding() -> ChannelBinding:
    return ChannelBinding(
        id="chan_feishu",
        tenant_id="tenant_demo",
        agent_id="agent_hr",
        channel="feishu",
        status="active",
        config_json={"app_id": "cli_app"},
        credentials_enc=encrypt_channel_secret("secret"),
        external_account_key="feishu:app:7:cli_app",
    )


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine
