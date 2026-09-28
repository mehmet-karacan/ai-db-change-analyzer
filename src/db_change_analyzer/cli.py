from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .config import ConfigError, load_config
from .git_client import GitClient, GitError
from .history import HistoryError, HistoryPlanner, RangePlan
from .inventory import inventory_revision
from .locking import LockBusyError
from .models import ExitCode, Quality, ResultRecord
from .state import SqliteStateStore, StateError


def _json_line(value: Any, *, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stream, flush=True)


def _result(*, mode: str, outcome: str, exit_code: int, error_code: str | None = None, emitted_files: list[str] | None = None, checkpoint_before: str | None = None, checkpoint_after: str | None = None, run_id: str | None = None, report_id: str | None = None, report_sha256: str | None = None, notification_status: str = "NOT_APPLICABLE", quality: str | None = None, ai_http_attempts: int = 0, smtp_attempts: int = 0, smtp_accepted_transactions: int = 0) -> ResultRecord:
    return ResultRecord(
        operation_id=str(uuid.uuid4()), run_id=run_id, report_id=report_id, report_sha256=report_sha256,
        mode=mode, outcome=outcome, exit_code=exit_code, checkpoint_before=checkpoint_before,
        checkpoint_after=checkpoint_after, notification_status=notification_status, quality=Quality(quality) if quality else None,
        ai_http_attempts=ai_http_attempts, smtp_attempts=smtp_attempts, smtp_accepted_transactions=smtp_accepted_transactions,
        originating_build=None, current_build=None, emitted_files=emitted_files or [], error_code=error_code,
    )


def _write_result(args: argparse.Namespace, result: ResultRecord) -> None:
    if args.emit_dir:
        directory = Path(args.emit_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "result.json"
        temporary = directory / f".result.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(result.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    _json_line(result.model_dump(mode="json", exclude_none=False))


def _finish(args: argparse.Namespace, *, mode: str, outcome: str, exit_code: int = 0, error_code: str | None = None, emitted_files: list[str] | None = None, checkpoint_before: str | None = None, checkpoint_after: str | None = None, **details: Any) -> int:
    result = _result(mode=mode, outcome=outcome, exit_code=exit_code, error_code=error_code, emitted_files=emitted_files, checkpoint_before=checkpoint_before, checkpoint_after=checkpoint_after, **details)
    _write_result(args, result)
    return int(exit_code)


def _git_version() -> tuple[bool, str]:
    executable = shutil.which("git")
    if not executable:
        return False, "missing"
    try:
        environment = {name: value for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if (value := os.environ.get(name))}
        completed = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True, timeout=10, shell=False, env=environment)
    except (OSError, subprocess.SubprocessError):
        return False, "unavailable"
    return True, completed.stdout.strip()


def doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    git_ok, git_version = _git_version()
    grammar_manifest = Path(__file__).parent / "oracle" / "generated" / "manifest.json"
    checks = {
        "python": {"ok": sys.version_info[:2] == (3, 13), "version": platform.python_version()},
        "git": {"ok": git_ok, "version": git_version},
        "config": {"ok": True, "digest": config.config_digest, "scope_hash": config.scope_hash},
        "grammar_manifest": {"ok": grammar_manifest.is_file()},
        "network_performed": False, "model_performed": False, "smtp_performed": False,
    }
    state = SqliteStateStore(config)
    if state.paths.root.exists():
        try:
            checks["state"] = {"ok": True, **state.verify()}
        except StateError:
            checks["state"] = {"ok": False, "code": "STATE_INVALID"}
    else:
        checks["state"] = {"ok": True, "warning": "STATE_NOT_INITIALIZED"}
    ok = all(item.get("ok", True) for item in checks.values() if isinstance(item, dict))
    _json_line({"event": "doctor_checks", "checks": checks}, stream=sys.stderr)
    return _finish(args, mode="DOCTOR", outcome="DOCTOR_OK" if ok else "DOCTOR_FAILED", exit_code=0 if ok else ExitCode.CONFIG, error_code=None if ok else "PREREQUISITE_FAILED")


