"""Write immutable, repository-friendly report projections to an application archive."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .compat import UTC


ARCHIVE_PREFIX = Path("db-change-analyzer") / "reports"
ARCHIVE_SCHEMA_VERSION = "db-change-archive/2.0"
LEGACY_ARCHIVE_SCHEMA_VERSIONS = {"db-change-archive/1.0", ARCHIVE_SCHEMA_VERSION}
_SAFE_PART = re.compile(r"[^A-Za-z0-9._-]+")


class ArchiveError(ValueError):
    """A report could not be archived without risking an overwrite or unsafe path."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _part(value: str, *, fallback: str = "unknown", maximum: int = 120) -> str:
    text = _SAFE_PART.sub("_", str(value)).strip("._-")[:maximum]
    return text or fallback


def _date_parts(value: str) -> tuple[str, str]:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ArchiveError("ARCHIVE_DATE_INVALID") from exc
    local = timestamp.astimezone(ZoneInfo("Europe/Istanbul"))
    return f"{local.year:04d}", local.date().isoformat()


def archive_directory(archive_root: str | Path, report: dict[str, Any]) -> Path:
    """Return the deterministic archive leaf for one immutable report."""
    if not archive_root or not str(archive_root).strip():
        raise ArchiveError("ARCHIVE_ROOT_NOT_CONFIGURED")
    run = report.get("run") or {}
    report_id = report.get("report_id")
    completed_at = run.get("analysis_completed_at") or run.get("observed_git_at")
    if not isinstance(report_id, str) or not report_id or not isinstance(completed_at, str):
        raise ArchiveError("ARCHIVE_METADATA_INCOMPLETE")
    year, day = _date_parts(completed_at)
    base = _part(run.get("base_sha") or "initial", maximum=64)
    target = _part(run.get("target_sha") or "unknown", maximum=64)
    leaf = f"{base}_{target}_{_part(report_id, maximum=80)}"
    root = Path(archive_root).expanduser().resolve()
    destination = root / ARCHIVE_PREFIX / year / day / _part(run.get("repository_id"), maximum=100) / _part(run.get("branch"), maximum=100) / leaf
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ArchiveError("ARCHIVE_PATH_ESCAPE") from exc
    return destination


def daily_archive_directory(
    archive_root: str | Path, repository_id: str, branch: str, *, when: str | None = None
) -> Path:
    """Return the date/repository/branch directory used to group reports."""
    if not archive_root or not str(archive_root).strip():
        raise ArchiveError("ARCHIVE_ROOT_NOT_CONFIGURED")
    timestamp = when or datetime.now(UTC).isoformat()
    year, day = _date_parts(timestamp)
    root = Path(archive_root).expanduser().resolve()
    destination = root / ARCHIVE_PREFIX / year / day / _part(repository_id, maximum=100) / _part(branch, maximum=100)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ArchiveError("ARCHIVE_PATH_ESCAPE") from exc
    return destination


def find_range_archive(
    archive_root: str | Path,
    repository_id: str,
    branch: str,
    base_sha: str | None,
    target_sha: str,
    *,
    when: str | None = None,
    analysis_fingerprint: str | None = None,
) -> Path | None:
    """Find a valid immutable record for one exact Git comparison range.

    The date directory is only a grouping boundary. Idempotency is determined by
    the base and target revisions, so multiple ranges can safely be archived on
    the same Istanbul calendar day.
    """
    directory = daily_archive_directory(archive_root, repository_id, branch, when=when)
    if not directory.is_dir() or directory.is_symlink():
        return None
    expected_prefix = f"{_part(base_sha or 'initial', maximum=64)}_{_part(target_sha, maximum=64)}_"
    for candidate in sorted(directory.iterdir(), key=lambda item: item.name):
        manifest_path = candidate / "archive-manifest.json"
        if (not candidate.is_dir() or candidate.is_symlink() or not manifest_path.is_file()
                or not candidate.name.startswith(expected_prefix)):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArchiveError("ARCHIVE_MANIFEST_INVALID") from exc
        if (
            manifest.get("schema_version") not in LEGACY_ARCHIVE_SCHEMA_VERSIONS
            or manifest.get("repository_id") != repository_id
            or manifest.get("branch") != branch
            or manifest.get("base_sha") != base_sha
            or manifest.get("target_sha") != target_sha
        ):
            raise ArchiveError("ARCHIVE_MANIFEST_INVALID")
        if analysis_fingerprint is not None and manifest.get("analysis_fingerprint") != analysis_fingerprint:
            continue
        return candidate
    return None


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(report: dict[str, Any], files: dict[str, bytes]) -> bytes:
    run = report["run"]
    payload = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "report_id": report["report_id"],
        "report_sha256": _file_hash(files["report.json"].rstrip(b"\n")),
        "repository_id": run["repository_id"],
        "branch": run["branch"],
        "base_sha": run.get("base_sha"),
        "target_sha": run["target_sha"],
        "analysis_fingerprint": report.get("analysis_fingerprint") or (report.get("versions") or {}).get("analysis_fingerprint"),
        "commit_shas": [item.get("sha") for item in report.get("commits", []) if item.get("sha")],
        "analysis_completed_at": run["analysis_completed_at"],
        "trigger_exclusion_prefix": "db-change-analyzer/reports/",
        "files": [
            {"name": name, "bytes": len(files[name]), "sha256": _file_hash(files[name])}
            for name in sorted(files)
        ],
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _expected_files(report: dict[str, Any], files: dict[str, bytes]) -> dict[str, bytes]:
    required = {"report.json", "report.html", "report.txt", "mail-view.json", "render-manifest.json"}
    allowed = (required, required | {"report-email.html"})
    if set(files) not in allowed:
        raise ArchiveError("ARCHIVE_FILE_SET_INVALID")
    try:
        report_id = report["report_id"]
        if json.loads(files["report.json"].decode("utf-8"))["report_id"] != report_id:
            raise ArchiveError("ARCHIVE_REPORT_ID_MISMATCH")
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("ARCHIVE_REPORT_INVALID") from exc
    return {**files, "archive-manifest.json": _manifest(report, files)}


def _verify_existing(destination: Path, expected: dict[str, bytes]) -> list[Path]:
    paths: list[Path] = []
    for name, data in expected.items():
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ArchiveError("ARCHIVE_COLLISION")
        paths.append(path)
    return paths


def write_report_archive(archive_root: str | Path, report: dict[str, Any], files: dict[str, bytes]) -> list[Path]:
    """Atomically create or idempotently verify a report archive leaf."""
    expected = _expected_files(report, files)
    destination = archive_directory(archive_root, report)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ArchiveError("ARCHIVE_COLLISION")
        return _verify_existing(destination, expected)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = parent / f".archive.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir(mode=0o700)
        for name, data in expected.items():
            path = temporary / name
            path.write_bytes(data)
            os.chmod(path, 0o600)
        try:
            os.replace(temporary, destination)
        except FileExistsError:
            return _verify_existing(destination, expected)
        return [destination / name for name in expected]
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink(missing_ok=True)
            temporary.rmdir()
