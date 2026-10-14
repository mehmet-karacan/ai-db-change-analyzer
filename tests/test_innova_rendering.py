import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db_change_analyzer.innova_rendering import (
    InnovaRenderError,
    _new_analysis_context,
    _format_analysis_duration,
    build_innova_render_manifest,
    render_innova_view,
)
from db_change_analyzer.cli import _extract_persisted_html
from db_change_analyzer.v5_rendering import _PROFILES, _context, validate_mail_view


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
    assert _extract_persisted_html(rendered.mime) == rendered.email_html


def test_innova_renderer_links_logo_to_official_corporate_identity_page() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<logo-link@example.test>",
        date=datetime.now(UTC),
    )
    html = rendered.email_html.decode("utf-8")

    assert 'https://www.innova.com.tr/kurumsal-kimlik' in html
    assert "İnnova kurumsal kimlik" in html


@pytest.mark.parametrize(
    ("elapsed_ms", "expected"),
    [
        (None, "Kaydedilmedi"),
        (0, "0 saniye"),
        (5_000, "5 saniye"),
        (500, "1 saniye"),
        (60_000, "1 dakika"),
        (65_000, "1 dakika 5 saniye"),
        (100_973, "1 dakika 41 saniye"),
        (59_500, "1 dakika"),
        (3_600_000, "1 saat"),
        (3_661_000, "1 saat 1 dakika 1 saniye"),
    ],
)
def test_innova_renderer_formats_analysis_duration(elapsed_ms: int | None, expected: str) -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["analysis"]["elapsed_ms"] = elapsed_ms
    validate_mail_view(view)

    assert _format_analysis_duration(elapsed_ms) == expected

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id=f"<duration-{elapsed_ms}@example.test>",
        date=datetime.now(UTC),
    )

    assert expected.encode("utf-8") in rendered.html


def test_innova_renderer_displays_full_model_name() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["analysis"]["ai"]["models"][0]["configured_model"] = "Qwen/Qwen3.8-27B-FP8"
    validate_mail_view(view)

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<model-display@example.test>",
        date=datetime.now(UTC),
    )

    assert b"Qwen3.8 27B-IT" in rendered.html
    assert b"Qwen/Qwen3.8-27B-FP8" not in rendered.html
    assert b"Qwen3.8-27B-FP8" not in rendered.html


def test_innova_renderer_orders_summary_metrics_by_importance() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<metric-order@example.test>",
        date=datetime.now(UTC),
    )

    html = rendered.html.decode("utf-8")
    positions = [html.index(marker) for marker in (
        "Toplam Değişiklik", "Risk Seviyesi", "Yorumlayan Model", "Analiz Süresi", "Analiz Zamanı",
    )]
    assert positions == sorted(positions)


def test_innova_renderer_explains_source_change_and_object_counts_separately() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    context = _context(view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(view, context)
    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<count-clarity@example.test>",
        date=datetime.now(UTC),
    )

    assert rendered_context["total_changes"] == "7"
    assert rendered_context["verified"] == "7 nesne değişikliği tespit edildi."
    assert "Toplam Değişiklik".encode("utf-8") in rendered.html


def test_innova_renderer_builds_a_professional_executive_summary() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    context = _context(view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(view, context)
    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<executive-summary@example.test>",
        date=datetime.now(UTC),
    )

    summary = rendered_context["summary"]
    assert "7 veritabanı nesnesinde değişiklik tespit edildi" in summary
    assert "Sıra üreticisi (1)" in summary
    assert "gpu-db" not in summary
    assert "a1b2c3d" not in summary and "e4f5a6b" not in summary
    assert "snapshot" not in summary.lower()
    assert "Ana göstergeler" not in rendered.html.decode("utf-8")


def test_innova_renderer_adds_context_to_impact_and_assessment() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    view["impact_analysis"] = {
        "status": "no_static_dependency_evidence",
        "candidate_count": 0,
        "provided_count": 0,
        "external_sources": {
            "oracle_metadata": "not_connected",
            "application_repositories": "not_connected",
            "jenkins_deployments": "not_run",
            "runtime_usage": "not_connected",
        },
    }
    view["risk_assessment"] = {
        "level": "medium",
        "method": "deterministic_source_rules",
        "confidence": "limited",
        "basis": {"critical": [], "high": [], "medium": ["TABLE"], "low": [], "unknown": [], "static_dependency_candidates": 0},
        "object_levels": {},
        "limitations": ["Canlı metadata ve çalışma zamanı kullanımı doğrulanmadı."],
    }
    validate_mail_view(view)

    rendered = render_innova_view(
        view,
        sender="sender@example.test",
        recipients=["recipient@example.test"],
        message_id="<impact-commentary@example.test>",
        date=datetime.now(UTC),
    )
    html = rendered.html.decode("utf-8")

    assert "Bu sonuç, nesnelerin kullanılmadığını kanıtlamaz" in html
    assert "Genel risk seviyesi Orta" in html
    assert "Dağıtım öncesinde" in html