def state_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = SqliteStateStore(config)
    with store.lock():
        emitted: list[str] = []
        if args.state_command == "init":
            details = store.initialize()
            outcome = "STATE_INITIALIZED"
        elif args.state_command == "status":
            details = store.status()
            outcome = "STATE_OK"
        elif args.state_command == "backup":
            backup = store.backup()
            details, outcome, emitted = {"backup": str(backup)}, "STATE_BACKUP_COMPLETE", [str(backup)]
        elif args.state_command == "restore":
            store.restore(Path(args.backup), args.reason)
            details, outcome = store.status(), "STATE_RESTORE_COMPLETE"
        elif args.state_command == "rebaseline":
            store.rebaseline(args.expected_base, args.target, args.reason)
            details, outcome = store.status(), "STATE_REBASELINED"
        elif args.state_command == "retry-blocked":
            generation = store.retry_blocked(args.run_id, args.expected_base, args.report_sha256, args.reason)
            details, outcome = {"analysis_generation": generation}, "BLOCKED_RETRY_PLANNED"
        elif args.state_command == "acknowledge-limits":
            store.acknowledge_limits(args.run_id, args.expected_base, args.report_sha256, args.reason)
            details, outcome = store.status(), "LIMITS_ACKNOWLEDGED"
        elif args.state_command == "migrate-run":
            generation = store.migrate_run(args.run_id, args.reason)
            details, outcome = {"analysis_generation": generation}, "RUN_MIGRATED"
        else:
            raise StateError("unsupported state command")
    _json_line({"event": "state_details", "details": details}, stream=sys.stderr)
    return _finish(args, mode="STATE", outcome=outcome, emitted_files=emitted)


def _client(config, store: SqliteStateStore) -> GitClient:
    return GitClient(store.paths.source, timeout_seconds=config.git.timeout_seconds)


def _target(git: GitClient, config, *, offline: bool) -> str:
    if offline:
        git.ensure_cache()
        return git.resolve_commit(f"refs/remotes/source/{config.repository.branch}")
    return git.fetch(
        config.repository.url,
        config.repository.branch,
        username=os.environ.get("DB_ANALYZER_GIT_USERNAME"),
        password=os.environ.get("DB_ANALYZER_GIT_PASSWORD"),
    )


def _automatic_plan(config, store: SqliteStateStore, *, offline: bool) -> RangePlan:
    status = store.verify()
    git = _client(config, store)
    target = _target(git, config, offline=offline)
    return HistoryPlanner(git, [root.path for root in config.scope.roots], config.git.max_new_commits).automatic(status["checkpoint_sha"], target)


def _plan_projection(plan: RangePlan) -> dict[str, Any]:
    return {
        "outcome": plan.outcome, "base_sha": plan.base_sha, "target_sha": plan.target_sha,
        "commit_count": len(plan.commits), "net_file_count": len(plan.net_deltas),
        "scope_file_count": len({item.path for item in plan.scope_deltas}),
    }


def plan_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = SqliteStateStore(config)
    plan = _automatic_plan(config, store, offline=args.offline)
    _json_line({"event": "range_plan", "plan": _plan_projection(plan)}, stream=sys.stderr)
    return _finish(args, mode="PLAN", outcome="PLAN_COMPLETE", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)


def inventory_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = SqliteStateStore(config)
    store.verify()
    git = _client(config, store)
    git.ensure_cache()
    target = git.resolve_commit(args.target)
    inventories = inventory_revision(git, target, {root.path: root.default_schema for root in config.scope.roots}, max_file_bytes=config.parser.max_file_bytes, timeout_seconds=config.parser.worker_timeout_seconds)
    projection = {
        "schema_version": 1, "target": target,
        "files": [{"path_display": item.entry.path_display, "path_b64": item.entry.path_b64, "bytes": item.bytes, "parse_status": item.parse_status, "diagnostics": list(item.diagnostics), "object_count": len(item.occurrences)} for item in inventories],
    }
    emitted: list[str] = []
    if args.emit_dir:
        path = Path(args.emit_dir).resolve() / "inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        emitted.append(str(path))
    _json_line({"event": "inventory_summary", "target": target, "files": len(inventories), "objects": sum(len(item.occurrences) for item in inventories)}, stream=sys.stderr)
    return _finish(args, mode="INVENTORY", outcome="INVENTORY_COMPLETE", emitted_files=emitted)


