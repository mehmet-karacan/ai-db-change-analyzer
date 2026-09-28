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
from .models import ExitCode, ResultRecord


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db-change-analyzer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", required=True, help="trusted TOML configuration")
    parser.add_argument("--emit-dir", help="owned build output leaf")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor", help="validate local prerequisites")
    doctor_parser.add_argument("--offline", action="store_true", required=True)
    doctor_parser.set_defaults(handler=doctor)
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
