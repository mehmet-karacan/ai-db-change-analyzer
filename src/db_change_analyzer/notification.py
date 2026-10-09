from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .compat import UTC
from .smtp_transport import SmtpDeliveryError, SmtpResult, SmtpTransport
from .state import StateError


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class NotificationOutcome:
    status: str
    smtp_attempts: int
    accepted_transactions: int
    error_code: str | None


def canonical_recipients(recipients: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for address in recipients:
        normalized = address.strip().lower()
        if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized) or normalized.count("@") != 1:
            raise ValueError("invalid SMTP envelope address")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if not result:
        raise ValueError("recipient list is empty")
    return result


def persist_notification(
    connection: sqlite3.Connection,
    *,
    notification_id: str,
    report_id: str,
    generation: int,
    recipients: list[str],
    message_id: str,
    mime_bytes: bytes,
    mime_sha256: str,
) -> None:
    with connection:
        insert_notification(
            connection, notification_id=notification_id, report_id=report_id,
            generation=generation, recipients=recipients, message_id=message_id,
            mime_bytes=mime_bytes, mime_sha256=mime_sha256,
        )


def insert_notification(
    connection: sqlite3.Connection,
    *,
    notification_id: str,
    report_id: str,
    generation: int,
    recipients: list[str],
    message_id: str,
    mime_bytes: bytes,
    mime_sha256: str,
) -> None:
    """Insert an immutable outbox row inside the caller's transaction."""
    addresses = canonical_recipients(recipients)
    connection.execute(
        "INSERT INTO notifications(notification_id,report_id,generation,envelope_json,message_id,mime_bytes,mime_sha256,status) VALUES (?,?,?,?,?,?,?,'READY')",
        (notification_id, report_id, generation, json.dumps(addresses, separators=(",", ":")), message_id, mime_bytes, mime_sha256),
    )
    connection.executemany(
        "INSERT INTO notification_recipients(notification_id,address,status) VALUES (?,?,'PENDING')",
        [(notification_id, address) for address in addresses],
    )


def finalize_auto_delivery(connection: sqlite3.Connection, notification_id: str, scope_hash: str) -> None:
    """Advance an active AUTO run only after its immutable envelope is accepted.

    Call inside the transaction that records SMTP acceptance or operator
    resolution. Manual reports and later resends of committed runs are inert.
    """
    row = connection.execute(
        """SELECT r.run_id,r.mode,r.status,r.scope_hash,r.epoch,r.base_sha,r.target_sha,
                  r.report_id AS current_report_id,p.report_id,
                  p.quality,n.status AS notification_status
           FROM notifications n
           JOIN reports p ON p.report_id=n.report_id
           JOIN runs r ON r.run_id=p.run_id
           WHERE n.notification_id=?""",
        (notification_id,),
    ).fetchone()
    if row is None:
        raise StateError("notification is not linked to its current report")
    if row["scope_hash"] != scope_hash:
        raise StateError("notification scope mismatch")
    if (row["current_report_id"] != row["report_id"] or row["mode"] != "AUTO"
            or row["status"] != "REPORTED" or row["notification_status"] != "ACCEPTED"):
        return
    if row["quality"] == "blocked":
        connection.execute("UPDATE runs SET status='REVIEW_REQUIRED' WHERE run_id=?", (row["run_id"],))
        return
    cursor = connection.execute(
        "UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=? AND epoch=? AND checkpoint_sha IS ?",
        (row["target_sha"], scope_hash, row["epoch"], row["base_sha"]),
    )
    if cursor.rowcount != 1:
        raise StateError("checkpoint compare-and-swap failed during delivery commit")
    connection.execute("UPDATE runs SET status='COMMITTED' WHERE run_id=?", (row["run_id"],))
    connection.execute(
        "INSERT INTO audit_events(scope_hash,run_id,action,actor,expected_base,target,created_at) VALUES (?,?,'CHECKPOINT_ADVANCED','system',?,?,?)",
        (scope_hash, row["run_id"], row["base_sha"], row["target_sha"], _now()),
    )


