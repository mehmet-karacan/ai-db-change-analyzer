import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db_change_analyzer.innova_rendering import InnovaRenderError, build_innova_render_manifest, render_innova_view
from db_change_analyzer.v5_rendering import validate_mail_view


ROOT = Path(__file__).resolve().parents[1]


def test_innova_renderer_separates_standalone_html_from_cid_email() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<innova-render@example.test>",
        date=datetime.now(UTC),
    )
    manifest = build_innova_render_manifest(view, rendered, source_report_sha256="a" * 64)

    assert b"cid:innova-logo" not in rendered.html
    assert b"cid:innova-logo" in rendered.email_html
    assert b"innova-logo" in rendered.mime
    assert manifest["schema_version"] == "render-manifest/2.0"
    assert manifest["email_html_sha256"]


def test_innova_renderer_fails_closed_when_approved_logo_is_missing(tmp_path: Path, monkeypatch) -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/02-no-change.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    monkeypatch.setattr("db_change_analyzer.innova_rendering._LOGO", tmp_path / "missing-logo.png")

    with pytest.raises(InnovaRenderError, match="APPROVED_LOGO_MISSING"):
        render_innova_view(
            view,
            sender="sender@example.test",
            recipients=["recipient@example.test"],
            message_id="<missing-logo@example.test>",
            date=datetime.now(UTC),
        )


def test_innova_renderer_fails_closed_when_approved_template_is_missing(tmp_path: Path, monkeypatch) -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/02-no-change.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    monkeypatch.setattr("db_change_analyzer.innova_rendering._TEMPLATES", tmp_path / "missing-templates")

    with pytest.raises(InnovaRenderError, match="APPROVED_TEMPLATE_MISSING"):
        render_innova_view(
            view,
            sender="sender@example.test",
            recipients=["recipient@example.test"],
            message_id="<missing-template@example.test>",
            date=datetime.now(UTC),
        )


def test_innova_renderer_escapes_source_text_and_keeps_profiles_semantically_equal() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["objects"][0]["identity"]["name"] = "A<&long-nesne-" + ("x" * 80)
    view["objects"][0]["ai_comments"][0]["text_tr"] = "Model {{ 7*7 }} $BUILD_URL & not a template"
    validate_mail_view(view)
    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<escaping@example.test>",
        date=datetime.now(UTC),
    )

    assert b"&lt;&amp;" in rendered.html
    assert b"<script" not in rendered.html.lower()
    normalize = lambda value: re.sub(rb"data:image/png;base64,[A-Za-z0-9+/=]+|cid:innova-logo", b"LOGO", value)
    assert normalize(rendered.html) == normalize(rendered.email_html)
    assert b"A&lt;&amp;long-nesne-" in rendered.email_html
    assert b"{{ 7*7 }}" in rendered.email_html
    assert b"$BUILD_URL" in rendered.email_html


def test_innova_renderer_keeps_accepted_execution_attribution_in_both_profiles() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    execution_id = "execution-001"
    view["objects"][0]["ai_comments"][0]["author_execution_id"] = execution_id
    view["analysis"]["ai"]["executions"] = [{
        "execution_id": execution_id, "unit_id": "unit-001", "attempt": 1, "turn": 1, "generation": 0,
        "requested_route": "https://model.example.test/v1/chat/completions", "requested_model": "model-a",
        "returned_model": "model-a", "started_at": "2026-10-08T08:00:00+00:00",
        "completed_at": "2026-10-08T08:00:01+00:00", "status": "accepted",
        "policy_versions": {"analysis_policy": "1.0;sha256=" + "a" * 64},
        "policy_fingerprint": "b" * 64, "response_schema": "mail-commentary/1.1", "error_code": None,
    }]
    validate_mail_view(view)
    rendered = render_innova_view(
        view, sender="sender@example.test", recipients=["recipient@example.test"],
        message_id="<execution@example.test>", date=datetime.now(UTC),
    )

    assert execution_id.encode() in rendered.html
    assert execution_id.encode() in rendered.email_html
    assert b"requested_route" not in rendered.html