def test_innova_renderer_groups_sequence_start_changes_by_direction() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/03-sequence-only.view.json").read_text(encoding="utf-8"))
    sequence = view["objects"][0]
    view["objects"] = []
    for index in range(51):
        item = deepcopy(sequence)
        item["object_id"] = f"obj-seq-{index:03d}"
        item["identity"]["name"] = f"SEQ_{index:03d}"
        item["facts"][0]["fact_id"] = f"fact-seq-{index:03d}"
        item["facts"][0]["after"]["value"] = str(15_480 + index)
        view["objects"].append(item)

    context = _context(view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(view, context)
    sequence_rows = [row for row in rendered_context["diff_rows"] if row["tur"] == "SEQUENCE"]

    assert sequence_rows == [{
        "tur": "SEQUENCE",
        "nesne": "51 sequence",
        "degisiklik": "İleri gitti",
        "aciklama": "51 sequence tek satırda toplandı.",
    }]


def test_innova_renderer_keeps_non_sequence_objects_visible_with_aggregated_sequences() -> None:
    sequence_view = json.loads((ROOT / "tests/fixtures/v5/03-sequence-only.view.json").read_text(encoding="utf-8"))
    showcase_view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    sequence = sequence_view["objects"][0]
    table = next(item for item in showcase_view["objects"] if item["identity"]["object_type"] == "TABLE")

    objects = []
    evidence_registry = []
    for index in range(45):
        item = deepcopy(sequence)
        item["object_id"] = f"obj-seq-{index:03d}"
        item["identity"]["name"] = f"SEQ_{index:03d}"
        item["facts"][0]["fact_id"] = f"fact-seq-{index:03d}"
        item["facts"][0]["after"]["value"] = str(15_480 + index)
        objects.append(item)
        for evidence in sequence_view["evidence_registry"]:
            copied = deepcopy(evidence)
            copied["evidence_id"] = copied["evidence_id"].replace("ev-004", f"ev-seq-{index:03d}")
            copied["object_id"] = item["object_id"] if copied["object_id"] else None
            evidence_registry.append(copied)

    objects.append(table)
    evidence_registry.extend(showcase_view["evidence_registry"])
    sequence_view["objects"] = objects
    sequence_view["evidence_registry"] = evidence_registry
    sequence_view["changed_files"] = 46

    context = _context(sequence_view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(sequence_view, context)
    rows = rendered_context["diff_rows"]

    assert {row["tur"] for row in rows} == {"SEQUENCE", "TABLE"}
    assert next(row for row in rows if row["tur"] == "SEQUENCE")["nesne"] == "45 sequence"
    assert next(row for row in rows if row["tur"] == "TABLE")["nesne"] == "GPU_USER.CUSTOMER"


def test_innova_renderer_orders_detail_rows_by_object_priority() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/01-v5-showcase.view.json").read_text(encoding="utf-8"))
    view["schema_version"] = "mail-view/3.0"
    view["template_version"] = "innova-db-report/1.0"
    validate_mail_view(view)

    context = _context(view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(view, context)

    assert [row["tur"] for row in rendered_context["diff_rows"]] == [
        "TABLE", "TABLE", "VIEW", "PACKAGE_BODY", "PACKAGE_SPEC", "INDEX", "SEQUENCE",
    ]


def test_innova_renderer_orders_sequence_subtypes_with_backward_first() -> None:
    view = json.loads((ROOT / "tests/fixtures/v5/03-sequence-only.view.json").read_text(encoding="utf-8"))
    sequence = view["objects"][0]
    forward = deepcopy(sequence)
    forward["object_id"] = "obj-seq-forward"
    forward["identity"]["name"] = "SEQ_FORWARD"
    forward["facts"][0]["fact_id"] = "fact-seq-forward"
    forward["facts"][0]["after"]["value"] = "15481"
    backward = deepcopy(sequence)
    backward["object_id"] = "obj-seq-backward"
    backward["identity"]["name"] = "SEQ_BACKWARD"
    backward["facts"][0]["fact_id"] = "fact-seq-backward"
    backward["facts"][0]["after"]["value"] = "15199"
    view["objects"] = [forward, backward]

    context = _context(view, _PROFILES[0], "")
    rendered_context = _new_analysis_context(view, context)

    assert [row["degisiklik"] for row in rendered_context["diff_rows"]] == [
        "Geri gitti", "İleri gitti",
    ]


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

    assert "YZ Destekli Teknik Değerlendirme".encode("utf-8") in rendered.html
    assert b"Yorumlayan Model" in rendered.html
    assert b"AI" in rendered.html
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
    assert "Risk Seviyesi" in html
    assert "Yüksek" in html and "3 statik bağımlılık adayı bulundu" in html
    assert "Etki ve Değerlendirme" in html
    assert "Değişiklik Detayları" in html


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
        "Özet", "Etki ve Değerlendirme", "Değişiklik Detayları", "Kaynak ve İzlenebilirlik",
    )]
    assert positions == sorted(positions)
    assert "Risk Seviyesi" in html


@pytest.mark.parametrize("level,label,color", [
    ("critical", "Kritik", "#8F1D2C"), ("high", "Yüksek", "#A44A16"),
    ("medium", "Orta", "#8A6500"), ("low", "Düşük", "#2F6B4F"),
    ("unknown", "Belirsiz", "#596574"),
])
def test_innova_renderer_has_distinct_risk_palette(level: str, label: str, color: str) -> None:
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
    assert f"color:{color}" in html
