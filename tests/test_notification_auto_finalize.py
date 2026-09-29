from __future__ import annotations

import hashlib
import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from db_change_analyzer.cli import _restore_v5_outputs, _verify_v5_notification_binding, main
from db_change_analyzer.config import load_config
from db_change_analyzer.notification import finalize_auto_delivery, insert_notification, persist_notification
from db_change_analyzer.smtp_transport import SmtpResult
from db_change_analyzer.state import SqliteStateStore, StateError
from db_change_analyzer.v5_rendering import build_render_manifest, render_v5_view
from datetime import UTC, datetime


ROOT = Path(__file__).resolve().parents[1]
REPORT_ID = "34af36f4-be37-5271-8d46-dfc8b4abf9c1"


def _state(tmp_path: Path, *, quality: str = "complete", mode: str = "AUTO"):
    source = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8")
    source = source.replace('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"')
    source = source.replace('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"')
    config_path = tmp_path / "config.toml"
    config_path.write_text(source.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"'), encoding="utf-8")
    config = load_config(config_path)
    store = SqliteStateStore(config)
    with store.lock():
        store.initialize()
    base, target = "a" * 40, "b" * 40
    mime = b"Subject: local test\r\n\r\nbody\r\n"
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=?", (base, config.scope_hash))
            connection.execute(
                "INSERT INTO runs(run_id,scope_hash,mode,base_sha,target_sha,epoch,status,config_digest,fingerprint_json,planned_at,report_id,quality) VALUES (?,?,?,?,?,1,?,?,?,?,?,?)",
                ("run-test", config.scope_hash, mode, base, target, "REPORTED" if mode == "AUTO" else "CLOSED", config.config_digest, "{}", "2026-09-29T00:00:00+00:00", REPORT_ID, quality),
            )
            connection.execute(
                "INSERT INTO reports(report_id,run_id,canonical_json,content_sha256,html,text,rendered_version,quality,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (REPORT_ID, "run-test", b"{}", hashlib.sha256(b"{}").hexdigest(), b"", b"", "v5.0", quality, "2026-09-29T00:00:00+00:00"),
            )
        persist_notification(
            connection, notification_id="notification-test", report_id=REPORT_ID, generation=0,
            recipients=["one@example.test", "two@example.test"], message_id="<notification-test@example.test>",
            mime_bytes=mime, mime_sha256=hashlib.sha256(mime).hexdigest(),
        )
    finally:
        connection.close()
    return config_path, store, base, target, mime


def _record(store: SqliteStateStore):
    connection = store.connection()
    try:
        return (connection.execute("SELECT checkpoint_sha FROM scopes WHERE scope_hash=?", (store.config.scope_hash,)).fetchone()[0],
                connection.execute("SELECT status FROM runs WHERE run_id='run-test'").fetchone()[0],
                connection.execute("SELECT status FROM notifications WHERE notification_id='notification-test'").fetchone()[0])
    finally:
        connection.close()


def _add_v5_sidecar(store: SqliteStateStore, config_path: Path) -> None:
    config = load_config(config_path)
    view = json.loads((ROOT / "tests" / "fixtures" / "v5" / "03-sequence-only.view.json").read_text(encoding="utf-8"))
    rendered = render_v5_view(
        view, sender=config.smtp.sender, recipients=["one@example.test", "two@example.test"],
        message_id="<notification-test@example.test>", date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    report_sha = hashlib.sha256(b"{}").hexdigest()
    manifest = build_render_manifest(view, rendered, source_report_sha256=report_sha)
    view_json = json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    connection = store.connection()
    try:
        with connection:
            connection.execute(
                "INSERT INTO report_render_sidecars(report_id,render_generation,source_report_sha256,mail_view_json,mail_view_sha256,manifest_json,manifest_sha256,html,text,mime,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (REPORT_ID, 0, report_sha, view_json, rendered.view_sha256, manifest_json,
                 hashlib.sha256(manifest_json).hexdigest(), rendered.html, rendered.text, rendered.mime,
                 "2026-09-29T00:00:00+00:00"),
            )
    finally:
        connection.close()


@pytest.mark.parametrize("quality,expected_run", [("complete", "COMMITTED"), ("blocked", "REVIEW_REQUIRED")])
def test_operator_retry_finalizes_auto_run_atomically(tmp_path: Path, monkeypatch, quality: str, expected_run: str) -> None:
    config_path, store, base, target, mime = _state(tmp_path, quality=quality)
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='PARTIAL' WHERE notification_id='notification-test'")
            connection.execute("UPDATE notification_recipients SET status='ACCEPTED' WHERE notification_id='notification-test' AND address='one@example.test'")
            connection.execute("UPDATE notification_recipients SET status='REFUSED' WHERE notification_id='notification-test' AND address='two@example.test'")
    finally:
        connection.close()
    sent = []

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, message, recipients, **kwargs):
            sent.append((message, tuple(recipients)))
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.smtp_transport.SmtpTransport", FakeMail)
    assert main(["--config", str(config_path), "notification", "retry", "--notification-id", "notification-test", "--reason", "local test", "--allow-mail"]) == 0
    assert sent == [(mime, ("two@example.test",))]
    assert _record(store) == (target if quality == "complete" else base, expected_run, "ACCEPTED")