def finalize_artifact_checkpoint(connection: sqlite3.Connection, report_id: str, scope_hash: str) -> None:
    """Advance an AUTO checkpoint after the immutable artifact is durable."""
    row = connection.execute(
        """SELECT r.run_id,r.mode,r.status,r.scope_hash,r.epoch,r.base_sha,r.target_sha,p.quality
           FROM reports p JOIN runs r ON r.run_id=p.run_id WHERE p.report_id=?""",
        (report_id,),
    ).fetchone()
    if row is None:
        raise StateError("artifact report is not linked to a run")
    if row["scope_hash"] != scope_hash or row["mode"] != "AUTO" or row["status"] != "REPORTED":
        return
    if row["quality"] == "blocked":
        connection.execute("UPDATE runs SET status='REVIEW_REQUIRED' WHERE run_id=?", (row["run_id"],))
        return
    cursor = connection.execute(
        "UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=? AND epoch=? AND checkpoint_sha IS ?",
        (row["target_sha"], scope_hash, row["epoch"], row["base_sha"]),
    )
    if cursor.rowcount != 1:
        raise StateError("checkpoint compare-and-swap failed during artifact commit")
    connection.execute("UPDATE runs SET status='COMMITTED' WHERE run_id=?", (row["run_id"],))
    connection.execute(
        "INSERT INTO audit_events(scope_hash,run_id,action,actor,expected_base,target,created_at) VALUES (?,?,'ARTIFACT_CHECKPOINT_ADVANCED','system',?,?,?)",
        (scope_hash, row["run_id"], row["base_sha"], row["target_sha"], _now()),
    )


def send_persisted_notification(
    connection: sqlite3.Connection,
    notification_id: str,
    transport: SmtpTransport,
    *,
    username: str | None,
    password: str | None,
    on_accepted_transaction: Callable[[sqlite3.Connection, str], None] | None = None,
) -> NotificationOutcome:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT
               *
           FROM notifications
           WHERE notification_id = ?""",
        (notification_id,),
    ).fetchone()
    if row is None:
        raise ValueError("notification does not exist")
    if hashlib.sha256(row["mime_bytes"]).hexdigest() != row["mime_sha256"]:
        raise ValueError("persisted notification MIME hash mismatch")
    if row["status"] == "UNKNOWN":
        raise ValueError("unknown delivery requires explicit operator resolution")
    recipients = [item["address"] for item in connection.execute(
        """SELECT
               address
           FROM notification_recipients
           WHERE notification_id=? AND status='PENDING'
           ORDER BY address""",
        (notification_id,),
    )]
    if not recipients:
        raise ValueError("notification has no pending recipients")
    attempt_id = str(uuid.uuid4())
    with connection:
        connection.execute("INSERT INTO notification_attempts(attempt_id,notification_id,intended_recipients_json,state,started_at) VALUES (?,?,?,'INFLIGHT',?)", (attempt_id, notification_id, json.dumps(recipients), _now()))
        connection.execute("UPDATE notifications SET status='INFLIGHT', attempt_count=attempt_count+1 WHERE notification_id=?", (notification_id,))

    def data_started() -> None:
        with connection:
            connection.execute("UPDATE notifications SET status='DATA_STARTED' WHERE notification_id=?", (notification_id,))

    try:
        result = transport.send(row["mime_bytes"], recipients, username=username, password=password, on_data_started=data_started)
    except SmtpDeliveryError as exc:
        state = "UNKNOWN" if exc.unknown else "FAILED"
        with connection:
            connection.execute("UPDATE notification_attempts SET state=?, completed_at=?, sanitized_error=? WHERE attempt_id=?", (state, _now(), exc.code, attempt_id))
            connection.execute("UPDATE notifications SET status=? WHERE notification_id=?", (state, notification_id))
            if exc.unknown:
                connection.execute("UPDATE notification_recipients SET status='UNKNOWN' WHERE notification_id=? AND status='PENDING'", (notification_id,))
        return NotificationOutcome(state, 1, 0, exc.code)

    with connection:
        for address in result.accepted:
            connection.execute("UPDATE notification_recipients SET status='ACCEPTED', smtp_code=250, accepted_at=? WHERE notification_id=? AND address=?", (_now(), notification_id, address))
        for address, code in result.refused.items():
            connection.execute("UPDATE notification_recipients SET status='REFUSED', smtp_code=? WHERE notification_id=? AND address=?", (code, notification_id, address))
        all_accepted = connection.execute(
            """SELECT
                   COUNT(*)
               FROM notification_recipients
               WHERE notification_id=? AND status!='ACCEPTED'""",
            (notification_id,),
        ).fetchone()[0] == 0
        status = "ACCEPTED" if all_accepted else "PARTIAL"
        connection.execute("UPDATE notification_attempts SET state='ACCEPTED', completed_at=? WHERE attempt_id=?", (_now(), attempt_id))
        connection.execute("UPDATE notifications SET status=? WHERE notification_id=?", (status, notification_id))
        if on_accepted_transaction is not None:
            on_accepted_transaction(connection, status)
    return NotificationOutcome(status, 1, 1, None)