def test_innova_renderer_separates_source_risk_from_operation_colors() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["impact_analysis"] = {
        "status": "static_repository_evidence", "candidate_count": 3, "provided_count": 2,
        "external_sources": {
            "oracle_metadata": "not_connected", "application_repositories": "not_connected",
            "jenkins_deployments": "not_run", "runtime_usage": "not_connected",
        },
    }
    view["risk_assessment"] = {
        "level": "high", "method": "deterministic_source_rules", "confidence": "limited",
        "basis": {"critical": [], "high": ["TABLE"], "medium": [], "low": [], "unknown": [], "static_dependency_candidates": 3},
        "object_levels": {},
        "limitations": ["Canlı metadata ve çalışma zamanı kullanımı doğrulanmadı."],
    }
    view["objects"][0]["risk"] = {
        "level": "high", "confidence": "limited", "reason": "high_source_rule",
    }
    validate_mail_view(view)
    rendered = render_innova_view(
        view, sender="sender@example.test", recipients=["recipient@example.test"],
        message_id="<risk-summary@example.test>", date=datetime.now(UTC),
    )

    html = rendered.html.decode("utf-8")
    assert "KAYNAK TABANLI RİSK SINIFI" in html
    assert "Yüksek" in html and "Repository içi statik kanıt bulundu" in html
    assert "Jenkins deployment geçmişi" in html and "Çalıştırılmadı" in html
    assert "EKLENEN" in html and "GÜNCELLENEN" in html
    assert "RİSK DÜZEYİ" in html and "YÜKSEK" in html and "background:#A62C24" in html


def test_innova_renderer_keeps_approved_section_order_and_object_risk_badges() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["objects"][0]["risk"] = {
        "level": "high", "confidence": "limited", "reason": "high_source_rule",
    }
    validate_mail_view(view)
    rendered = render_innova_view(
        view, sender="sender@example.test", recipients=["recipient@example.test"],
        message_id="<section-order@example.test>", date=datetime.now(UTC),
    )

    html = rendered.html.decode("utf-8")
    positions = [html.index(marker) for marker in (
        "01 / YÖNETİCİ ÖZETİ", "02 / DEĞİŞİKLİKLER", "03 / ETKİ VE KONTROL", "04 / KAPSAM",
    )]
    assert positions == sorted(positions)
    assert "RİSK DÜZEYİ" in html and "YÜKSEK" in html
    assert "background:#A62C24" in html


@pytest.mark.parametrize("level,label,background", [
    ("critical", "Kritik", "#FBE8EC"), ("high", "Yüksek", "#FFF0E5"),
    ("medium", "Orta", "#FFF7D6"), ("low", "Düşük", "#EAF5EE"),
    ("unknown", "Belirsiz", "#F3F6F8"),
])
def test_innova_renderer_has_distinct_risk_palette(level: str, label: str, background: str) -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["risk_assessment"] = {
        "level": level, "method": "deterministic_source_rules", "confidence": "limited",
        "basis": {"critical": [], "high": [], "medium": [], "low": [], "unknown": [], "static_dependency_candidates": 0},
        "object_levels": {},
        "limitations": ["Kaynak tabanlı sınıflandırmadır."],
    }
    view["impact_analysis"] = {
        "status": "no_static_dependency_evidence", "candidate_count": 0, "provided_count": 0,
        "external_sources": {"oracle_metadata": "not_connected", "application_repositories": "not_connected",
                              "jenkins_deployments": "not_run", "runtime_usage": "not_connected"},
    }
    validate_mail_view(view)
    rendered = render_innova_view(
        deepcopy(view), sender="sender@example.test", recipients=["recipient@example.test"],
        message_id=f"<risk-{level}@example.test>", date=datetime.now(UTC),
    )
    html = rendered.html.decode("utf-8")
    assert f">{label}<" in html
    assert f"background:{background}" in html