def test_operator_resolve_finalizes_only_when_all_recipients_proven(tmp_path: Path) -> None:
    config_path, store, base, target, _mime = _state(tmp_path)
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='UNKNOWN' WHERE notification_id='notification-test'")
            connection.execute("UPDATE notification_recipients SET status='UNKNOWN' WHERE notification_id='notification-test'")
    finally:
        connection.close()
    command = ["--config", str(config_path), "notification", "resolve", "--notification-id", "notification-test", "--evidence", "relay-record", "--reason", "operator verified", "--accepted"]
    assert main([*command, "one@example.test"]) == 0
    assert _record(store) == (base, "REPORTED", "UNKNOWN")
    assert main([*command, "two@example.test"]) == 0
    assert _record(store) == (target, "COMMITTED", "ACCEPTED")


def test_manual_report_delivery_does_not_advance_auto_checkpoint(tmp_path: Path, monkeypatch) -> None:
    config_path, store, base, _target, _mime = _state(tmp_path, mode="MANUAL")
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='FAILED' WHERE notification_id='notification-test'")
    finally:
        connection.close()

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _message, recipients, **kwargs):
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.smtp_transport.SmtpTransport", FakeMail)
    assert main(["--config", str(config_path), "notification", "retry", "--notification-id", "notification-test", "--reason", "local test", "--allow-mail"]) == 0
    assert _record(store) == (base, "CLOSED", "ACCEPTED")


def test_replaced_v5_envelope_gets_manifest_and_ready_outbox_can_send(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path, store, base, _target, _mime = _state(tmp_path, mode="MANUAL")
    _add_v5_sidecar(store, config_path)
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='FAILED' WHERE notification_id='notification-test'")
    finally:
        connection.close()
    assert main(["--config", str(config_path), "notification", "replace-envelope", "--notification-id", "notification-test", "--to", "new@example.test", "--reason", "recipient correction"]) == 0
    stderr = capsys.readouterr().err
    event = next(json.loads(line) for line in stderr.splitlines() if '"event":"notification_outbox"' in line)
    new_id = event["notification_id"]
    connection = store.connection()
    try:
        outbox = connection.execute("SELECT generation,status,mime_bytes FROM notifications WHERE notification_id=?", (new_id,)).fetchone()
        sidecar = connection.execute("SELECT mime,manifest_json FROM report_render_sidecars WHERE report_id=? AND render_generation=1", (REPORT_ID,)).fetchone()
        assert outbox["generation"] == 1 and outbox["status"] == "READY"
        assert outbox["mime_bytes"] == sidecar["mime"]
        assert hashlib.sha256(outbox["mime_bytes"]).hexdigest() == json.loads(sidecar["manifest_json"])["mime_sha256"]
    finally:
        connection.close()
    sent = []

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, message, recipients, **kwargs):
            sent.append((message, tuple(recipients)))
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.smtp_transport.SmtpTransport", FakeMail)
    assert main(["--config", str(config_path), "notification", "retry", "--notification-id", new_id, "--reason", "send replacement", "--allow-mail"]) == 0
    assert sent == [(outbox["mime_bytes"], ("new@example.test",))]
    assert _record(store)[0:2] == (base, "CLOSED")


def test_prior_accepted_auto_receipt_can_finalize_once_without_resending(tmp_path: Path) -> None:
    _config_path, store, _base, target, _mime = _state(tmp_path)
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='ACCEPTED' WHERE notification_id='notification-test'")
            connection.execute("UPDATE notification_recipients SET status='ACCEPTED' WHERE notification_id='notification-test'")
            finalize_auto_delivery(connection, "notification-test", store.config.scope_hash)
            finalize_auto_delivery(connection, "notification-test", store.config.scope_hash)
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='CHECKPOINT_ADVANCED'").fetchone()[0] == 1
    finally:
        connection.close()
    assert _record(store) == (target, "COMMITTED", "ACCEPTED")


def test_resolve_checkpoint_conflict_rolls_back_receipt(tmp_path: Path) -> None:
    config_path, store, _base, _target, _mime = _state(tmp_path)
    connection = store.connection()
    try:
        with connection:
            connection.execute("UPDATE notifications SET status='UNKNOWN' WHERE notification_id='notification-test'")
            connection.execute("UPDATE notification_recipients SET status='UNKNOWN' WHERE notification_id='notification-test'")
            connection.execute("UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=?", ("c" * 40, store.config.scope_hash))
    finally:
        connection.close()
    assert main(["--config", str(config_path), "notification", "resolve", "--notification-id", "notification-test", "--accepted", "one@example.test", "two@example.test", "--evidence", "relay-record", "--reason", "operator verified"]) != 0
    assert _record(store) == ("c" * 40, "REPORTED", "UNKNOWN")


