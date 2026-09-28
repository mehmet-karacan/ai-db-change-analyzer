from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db_change_analyzer.reporting import ReportError, build_message, render_report, validate_report


def minimal_report() -> dict:
    now = "2026-09-28T09:00:00+00:00"
    return {
        "schema_version": "1.0",
        "synthetic": True,
        "report_id": str(uuid.uuid4()),
        "supersedes_report_id": None,
        "run": {
            "run_id": str(uuid.uuid4()), "mode": "MANUAL", "repository_id": "gpu-db", "branch": "master",
            "scope_hash": "a" * 64, "epoch": 1, "base_sha": "1" * 40, "target_sha": "2" * 40,
            "planned_at": now, "analysis_started_at": now, "analysis_completed_at": now,
            "analysis_generation": 0, "observed_git_at": now, "snapshot_captured_at": None,
            "actual_db_change_at": None, "originating_analyzer_build": None, "sync_build": None,
        },
        "quality": "limited", "automatic_commit_eligible": True,
        "summary_tr": "Git snapshot'inda bir kaynak degisikligi goruldu.", "overall_ai_risk": "unknown",
        "counts": {
            "net_files": 1, "history_files": 1, "net_known_objects": 0, "history_known_objects": 0,
            "change_events": 0, "ai_units": 0, "ai_http_attempts": 0, "analyzed_objects": 0,
            "limited_objects": 0, "unresolved_objects": 0, "unknown_artifacts": 1,
            "has_unknown_object_count": True,
        },
        "versions": {
            "analyzer": "0.1.0", "grammar_commit": "b434a051c56dcc2a5de0a0f2d86575ed192b59da",
            "parser_adapter": "1.0", "prompt": "db-change-tr-1.0", "unit_response_schema": "1.0",
            "report_schema": "1.0", "configured_model": "openai/codepilot-gpt-oss", "returned_model": None,
            "resolved_model_version": None, "config_digest": "b" * 64,
        },
        "commits": [], "events": [], "artifacts": [], "evidence_registry": [], "objects": [],
        "limitations": ["Repository disi tuketiciler bilinmez."],
    }


def test_report_schema_relations_and_rendering() -> None:
    report = minimal_report()
    validate_report(report)
    rendered = render_report(report)
    assert rendered.sha256 and b"AI DB Analyzer" in rendered.html
    assert rendered.canonical_json.startswith(b'{"artifacts"')


def test_report_rejects_count_and_time_invariants() -> None:
    report = minimal_report()
    report["counts"]["history_known_objects"] = 1
    with pytest.raises(ReportError, match="REPORT_OBJECT_COUNTS_INVALID"):
        validate_report(report)
    report = minimal_report()
    report["run"]["planned_at"] = "2026-09-29T00:00:00+00:00"
    with pytest.raises(ReportError, match="REPORT_TIME_ORDER_INVALID"):
        validate_report(report)


def test_template_autoescapes_ai_text_and_message_is_stable() -> None:
    report = minimal_report()
    report["summary_tr"] = "<script>alert(1)</script>"
    rendered = render_report(report)
    assert b"<script>" not in rendered.html
    assert b"&lt;script&gt;" in rendered.html
    date = datetime(2026, 9, 28, tzinfo=UTC)
    first = build_message(report, rendered, sender="analyzer@example.test", recipients=["dev@example.test"], message_id="<fixed@example.test>", date=date, job_short_name="db", current_build_number=None, max_bytes=150000)
    second = build_message(report, rendered, sender="analyzer@example.test", recipients=["dev@example.test"], message_id="<fixed@example.test>", date=date, job_short_name="db", current_build_number=None, max_bytes=150000)
    assert first.mime_bytes == second.mime_bytes
    with pytest.raises(ReportError, match="MAIL_SIZE_LIMIT"):
        build_message(report, rendered, sender="analyzer@example.test", recipients=["dev@example.test"], message_id="<fixed@example.test>", date=date, job_short_name="db", current_build_number=None, max_bytes=100)


def test_report_link_requires_explicit_https_host() -> None:
    with pytest.raises(ReportError, match="REPORT_URL_NOT_ALLOWED"):
        render_report(minimal_report(), report_url="https://evil.example/report", allowed_link_hosts={"jenkins.example"})
