from __future__ import annotations

import hashlib
import imaplib
import ssl
import time
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Callable

from bs4 import BeautifulSoup


IMAP_RETRY_DELAYS_SECONDS = (5, 30, 120)


class MailboxError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content_type: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class ParsedMail:
    subject: str
    sender: str
    message_id: str | None
    received_at: object | None
    body_text: str
    attachments: tuple[MailAttachment, ...]
    sha256: str


class ReadOnlyIMAPClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str = "INBOX",
        *,
        client_factory: Callable[..., imaplib.IMAP4_SSL] | None = None,
        sleep_function: Callable[[float], None] = time.sleep,
        retry_delays: tuple[float, ...] = IMAP_RETRY_DELAYS_SECONDS,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.client_factory = client_factory or imaplib.IMAP4_SSL
        self.sleep_function = sleep_function
        self.retry_delays = retry_delays
        self.client: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "ReadOnlyIMAPClient":
        for attempt in range(len(self.retry_delays) + 1):
            try:
                self.client = self.client_factory(
                    self.host,
                    self.port,
                    ssl_context=ssl.create_default_context(),
                )
                self._ok(self.client.login(self.username, self.password), "AUTH_REQUIRED")
                self._ok(self.client.capability(), "TRANSIENT_IMAP_ERROR")
                self._ok(self.client.list(), "MAILBOX_NOT_FOUND")
                self._ok(self.client.select(self.mailbox, readonly=True), "MAILBOX_NOT_FOUND")
                return self
            except imaplib.IMAP4.error as exc:
                self._close()
                raise MailboxError("AUTH_REQUIRED", "mailbox authentication failed") from exc
            except ssl.SSLCertVerificationError as exc:
                self._close()
                raise MailboxError("TLS_ERROR", "mailbox TLS certificate validation failed") from exc
            except (OSError, ssl.SSLError) as exc:
                self._close()
                if attempt >= len(self.retry_delays):
                    raise MailboxError("TLS_ERROR", "mailbox TLS connection failed") from exc
                self.sleep_function(self.retry_delays[attempt])
            except MailboxError as exc:
                self._close()
                if exc.code != "TRANSIENT_IMAP_ERROR" or attempt >= len(self.retry_delays):
                    raise
                self.sleep_function(self.retry_delays[attempt])
        raise MailboxError("TRANSIENT_IMAP_ERROR")

    def __exit__(self, *_args: object) -> None:
        self._close()

    def _close(self) -> None:
        if self.client is not None:
            try:
                self.client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self.client = None

    def uid_validity(self) -> str:
        def operation() -> object:
            client = self._require_client()
            _name, values = client.response("UIDVALIDITY")
            if not values or not values[0]:
                raise MailboxError("TRANSIENT_IMAP_ERROR", "UIDVALIDITY missing")
            return values[0]

        result = self._retry(operation)
        value = result.decode("ascii") if isinstance(result, bytes) else str(result)
        return value.strip()

    def highest_uid(self) -> int:
        status, data = self._retry(
            lambda: self._checked(self._require_client().uid("SEARCH", None, "ALL"))
        )
        values = _uid_values(data)
        return max(values, default=0)

    def uids_between(self, start_uid: int, end_uid: int) -> list[int]:
        if end_uid < start_uid:
            return []
        status, data = self._retry(
            lambda: self._checked(
                self._require_client().uid("SEARCH", None, f"UID {start_uid}:{end_uid}")
            )
        )
        return [uid for uid in _uid_values(data) if start_uid <= uid <= end_uid]

    def fetch_peek(self, uid: int) -> bytes:
        status, data = self._retry(
            lambda: self._checked(
                self._require_client().uid("FETCH", str(uid), "(BODY.PEEK[])")
            )
        )
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return item[1]
        raise MailboxError("PARSE_FAILED", "message body missing")

    def _require_client(self) -> imaplib.IMAP4_SSL:
        if self.client is None:
            raise RuntimeError("mailbox client is not connected")
        return self.client

    def _retry(self, operation: Callable[[], object]) -> object:
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return operation()
            except (imaplib.IMAP4.abort, OSError, MailboxError) as exc:
                code = exc.code if isinstance(exc, MailboxError) else "TRANSIENT_IMAP_ERROR"
                if code != "TRANSIENT_IMAP_ERROR" or attempt >= len(self.retry_delays):
                    if isinstance(exc, MailboxError):
                        raise
                    raise MailboxError("TRANSIENT_IMAP_ERROR") from exc
                self.sleep_function(self.retry_delays[attempt])
        raise MailboxError("TRANSIENT_IMAP_ERROR")

    @classmethod
    def _checked(cls, result: tuple[object, object]) -> tuple[object, object]:
        cls._ok(result, "TRANSIENT_IMAP_ERROR")
        return result

    @staticmethod
    def _ok(result: tuple[object, object], code: str) -> None:
        status = result[0]
        if str(status).upper() != "OK":
            raise MailboxError(code)


def parse_mail(raw: bytes, *, max_message_bytes: int, max_attachment_bytes: int) -> ParsedMail:
    if len(raw) > max_message_bytes:
        raise MailboxError("MESSAGE_TOO_LARGE")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_parts: list[str] = []
    attachments: list[MailAttachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = str(part.get_content_disposition() or "")
        content_type = str(part.get_content_type() or "application/octet-stream").lower()
        payload = part.get_payload(decode=True) or b""
        if filename or disposition == "attachment":
            if len(payload) > max_attachment_bytes:
                raise MailboxError("MESSAGE_TOO_LARGE")
            clean_name = str(filename or "attachment").replace("\x00", "").strip() or "attachment"
            attachments.append(
                MailAttachment(clean_name, content_type, payload, hashlib.sha256(payload).hexdigest())
            )
            continue
        if content_type == "text/plain":
            body_parts.append(_decode_text_part(part, payload))
        elif content_type == "text/html" and not body_parts:
            html = _decode_text_part(part, payload)
            body_parts.append(BeautifulSoup(html, "html.parser").get_text("\n", strip=True))
    date_value = None
    if message.get("Date"):
        try:
            date_value = parsedate_to_datetime(str(message.get("Date")))
        except (TypeError, ValueError):
            date_value = None
    message_id = str(message.get("Message-ID") or "").strip().lower() or None
    return ParsedMail(
        subject=str(message.get("Subject") or "").strip(),
        sender=str(message.get("From") or "").strip(),
        message_id=message_id,
        received_at=date_value,
        body_text="\n\n".join(part for part in body_parts if part.strip()).strip(),
        attachments=tuple(attachments),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _decode_text_part(part: Message, payload: bytes) -> str:
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _uid_values(data: object) -> list[int]:
    values: list[int] = []
    for item in data or []:
        if isinstance(item, bytes):
            values.extend(int(value) for value in item.split() if value.isdigit())
    return values
