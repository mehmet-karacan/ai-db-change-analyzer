"""Validate the local artifact bundle consumed by Jenkins archive steps."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArtifactContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    root: Path
    manifest: dict[str, Any]
    files: dict[str, Path]


_REQUIRED = ("result.json", "report.json", "report.html", "report-email.html", "report.txt", "mail-view.json", "render-manifest.json")


def consume_artifact_directory(root: str | Path) -> ArtifactBundle:
    """Read and validate a report directory without writing or delivering anything."""
    candidate = Path(root)
    if candidate.is_symlink():
        raise ArtifactContractError("ARTIFACT_ROOT_INVALID")
    directory = candidate.resolve(strict=True)
    if not directory.is_dir():
        raise ArtifactContractError("ARTIFACT_ROOT_INVALID")
    files: dict[str, Path] = {}
    for name in _REQUIRED:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ArtifactContractError(f"ARTIFACT_FILE_MISSING:{name}")
        files[name] = path
    try:
        manifest = json.loads(files["render-manifest.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("ARTIFACT_MANIFEST_INVALID") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "render-manifest/2.0":
        raise ArtifactContractError("ARTIFACT_MANIFEST_VERSION")
    try:
        result = json.loads(files["result.json"].read_text(encoding="utf-8"))
        report = json.loads(files["report.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("ARTIFACT_RESULT_INVALID") from exc
    if not isinstance(result, dict) or result.get("schema_version") != "2.0":
        raise ArtifactContractError("ARTIFACT_RESULT_VERSION")
    if result.get("outcome") != "ARTIFACT_READY" or result.get("artifact_ready") is not True or result.get("exit_code") != 0:
        raise ArtifactContractError("ARTIFACT_RESULT_NOT_READY")
    if result.get("delivery_mode") != "jenkins_artifact" or result.get("smtp_attempts") != 0:
        raise ArtifactContractError("ARTIFACT_DELIVERY_PROFILE_MISMATCH")
    if result.get("report_id") != manifest.get("report_id") or report.get("report_id") != manifest.get("report_id"):
        raise ArtifactContractError("ARTIFACT_REPORT_ID_MISMATCH")
    manifest_digest = hashlib.sha256(files["render-manifest.json"].read_bytes()).hexdigest()
    if result.get("artifact_manifest_sha256") != manifest_digest:
        raise ArtifactContractError("ARTIFACT_MANIFEST_HASH_MISMATCH")
    if result.get("email_html_path") != "report-email.html":
        raise ArtifactContractError("ARTIFACT_EMAIL_PATH_MISMATCH")
    _check_hash(files["report.html"], manifest.get("html_sha256"), "html")
    _check_hash(files["report-email.html"], manifest.get("email_html_sha256"), "email_html")
    if manifest.get("html_bytes") != files["report.html"].stat().st_size:
        raise ArtifactContractError("ARTIFACT_HTML_SIZE_MISMATCH")
    if manifest.get("email_html_bytes") != files["report-email.html"].stat().st_size:
        raise ArtifactContractError("ARTIFACT_EMAIL_HTML_SIZE_MISMATCH")
    try:
        view = json.loads(files["mail-view.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("ARTIFACT_VIEW_INVALID") from exc
    if view.get("report_id") != manifest.get("report_id"):
        raise ArtifactContractError("ARTIFACT_REPORT_ID_MISMATCH")
    return ArtifactBundle(directory, manifest, files)


def _check_hash(path: Path, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ArtifactContractError(f"ARTIFACT_{label.upper()}_HASH_MISMATCH")
