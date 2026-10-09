import json
import hashlib
from pathlib import Path

import pytest

from db_change_analyzer.artifact_consumer import ArtifactContractError, consume_artifact_directory


def test_artifact_consumer_accepts_hash_bound_bundle(tmp_path: Path) -> None:
    html = b"<html>standalone</html>"
    email_html = b"<html>email</html>"
    report_id = "11111111-1111-4111-8111-111111111111"
    (tmp_path / "report.json").write_text(json.dumps({"report_id": report_id}), encoding="utf-8")
    (tmp_path / "report.html").write_bytes(html)
    (tmp_path / "report-email.html").write_bytes(email_html)
    (tmp_path / "report.txt").write_text("report", encoding="utf-8")
    (tmp_path / "mail-view.json").write_text(json.dumps({"report_id": report_id}), encoding="utf-8")
    manifest = {
        "schema_version": "render-manifest/2.0", "report_id": report_id,
        "html_sha256": hashlib.sha256(html).hexdigest(), "email_html_sha256": hashlib.sha256(email_html).hexdigest(),
        "html_bytes": len(html), "email_html_bytes": len(email_html),
        "source_report_sha256": "a" * 64,
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    (tmp_path / "render-manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "result.json").write_text(json.dumps({
        "schema_version": "2.0", "outcome": "ARTIFACT_READY", "artifact_ready": True, "exit_code": 0,
        "delivery_mode": "jenkins_artifact", "smtp_attempts": 0, "report_id": report_id,
        "artifact_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "email_html_path": "report-email.html",
    }), encoding="utf-8")

    bundle = consume_artifact_directory(tmp_path)

    assert bundle.manifest["report_id"] == report_id
    assert bundle.files["report-email.html"] == tmp_path / "report-email.html"


def test_artifact_consumer_rejects_email_hash_mismatch(tmp_path: Path) -> None:
    for name in ("report.json", "report.html", "report-email.html", "report.txt", "mail-view.json"):
        (tmp_path / name).write_text(json.dumps({"report_id": "report-1"}), encoding="utf-8")
    manifest = {
        "schema_version": "render-manifest/2.0", "report_id": "report-1",
        "html_sha256": hashlib.sha256((tmp_path / "report.html").read_bytes()).hexdigest(),
        "email_html_sha256": "0" * 64,
        "html_bytes": (tmp_path / "report.html").stat().st_size,
        "email_html_bytes": (tmp_path / "report-email.html").stat().st_size,
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    (tmp_path / "render-manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "result.json").write_text(json.dumps({
        "schema_version": "2.0", "outcome": "ARTIFACT_READY", "artifact_ready": True, "exit_code": 0,
        "delivery_mode": "jenkins_artifact", "smtp_attempts": 0, "report_id": "report-1",
        "artifact_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "email_html_path": "report-email.html",
    }), encoding="utf-8")

    with pytest.raises(ArtifactContractError, match="ARTIFACT_EMAIL_HTML_HASH_MISMATCH"):
        consume_artifact_directory(tmp_path)


def test_artifact_consumer_requires_new_manifest_bundle(tmp_path: Path) -> None:
    (tmp_path / "report.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ArtifactContractError, match="ARTIFACT_FILE_MISSING"):
        consume_artifact_directory(tmp_path)


def test_artifact_consumer_rejects_invalid_root_manifest(tmp_path: Path) -> None:
    for name in ("result.json", "report.json", "report.html", "report-email.html", "report.txt", "mail-view.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "render-manifest.json").write_text(json.dumps({"schema_version": "render-manifest/1.0"}), encoding="utf-8")
    with pytest.raises(ArtifactContractError, match="ARTIFACT_MANIFEST_VERSION"):
        consume_artifact_directory(tmp_path)
