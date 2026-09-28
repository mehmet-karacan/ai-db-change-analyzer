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
from .locking import LockBusyError
from .models import ExitCode, ResultRecord
from .state import SqliteStateStore, StateError


def _json_line(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stream, flush=True)


def _result(*, mode: str, outcome: str, exit_code: int, error_code: str | None = None, emitted_files: list[str] | None = None) -> ResultRecord:
    return ResultRecord(
        operation_id=str(uuid.uuid4()),
        run_id=None,
        report_id=None,
        report_sha256=None,
        mode=mode,
        outcome=outcome,
        exit_code=exit_code,
        checkpoint_before=None,
        checkpoint_after=None,
        notification_status="NOT_APPLICABLE",
        quality=None,
        originating_build=None,
        current_build=None,
        emitted_files=emitted_files or [],
        error_code=error_code,
    )


def _git_version() -> tuple[bool, str]:
    executable = shutil.which("git")
    if not executable:
        return False, "missing"
    try:
        environment = {name: value for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if (value := os.environ.get(name))}
        completed = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "unavailable"
    return True, completed.stdout.strip()


def doctor(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError:
        result = _result(mode="DOCTOR", outcome="CONFIG_INVALID", exit_code=ExitCode.CONFIG, error_code="CONFIG_INVALID")
        _json_line(result.model_dump(mode="json", exclude_none=False))
        return int(ExitCode.CONFIG)

    git_ok, git_version = _git_version()
    checks = {
        "python": {"ok": sys.version_info[:2] == (3, 13), "version": platform.python_version()},
        "git": {"ok": git_ok, "version": git_version},
        "config": {"ok": True, "digest": config.config_digest, "scope_hash": config.scope_hash},
        "network_performed": False,
        "model_performed": False,
        "smtp_performed": False,
    }
    ok = all(item.get("ok", True) for item in checks.values() if isinstance(item, dict))
    result = _result(mode="DOCTOR", outcome="DOCTOR_OK" if ok else "DOCTOR_FAILED", exit_code=0 if ok else ExitCode.CONFIG, error_code=None if ok else "PREREQUISITE_FAILED")
    _json_line({"event": "doctor_checks", "checks": checks}, stream=sys.stderr)
    _json_line(result.model_dump(mode="json", exclude_none=False))
    return int(result.exit_code)


def state_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        store = SqliteStateStore(config)
        with store.lock():
            if args.state_command == "init":
                if not args.confirm_new_install:
                    raise StateError("explicit new installation confirmation is required")
                details = store.initialize()
                outcome = "STATE_INITIALIZED"
                emitted: list[str] = []
            elif args.state_command == "status":
                details = store.verify()
                outcome = "STATE_OK"
                emitted = []
            elif args.state_command == "backup":
                backup = store.backup()
                details = {"backup": str(backup)}
                outcome = "STATE_BACKUP_COMPLETE"
                emitted = [str(backup)]
            elif args.state_command == "restore":
                store.restore(Path(args.backup), args.reason)
                details = store.verify()
                outcome = "STATE_RESTORE_COMPLETE"
                emitted = []
            else:  # argparse constrains this branch
                raise StateError("unsupported state command")
        _json_line({"event": "state_details", "details": details}, stream=sys.stderr)
        result = _result(mode="STATE", outcome=outcome, exit_code=0, emitted_files=emitted)
        _json_line(result.model_dump(mode="json", exclude_none=False))
        return 0
    except ConfigError:
        error_code = "CONFIG_INVALID"
        exit_code = ExitCode.CONFIG
    except LockBusyError:
        error_code = "LOCK_BUSY"
        exit_code = ExitCode.STATE
    except StateError:
        error_code = "STATE_INVALID"
        exit_code = ExitCode.STATE
    result = _result(mode="STATE", outcome=error_code, exit_code=exit_code, error_code=error_code)
    _json_line(result.model_dump(mode="json", exclude_none=False))
    return int(exit_code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db-change-analyzer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", required=True, help="trusted TOML configuration")
    parser.add_argument("--emit-dir", help="owned build output leaf")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor", help="validate local prerequisites")
    doctor_parser.add_argument("--offline", action="store_true", required=True)
    doctor_parser.set_defaults(handler=doctor)
    state_parser = commands.add_parser("state", help="manage durable local state")
    state_commands = state_parser.add_subparsers(dest="state_command", required=True)
    state_init = state_commands.add_parser("init")
    state_init.add_argument("--confirm-new-install", action="store_true", required=True)
    state_init.set_defaults(handler=state_command)
    state_status = state_commands.add_parser("status")
    state_status.set_defaults(handler=state_command)
    state_backup = state_commands.add_parser("backup")
    state_backup.set_defaults(handler=state_command)
    state_restore = state_commands.add_parser("restore")
    state_restore.add_argument("--backup", required=True)
    state_restore.add_argument("--reason", required=True)
    state_restore.set_defaults(handler=state_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        result = _result(mode=str(args.command).upper(), outcome="INTERNAL_ERROR", exit_code=ExitCode.INTERNAL, error_code="INTERRUPTED")
        _json_line(result.model_dump(mode="json", exclude_none=False))
        return int(ExitCode.INTERNAL)
    except Exception as exc:  # last-resort boundary; raw inputs and exception text are not emitted
        _json_line({"event": "unhandled_error", "type": type(exc).__name__}, stream=sys.stderr)
        result = _result(mode=str(args.command).upper(), outcome="INTERNAL_ERROR", exit_code=ExitCode.INTERNAL, error_code="INTERNAL_ERROR")
        _json_line(result.model_dump(mode="json", exclude_none=False))
        return int(ExitCode.INTERNAL)
