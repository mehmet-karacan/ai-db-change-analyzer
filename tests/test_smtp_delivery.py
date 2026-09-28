from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from db_change_analyzer.config import load_config
from db_change_analyzer.notification import canonical_recipients, persist_notification, send_persisted_notification
from db_change_analyzer.smtp_transport import SmtpTransport


ROOT = Path(__file__).resolve().parents[1]


class FakeSmtp:
    def __init__(self, *_args, data_error: bool = False, **_kwargs) -> None:
        self.data_error = data_error
        self.calls: list[str] = []

    def ehlo(self):
        self.calls.append("ehlo")
        return 250, b"ok"

    def starttls(self, *, context):
        assert context.verify_mode != 0
        self.calls.append("starttls")
        return 220, b"ready"

    def login(self, username, password):
        assert username == "user" and password == "pass"
        self.calls.append("login")

    def mail(self, sender):
        self.calls.append("mail")
        return 250, b"ok"

    def rcpt(self, recipient):
        self.calls.append("rcpt")
        return (550, b"no") if recipient.startswith("reject") else (250, b"ok")

    def data(self, payload):
        self.calls.append("data")
        if self.data_error:
            raise TimeoutError("ambiguous")
        return 250, b"queued"

    def quit(self):
        self.calls.append("quit")
        return 221, b"bye"


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript((ROOT / "src" / "db_change_analyzer" / "migrations" / "001_initial.sql").read_text(encoding="utf-8"))
    connection.execute("INSERT INTO installation VALUES (1,'install',1,'now')")
    connection.execute("INSERT INTO scopes(scope_hash,repository_id,remote_digest,branch,scope_json,initialized_at) VALUES ('scope','repo','remote','main','{}','now')")
    connection.execute("INSERT INTO runs(run_id,scope_hash,mode,epoch,status,config_digest,fingerprint_json,planned_at) VALUES ('run','scope','MANUAL',1,'REPORTED','digest','{}','now')")
    connection.execute("INSERT INTO reports VALUES ('report','run',X'7B7D','digest',X'3C3E',X'74','1','limited','now')")
    return connection


def transport(factory) -> SmtpTransport:
    config = load_config(ROOT / "config" / "gpu.example.toml").smtp.model_copy(update={"host": "smtp.example.test", "sender": "sender@example.test", "message_id_domain": "example.test"})
    return SmtpTransport(config, factory=factory)


def test_recipient_snapshot_is_canonical_and_deduplicated() -> None:
    assert canonical_recipients([" A@Example.Test ", "a@example.test", "b@example.test"]) == ["a@example.test", "b@example.test"]


def test_smtp_persists_acceptance_and_refusal_with_one_transaction() -> None:
    connection = database()
    mime = b"From: sender@example.test\r\n\r\nbody\r\n"
    persist_notification(connection, notification_id="n1", report_id="report", generation=0, recipients=["ok@example.test", "reject@example.test"], message_id="<n1@example.test>", mime_bytes=mime, mime_sha256=hashlib.sha256(mime).hexdigest())
    instance = FakeSmtp()
    outcome = send_persisted_notification(connection, "n1", transport(lambda *_a, **_k: instance), username="user", password="pass")
    assert outcome.status == "PARTIAL" and outcome.accepted_transactions == 1
    rows = dict(connection.execute("SELECT address,status FROM notification_recipients").fetchall())
    assert rows == {"ok@example.test": "ACCEPTED", "reject@example.test": "REFUSED"}
    assert instance.calls == ["ehlo", "starttls", "ehlo", "login", "mail", "rcpt", "rcpt", "data", "quit"]


def test_disconnect_after_data_started_becomes_unknown_and_is_not_auto_retried() -> None:
    connection = database()
    mime = b"message"
    persist_notification(connection, notification_id="n2", report_id="report", generation=0, recipients=["ok@example.test"], message_id="<n2@example.test>", mime_bytes=mime, mime_sha256=hashlib.sha256(mime).hexdigest())
    outcome = send_persisted_notification(connection, "n2", transport(lambda *_a, **_k: FakeSmtp(data_error=True)), username="user", password="pass")
    assert outcome.status == "UNKNOWN" and outcome.error_code == "SMTP_DATA_UNKNOWN"
    try:
        send_persisted_notification(connection, "n2", transport(FakeSmtp), username="user", password="pass")
    except ValueError as exc:
        assert "explicit operator" in str(exc)
    else:
        raise AssertionError("UNKNOWN notification was retried")


def test_receipt_and_checkpoint_callback_share_one_transaction() -> None:
    connection = database()
    mime = b"message"
    persist_notification(connection, notification_id="n3", report_id="report", generation=0, recipients=["ok@example.test"], message_id="<n3@example.test>", mime_bytes=mime, mime_sha256=hashlib.sha256(mime).hexdigest())

    def fail_commit(_connection, _status):
        raise RuntimeError("simulated checkpoint failure")

    try:
        send_persisted_notification(connection, "n3", transport(FakeSmtp), username="user", password="pass", on_accepted_transaction=fail_commit)
    except RuntimeError:
        pass
    else:
        raise AssertionError("fault injection did not interrupt transaction")
    recipient = connection.execute("""SELECT
        status
    FROM notification_recipients
    WHERE notification_id='n3'""").fetchone()[0]
    attempt = connection.execute("""SELECT
        state
    FROM notification_attempts
    WHERE notification_id='n3'""").fetchone()[0]
    assert recipient == "PENDING" and attempt == "INFLIGHT"