def test_report_sidecar_and_outbox_failure_leave_no_half_report(tmp_path: Path) -> None:
    _config_path, store, _base, _target, _mime = _state(tmp_path)
    connection = store.connection()
    try:
        with pytest.raises(ValueError, match="recipient list is empty"):
            with connection:
                connection.execute(
                    "INSERT INTO reports(report_id,run_id,canonical_json,content_sha256,html,text,rendered_version,quality,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("report-half", "run-test", b"half", hashlib.sha256(b"half").hexdigest(), b"html", b"text", "v5.0", "complete", "2026-09-29T00:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO report_render_sidecars(report_id,render_generation,source_report_sha256,mail_view_json,mail_view_sha256,manifest_json,manifest_sha256,html,text,mime,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("report-half", 0, hashlib.sha256(b"half").hexdigest(), b"{}", "0" * 64, b"{}", "0" * 64, b"html", b"text", b"mime", "2026-09-29T00:00:00+00:00"),
                )
                insert_notification(connection, notification_id="half-outbox", report_id="report-half", generation=0,
                                    recipients=[], message_id="<half@example.test>", mime_bytes=b"mime", mime_sha256=hashlib.sha256(b"mime").hexdigest())
        assert connection.execute("SELECT COUNT(*) FROM reports WHERE report_id='report-half'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM report_render_sidecars WHERE report_id='report-half'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM notifications WHERE notification_id='half-outbox'").fetchone()[0] == 0
    finally:
        connection.close()


def test_pinned_v5_outputs_are_repaired_from_verified_sidecar_before_delivery(tmp_path: Path) -> None:
    config_path, store, _base, _target, _mime = _state(tmp_path)
    _add_v5_sidecar(store, config_path)
    args = Namespace(emit_dir=str(tmp_path / "out"))
    connection = store.connection()
    try:
        report = connection.execute("SELECT canonical_json,content_sha256,rendered_version FROM reports WHERE report_id=?", (REPORT_ID,)).fetchone()
        emitted = _restore_v5_outputs(args, load_config(config_path), connection, REPORT_ID, report)
        assert len(emitted) == 5
        assert (tmp_path / "out" / "report.json").read_bytes() == b"{}\n"
        assert (tmp_path / "out" / "report.html").read_bytes().endswith(b"</html>")
        (tmp_path / "out" / "report.txt").write_bytes(b"partial")
        assert _restore_v5_outputs(args, load_config(config_path), connection, REPORT_ID, report) == [str((tmp_path / "out" / "report.txt").resolve())]
        with connection:
            connection.execute("UPDATE report_render_sidecars SET manifest_json=? WHERE report_id=?", (b"{}", REPORT_ID))
        with pytest.raises(StateError, match="hash mismatch"):
            _restore_v5_outputs(args, load_config(config_path), connection, REPORT_ID, report)
    finally:
        connection.close()


def test_v5_notification_mime_must_match_its_generation_manifest(tmp_path: Path) -> None:
    config_path, store, _base, _target, _mime = _state(tmp_path)
    _add_v5_sidecar(store, config_path)
    connection = store.connection()
    try:
        sidecar_mime = connection.execute("SELECT mime FROM report_render_sidecars WHERE report_id=? AND render_generation=0", (REPORT_ID,)).fetchone()[0]
        with connection:
            connection.execute("UPDATE notifications SET mime_bytes=?,mime_sha256=? WHERE notification_id='notification-test'", (sidecar_mime, hashlib.sha256(sidecar_mime).hexdigest()))
        report = connection.execute("SELECT content_sha256,rendered_version FROM reports WHERE report_id=?", (REPORT_ID,)).fetchone()
        notification = connection.execute("SELECT generation,mime_bytes,mime_sha256 FROM notifications WHERE notification_id='notification-test'").fetchone()
        _verify_v5_notification_binding(connection, REPORT_ID, report, notification)
        other = b"Subject: valid hash, wrong report bytes\r\n\r\nbody"
        with connection:
            connection.execute("UPDATE notifications SET mime_bytes=?,mime_sha256=? WHERE notification_id='notification-test'", (other, hashlib.sha256(other).hexdigest()))
        notification = connection.execute("SELECT generation,mime_bytes,mime_sha256 FROM notifications WHERE notification_id='notification-test'").fetchone()
        with pytest.raises(StateError, match="binding mismatch"):
            _verify_v5_notification_binding(connection, REPORT_ID, report, notification)
    finally:
        connection.close()