def run_command(args: argparse.Namespace) -> int:
    if args.dry_run and (args.allow_ai or args.allow_mail):
        return _finish(args, mode="RUN", outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="DRY_RUN_PERMISSION_CONFLICT")
    config = load_config(args.config)
    store = SqliteStateStore(config)
    with store.lock():
        store.recover_inflight_notifications()
        plan = _automatic_plan(config, store, offline=args.offline)
        _json_line({"event": "range_plan", "plan": _plan_projection(plan)}, stream=sys.stderr)
        if args.dry_run:
            return _finish(args, mode="RUN", outcome="DRY_RUN", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
        if plan.outcome == "BASELINED":
            store.compare_and_swap_checkpoint(None, plan.target_sha, store.verify()["epoch"])
            return _finish(args, mode="RUN", outcome="BASELINED", checkpoint_before=None, checkpoint_after=plan.target_sha)
        if plan.outcome == "NO_CHANGE":
            return _finish(args, mode="RUN", outcome="NO_CHANGE", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
        if plan.outcome == "OUT_OF_SCOPE_ONLY":
            store.compare_and_swap_checkpoint(plan.base_sha, plan.target_sha, store.verify()["epoch"])
            return _finish(args, mode="RUN", outcome="OUT_OF_SCOPE_ONLY", checkpoint_before=plan.base_sha, checkpoint_after=plan.target_sha)
        if not args.allow_ai or not args.allow_mail:
            return _finish(args, mode="RUN", outcome="LIVE_PERMISSION_REQUIRED", exit_code=ExitCode.CONFIG, error_code="LIVE_PERMISSION_REQUIRED", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
        if not config.model.route_verified or not config.model.capabilities_verified:
            return _finish(args, mode="RUN", outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="MODEL_CAPABILITY_UNVERIFIED", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
        # Stateful analysis is intentionally entered only through a verified profile.
        from .workflow import execute_analysis
        return execute_analysis(args, config, store, plan, _client(config, store))


def manual_command(args: argparse.Namespace) -> int:
    if args.force_reanalysis and not args.reason:
        return _finish(args, mode="MANUAL", outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="REASON_REQUIRED")
    config = load_config(args.config)
    store = SqliteStateStore(config)
    with store.lock():
        store.verify()
        git = _client(config, store)
        git.ensure_cache()
        planner = HistoryPlanner(git, [root.path for root in config.scope.roots], config.git.max_new_commits)
        plan = planner.manual_root(args.target) if args.base == "ROOT" else planner.automatic(args.base, args.target)
        _json_line({"event": "manual_plan", "plan": _plan_projection(plan)}, stream=sys.stderr)
        checkpoint = store.verify()["checkpoint_sha"]
        if args.dry_run:
            return _finish(args, mode="MANUAL", outcome="DRY_RUN", checkpoint_before=checkpoint, checkpoint_after=checkpoint)
        if not args.allow_ai:
            return _finish(args, mode="MANUAL", outcome="LIVE_PERMISSION_REQUIRED", exit_code=ExitCode.CONFIG, error_code="LIVE_PERMISSION_REQUIRED")
        if not config.model.route_verified or not config.model.capabilities_verified:
            return _finish(args, mode="MANUAL", outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="MODEL_CAPABILITY_UNVERIFIED")
        from .workflow import execute_analysis
        return execute_analysis(args, config, store, plan, git, mode="MANUAL")


def cleanup_command(args: argparse.Namespace) -> int:
    from .retention import RetentionManager
    config = load_config(args.config)
    store = SqliteStateStore(config)
    with store.lock():
        manifest = RetentionManager(store, config.retention).plan()
        if args.apply:
            manifest = RetentionManager(store, config.retention).apply(manifest)
    _json_line({"event": "cleanup_manifest", "manifest": manifest}, stream=sys.stderr)
    return _finish(args, mode="CLEANUP", outcome="CLEANUP_APPLIED" if args.apply else "CLEANUP_DRY_RUN")


def _new_outbox(connection, config, report_id: str, *, recipients: list[str] | None = None) -> str:
    from datetime import UTC, datetime
    from .notification import persist_notification
    from .reporting import RenderedReport, build_message

    row = connection.execute("""SELECT
        canonical_json,
        content_sha256,
        html,
        text
    FROM reports
    WHERE report_id=?""", (report_id,)).fetchone()
    if row is None:
        raise StateError("report does not exist")
    report = json.loads(row["canonical_json"])
    generation = connection.execute("""SELECT
        COALESCE(MAX(generation),-1)+1
    FROM notifications
    WHERE report_id=?""", (report_id,)).fetchone()[0]
    notification_id = str(uuid.uuid4())
    selected = recipients or config.smtp.recipients
    message_id = f"<{notification_id}@{config.smtp.message_id_domain}>"
    rendered = RenderedReport(row["canonical_json"], row["content_sha256"], row["html"], row["text"])
    message = build_message(report, rendered, sender=config.smtp.sender, recipients=selected, message_id=message_id, date=datetime.now(UTC), job_short_name=os.environ.get("JOB_NAME", "MANUAL"), current_build_number=os.environ.get("BUILD_NUMBER"), max_bytes=config.reports.mail_max_bytes)
    persist_notification(connection, notification_id=notification_id, report_id=report_id, generation=generation, recipients=selected, message_id=message_id, mime_bytes=message.mime_bytes, mime_sha256=message.sha256)
    return notification_id


def _send_outbox(connection, config, notification_id: str):
    from .notification import send_persisted_notification
    from .smtp_transport import SmtpTransport
    return send_persisted_notification(connection, notification_id, SmtpTransport(config.smtp), username=os.environ.get("DB_ANALYZER_SMTP_USERNAME"), password=os.environ.get("DB_ANALYZER_SMTP_PASSWORD"))


def notification_command(args: argparse.Namespace) -> int:
    import hashlib
    from email import policy
    from email.parser import BytesParser
    from .notification import canonical_recipients, persist_notification

    config = load_config(args.config)
    store = SqliteStateStore(config)
    with store.lock():
        connection = store.connection()
        try:
            command = args.notification_command
            if command in {"send", "resend"}:
                if not args.allow_mail:
                    return _finish(args, mode="NOTIFICATION", outcome="LIVE_PERMISSION_REQUIRED", exit_code=20, error_code="LIVE_PERMISSION_REQUIRED")
                notification_id = _new_outbox(connection, config, args.report_id)
                outcome = _send_outbox(connection, config, notification_id)
            elif command == "retry":
                if not args.allow_mail:
                    return _finish(args, mode="NOTIFICATION", outcome="LIVE_PERMISSION_REQUIRED", exit_code=20, error_code="LIVE_PERMISSION_REQUIRED")
                row = connection.execute("""SELECT
                    status
                FROM notifications
                WHERE notification_id=?""", (args.notification_id,)).fetchone()
                if row is None or row["status"] not in {"FAILED", "PARTIAL", "UNKNOWN"}:
                    raise StateError("notification is not retryable")
                if row["status"] == "UNKNOWN" and not args.ack_duplicate_risk:
                    raise StateError("unknown retry requires duplicate-risk acknowledgement")
                with connection:
                    connection.execute("UPDATE notifications SET status='READY' WHERE notification_id=?", (args.notification_id,))
                    connection.execute("UPDATE notification_recipients SET status='PENDING',smtp_code=NULL WHERE notification_id=? AND status!='ACCEPTED'", (args.notification_id,))
                    connection.execute("INSERT INTO audit_events(scope_hash,action,actor,reason,evidence_reference,created_at) VALUES (?,'NOTIFICATION_RETRY','operator',?,?,datetime('now'))", (config.scope_hash, args.reason, args.notification_id))
                notification_id = args.notification_id
                outcome = _send_outbox(connection, config, notification_id)
            elif command == "resolve":
                accepted = canonical_recipients(args.accepted)
                with connection:
                    row = connection.execute("""SELECT
                        status
                    FROM notifications
                    WHERE notification_id=?""", (args.notification_id,)).fetchone()
                    if row is None or row["status"] != "UNKNOWN":
                        raise StateError("only UNKNOWN delivery can be resolved")
                    for address in accepted:
                        cursor = connection.execute("UPDATE notification_recipients SET status='ACCEPTED',accepted_at=datetime('now') WHERE notification_id=? AND address=?", (args.notification_id, address))
                        if cursor.rowcount != 1:
                            raise StateError("accepted address is outside immutable envelope")
                    pending = connection.execute("""SELECT
                        COUNT(*)
                    FROM notification_recipients
                    WHERE notification_id=? AND status!='ACCEPTED'""", (args.notification_id,)).fetchone()[0]
                    connection.execute("UPDATE notifications SET status=? WHERE notification_id=?", ("ACCEPTED" if pending == 0 else "UNKNOWN", args.notification_id))
                    connection.execute("INSERT INTO audit_events(scope_hash,action,actor,reason,evidence_reference,created_at) VALUES (?,'NOTIFICATION_RESOLVED','operator',?,?,datetime('now'))", (config.scope_hash, args.reason, args.evidence))
                return _finish(args, mode="NOTIFICATION", outcome="NOTIFICATION_RESOLVED")
            elif command == "replace-envelope":
                selected = canonical_recipients(args.to)
                old = connection.execute("""SELECT
                    *
                FROM notifications
                WHERE notification_id=?""", (args.notification_id,)).fetchone()
                if old is None or old["status"] not in {"FAILED", "PARTIAL", "UNKNOWN", "HELD"}:
                    raise StateError("notification envelope is not replaceable")
                parsed = BytesParser(policy=policy.SMTP).parsebytes(old["mime_bytes"])
                parsed.replace_header("To", ", ".join(selected))
                notification_id = str(uuid.uuid4())
                parsed.replace_header("Message-ID", f"<{notification_id}@{config.smtp.message_id_domain}>")
                mime = parsed.as_bytes()
                generation = connection.execute("""SELECT
                    MAX(generation)+1
                FROM notifications
                WHERE report_id=?""", (old["report_id"],)).fetchone()[0]
                persist_notification(connection, notification_id=notification_id, report_id=old["report_id"], generation=generation, recipients=selected, message_id=parsed["Message-ID"], mime_bytes=mime, mime_sha256=hashlib.sha256(mime).hexdigest())
                with connection:
                    connection.execute("INSERT INTO audit_events(scope_hash,action,actor,reason,evidence_reference,created_at) VALUES (?,'ENVELOPE_REPLACED','operator',?,?,datetime('now'))", (config.scope_hash, args.reason, args.notification_id))
                return _finish(args, mode="NOTIFICATION", outcome="ENVELOPE_REPLACED")
            else:
                raise StateError("unsupported notification command")
        finally:
            connection.close()
    if outcome.status == "ACCEPTED":
        return _finish(args, mode="NOTIFICATION", outcome="NOTIFICATION_ACCEPTED")
    if outcome.status == "UNKNOWN":
        return _finish(args, mode="NOTIFICATION", outcome="NOTIFICATION_UNKNOWN", exit_code=41, error_code=outcome.error_code)
    return _finish(args, mode="NOTIFICATION", outcome="NOTIFICATION_FAILED_OR_HELD", exit_code=40, error_code=outcome.error_code or "RECIPIENT_NOT_ACCEPTED")


def smoke_model_command(args: argparse.Namespace) -> int:
    from .litellm_http import LiteLLMClient
    config = load_config(args.config)
    if not args.allow_ai:
        return _finish(args, mode="SMOKE_MODEL", outcome="LIVE_PERMISSION_REQUIRED", exit_code=20, error_code="LIVE_PERMISSION_REQUIRED")
    schema = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "unit-response.schema.json").read_text(encoding="utf-8"))
    payload = {"schema_version": "1.0", "unit_id": "synthetic-smoke", "object_identity": None, "artifact_paths": ["synthetic/smoke.sql"], "related_object_keys": [], "view_tags": ["net"], "source_pair": {"old_revision": None, "new_revision": "0" * 40}, "deterministic_facts": [], "evidence_registry": [{"kind": "source", "evidence_id": "synthetic-ev", "snippet": "CREATE TABLE SYNTHETIC_CHECK (ID NUMBER);"}], "dependency_edges": [], "coverage_manifest": {"synthetic": True}, "allowed_claim_kinds": ["interpretation"]}
    with LiteLLMClient(config.model) as client:
        reply = client.complete(api_key=os.environ["LITELLM_API_KEY"], system_message="Return only the requested JSON for this synthetic smoke input.", user_payload=payload, response_schema=schema, output_tokens=min(256, config.analysis.output_tokens))
    record = {"schema_version": 1, "synthetic": True, "route": client.url, "configured_model": config.model.id, "returned_model": reply.returned_model, "output_mode": config.model.output_mode, "verified_context_window_tokens": config.model.verified_context_window_tokens, "recorded_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()}
    path = Path(args.record).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return _finish(args, mode="SMOKE_MODEL", outcome="SMOKE_MODEL_COMPLETE", emitted_files=[str(path)])


def smoke_mail_command(args: argparse.Namespace) -> int:
    from email.message import EmailMessage
    from email.policy import SMTP
    from .smtp_transport import SmtpTransport
    config = load_config(args.config)
    if not args.allow_mail:
        return _finish(args, mode="SMOKE_MAIL", outcome="LIVE_PERMISSION_REQUIRED", exit_code=20, error_code="LIVE_PERMISSION_REQUIRED")
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"], message["Subject"] = config.smtp.sender, args.to, "AI DB Analyzer sentetik SMTP smoke"
    message.set_content("Bu sentetik bir SMTP yapilandirma testidir; production DDL icermez.")
    result = SmtpTransport(config.smtp).send(message.as_bytes(), [args.to], username=os.environ.get("DB_ANALYZER_SMTP_USERNAME"), password=os.environ.get("DB_ANALYZER_SMTP_PASSWORD"), on_data_started=lambda: None)
    return _finish(args, mode="SMOKE_MAIL", outcome="SMOKE_MAIL_ACCEPTED" if result.accepted else "SMOKE_MAIL_FAILED", exit_code=0 if result.accepted else 40)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db-change-analyzer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", required=True, help="trusted TOML configuration")
    parser.add_argument("--emit-dir", help="owned build output leaf")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--offline", action="store_true", required=True)
    doctor_parser.set_defaults(handler=doctor)
    inventory_parser = commands.add_parser("inventory")
    inventory_parser.add_argument("--target", required=True)
    inventory_parser.add_argument("--offline", action="store_true", required=True)
    inventory_parser.set_defaults(handler=inventory_command)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--offline", action="store_true")
    plan_parser.set_defaults(handler=plan_command)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--allow-ai", action="store_true")
    run_parser.add_argument("--allow-mail", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--offline", action="store_true")
    run_parser.set_defaults(handler=run_command)
    manual_parser = commands.add_parser("manual")
    manual_parser.add_argument("--base", required=True)
    manual_parser.add_argument("--target", required=True)
    manual_parser.add_argument("--allow-ai", action="store_true")
    manual_parser.add_argument("--dry-run", action="store_true")
    manual_parser.add_argument("--force-reanalysis", action="store_true")
    manual_parser.add_argument("--reason")
    manual_parser.set_defaults(handler=manual_command)
    cleanup_parser = commands.add_parser("cleanup")
    cleanup_mode = cleanup_parser.add_mutually_exclusive_group(required=True)
    cleanup_mode.add_argument("--dry-run", action="store_true")
    cleanup_mode.add_argument("--apply", action="store_true")
    cleanup_parser.set_defaults(handler=cleanup_command)
    notification_parser = commands.add_parser("notification")
    notification_commands = notification_parser.add_subparsers(dest="notification_command", required=True)
    send = notification_commands.add_parser("send")
    send.add_argument("--report-id", required=True)
    send.add_argument("--allow-mail", action="store_true", required=True)
    send.set_defaults(handler=notification_command)
    retry = notification_commands.add_parser("retry")
    retry.add_argument("--notification-id", required=True)
    retry.add_argument("--reason", required=True)
    retry.add_argument("--ack-duplicate-risk", action="store_true")
    retry.add_argument("--allow-mail", action="store_true", required=True)
    retry.set_defaults(handler=notification_command)
    resend = notification_commands.add_parser("resend")
    resend.add_argument("--report-id", required=True)
    resend.add_argument("--reason", required=True)
    resend.add_argument("--allow-mail", action="store_true", required=True)
    resend.set_defaults(handler=notification_command)
    resolve = notification_commands.add_parser("resolve")
    resolve.add_argument("--notification-id", required=True)
    resolve.add_argument("--accepted", nargs="+", required=True)
    resolve.add_argument("--evidence", required=True)
    resolve.add_argument("--reason", required=True)
    resolve.set_defaults(handler=notification_command)
    replace = notification_commands.add_parser("replace-envelope")
    replace.add_argument("--notification-id", required=True)
    replace.add_argument("--to", nargs="+", required=True)
    replace.add_argument("--reason", required=True)
    replace.set_defaults(handler=notification_command)
    smoke_model = commands.add_parser("smoke-model")
    smoke_model.add_argument("--allow-ai", action="store_true", required=True)
    smoke_model.add_argument("--record", required=True)
    smoke_model.set_defaults(handler=smoke_model_command)
    smoke_mail = commands.add_parser("smoke-mail")
    smoke_mail.add_argument("--to", required=True)
    smoke_mail.add_argument("--allow-mail", action="store_true", required=True)
    smoke_mail.set_defaults(handler=smoke_mail_command)
    state_parser = commands.add_parser("state")
    state_commands = state_parser.add_subparsers(dest="state_command", required=True)
    state_init = state_commands.add_parser("init")
    state_init.add_argument("--confirm-new-install", action="store_true", required=True)
    state_init.set_defaults(handler=state_command)
    for name in ("status", "backup"):
        child = state_commands.add_parser(name)
        child.set_defaults(handler=state_command)
    state_restore = state_commands.add_parser("restore")
    state_restore.add_argument("--backup", required=True)
    state_restore.add_argument("--reason", required=True)
    state_restore.set_defaults(handler=state_command)
    rebaseline = state_commands.add_parser("rebaseline")
    rebaseline.add_argument("--expected-base", required=True)
    rebaseline.add_argument("--target", required=True)
    rebaseline.add_argument("--reason", required=True)
    rebaseline.add_argument("--ack-unanalysed-history", action="store_true", required=True)
    rebaseline.set_defaults(handler=state_command)
    for name in ("retry-blocked", "acknowledge-limits"):
        child = state_commands.add_parser(name)
        child.add_argument("--run-id", required=True)
        child.add_argument("--expected-base", required=True)
        child.add_argument("--report-sha256", required=True)
        child.add_argument("--reason", required=True)
        child.set_defaults(handler=state_command)
    migrate = state_commands.add_parser("migrate-run")
    migrate.add_argument("--run-id", required=True)
    migrate.add_argument("--reason", required=True)
    migrate.set_defaults(handler=state_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ConfigError:
        return _finish(args, mode=str(args.command).upper(), outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="CONFIG_INVALID")
    except GitError as exc:
        return _finish(args, mode=str(args.command).upper(), outcome="GIT_IO_OR_AUTH", exit_code=ExitCode.GIT, error_code=exc.code)
    except HistoryError as exc:
        return _finish(args, mode=str(args.command).upper(), outcome="HISTORY_UNPROVEN", exit_code=ExitCode.HISTORY, error_code=exc.code)
    except LockBusyError:
        return _finish(args, mode=str(args.command).upper(), outcome="LOCK_BUSY", exit_code=ExitCode.STATE, error_code="LOCK_BUSY")
    except StateError:
        return _finish(args, mode=str(args.command).upper(), outcome="STATE_INVALID", exit_code=ExitCode.STATE, error_code="STATE_INVALID")
    except KeyboardInterrupt:
        return _finish(args, mode=str(args.command).upper(), outcome="INTERNAL_ERROR", exit_code=ExitCode.INTERNAL, error_code="INTERRUPTED")
    except Exception as exc:
        _json_line({"event": "unhandled_error", "type": type(exc).__name__}, stream=sys.stderr)
        return _finish(args, mode=str(args.command).upper(), outcome="INTERNAL_ERROR", exit_code=ExitCode.INTERNAL, error_code="INTERNAL_ERROR")
