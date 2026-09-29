from __future__ import annotations

from datetime import UTC, datetime

from db_change_analyzer.oracle.changes import compare_projections
from db_change_analyzer.oracle.changes import Change, Value
from db_change_analyzer.oracle.projections import project
from db_change_analyzer.oracle.scanner import scan
from db_change_analyzer.v5_adapter import _display_value, _fact, build_mail_view
from db_change_analyzer.v5_rendering import build_render_manifest, render_v5_view


def _projection(sql: str):
    occurrence = scan(sql.encode(), default_schema="S").occurrences[0]
    return project(occurrence, sql, parse_ok=True)


def _report(operation: str = "modified") -> tuple[dict, dict]:
    base, target = "a" * 40, "b" * 40
    old = _projection("CREATE SEQUENCE S.Q START WITH 1 NOCACHE;")
    new = _projection("CREATE SEQUENCE S.Q START WITH 2 NOCACHE;")
    key = new.object_key
    changes = compare_projections(
        old if operation == "modified" else None, new,
        old_evidence_ids=("ev-old",) if operation == "modified" else (),
        new_evidence_ids=("ev-new",),
        old_scope_evidence_ids=("scope-old",) if operation == "added" else (),
    )
    report = {
        "report_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "synthetic": False,
        "run": {
            "repository_id": "gpu-db", "branch": "master", "base_sha": base, "target_sha": target,
            "observed_git_at": "2026-09-29T09:12:08+03:00", "snapshot_captured_at": None,
            "analysis_started_at": "2026-09-29T09:12:08+03:00",
            "analysis_completed_at": "2026-09-29T09:12:31+03:00",
        },
        "quality": "limited",
        "counts": {
            "net_files": 1, "unknown_artifacts": 0, "has_unknown_object_count": False,
            "ai_http_attempts": 0, "ai_units": 0,
        },
        "versions": {"configured_model": "provider/model"},
        "limitations": ["Canlı veritabanı kontrol edilmedi."],
        "evidence_registry": [
            {"evidence_id": "ev-old", "kind": "source", "revision": base, "path_display": "s/q.sql", "start_line": 1, "end_line": 1, "fragment_sha256": "1" * 64},
            {"evidence_id": "ev-new", "kind": "source", "revision": target, "path_display": "s/q.sql", "start_line": 1, "end_line": 1, "fragment_sha256": "2" * 64},
        ] if operation == "modified" else [
            {"evidence_id": "ev-new", "kind": "source", "revision": target, "path_display": "s/q.sql", "start_line": 1, "end_line": 1, "fragment_sha256": "2" * 64},
        ],
        "objects": [{
            "identity": {
                "object_key": key, "namespace": "SCHEMA", "schema_name": "S", "name": "Q",
                "schema_quoted": False, "name_quoted": False, "object_type": "SEQUENCE",
                "identity_confidence": "known",
            },
            "net_operation": operation, "status": "analyzed",
            "categories": ["sequence_observed_value"] if operation == "modified" else ["unknown"],
            "old_evidence_ids": ["ev-old"] if operation == "modified" else [],
            "new_evidence_ids": ["ev-new"], "assessments": [], "diagnostics": [],
        }],
    }
    return report, {key: changes}


def test_adapter_builds_real_schema_view_and_v5_mime_without_ai() -> None:
    report, changes = _report()
    view = build_mail_view(
        report, changes, analysis_elapsed_ms=23_000, ai_phase_started_at=None,
        ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={},
    )
    assert view["analysis"]["elapsed_ms"] == 23_000
    assert view["analysis"]["ai"]["status"] == "not_used"
    assert view["objects"][0]["pattern"] == "start_with_only"
    assert view["objects"][0]["facts"][0]["taxonomy_id"] == "sequence.sequence_property.start_with"
    rendered = render_v5_view(
        view, sender="analyzer@example.invalid", recipients=["review@example.invalid"],
        message_id="<v5@example.invalid>", date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert b"START WITH" in rendered.html
    assert b"AI" in rendered.text
    manifest = build_render_manifest(view, rendered, source_report_sha256="f" * 64)
    assert manifest["html_bytes"] == len(rendered.html)
    assert manifest["mime_sha256"]
    assert manifest["detail_object_ids"] == [view["objects"][0]["object_id"]]


def test_added_object_requires_scope_evidence_and_keeps_other_fields_contextual() -> None:
    report, changes = _report("added")
    view = build_mail_view(
        report, changes, analysis_elapsed_ms=100, ai_phase_started_at=None,
        ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={},
    )
    obj = view["objects"][0]
    assert obj["operation"] == "added"
    assert any(item["kind"] == "snapshot_absence" and item["side"] == "base" for item in view["evidence_registry"])
    assert obj["facts"][0]["before"]["state"] == "absent_in_snapshot"
    assert obj["facts"][0]["after"]["state"] == "present"


def test_mail_projection_hides_unsafe_paths_and_component_names() -> None:
    report, changes = _report()
    report["evidence_registry"][0]["path_display"] = "s/token=verysecretvalue.sql"
    view = build_mail_view(report, changes, analysis_elapsed_ms=100, ai_phase_started_at=None,
                           ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={})
    assert view["evidence_registry"][0]["path"] is None
    assert "s/token=verysecretvalue.sql" not in view["objects"][0]["source_paths"]
    fact, limited = _fact(Change("fact-safe", "table.column.nullable", ("<script>",), "modified", "contract",
                                 Value("present", "NULL", ("ev-old",)), Value("present", "NOT NULL", ("ev-new",)),
                                 False, "verified"), [])
    assert limited and fact["component_path"] == ["GIZLI"]
    assert fact["verification"] == "redacted"


def test_ai_ledger_preserves_multiple_returned_labels_without_version_claim() -> None:
    report, changes = _report()
    report["counts"]["ai_http_attempts"] = 2
    report["counts"]["ai_units"] = 1
    view = build_mail_view(
        report, changes, analysis_elapsed_ms=1000,
        ai_phase_started_at="2026-09-29T09:12:08+03:00",
        ai_phase_completed_at="2026-09-29T09:12:09+03:00", ai_phase_elapsed_ms=1000,
        returned_models_by_unit={"unit-1": {"provider/a", "provider/b"}},
        ai_unit_ids_by_key={report["objects"][0]["identity"]["object_key"]: "unit-1"},
        ai_status_by_key={report["objects"][0]["identity"]["object_key"]: "withheld"},
    )
    ledger = view["analysis"]["ai"]
    assert ledger["status"] == "complete"
    assert ledger["models"][0]["returned_models"] == ["provider/a", "provider/b"]
    assert ledger["models"][0]["resolved_model_version"] is None
    assert ledger["models"][0]["version_verification"] == "reported_unverified"


def test_editionable_value_is_visible_without_exposing_source_sql() -> None:
    displayed, limited = _display_value("common.object.editionable", Value("present", "NONEDITIONABLE", ("ev",)), [])
    assert displayed == {"state": "present", "value": "NONEDITIONABLE", "evidence_ids": ["ev"]}
    assert not limited


def test_source_digest_can_be_displayed_but_raw_source_text_is_redacted() -> None:
    digest, limited = _display_value("common.source.text", Value("present", "sha256:" + "a" * 64, ("ev",)), [])
    assert digest["value"] == "sha256:" + "a" * 64
    assert not limited
    raw, limited = _display_value("common.source.text", Value("present", "SELECT SECRET FROM T", ("ev",)), [])
    assert raw["value"] is None and raw["state"] == "redacted" and limited


def test_simple_overload_signatures_display_but_literal_defaults_are_hidden() -> None:
    safe = '["PROCEDURE X(P NUMBER)","PROCEDURE X(P VARCHAR2)"]'
    displayed, limited = _display_value("package_spec.routine.signature", Value("present", safe, ("ev",)), [])
    assert displayed["value"] == safe and not limited
    unsafe = '["PROCEDURE X(P VARCHAR2 DEFAULT \'credential\')"]'
    hidden, limited = _display_value("package_spec.routine.signature", Value("present", unsafe, ("ev",)), [])
    assert hidden["state"] == "redacted" and hidden["value"] is None and limited
    pii = '["PROCEDURE X(P NUMBER DEFAULT 12345678901)"]'
    hidden, limited = _display_value("package_spec.routine.signature", Value("present", pii, ("ev",)), [])
    assert hidden["state"] == "redacted" and hidden["value"] is None and limited


def test_overload_mail_view_states_pairing_limit() -> None:
    report, changes = _report()
    key = report["objects"][0]["identity"]["object_key"]
    report["objects"][0]["identity"]["object_type"] = "PACKAGE_SPEC"
    report["objects"][0]["categories"] = ["contract"]
    signature = Change("fact-overload", "package_spec.routine.signature", ("PROCEDURE", "X"), "modified", "contract",
                       Value("present", '["PROCEDURE X(P NUMBER)","PROCEDURE X(P VARCHAR2)"]', ("ev-old",)),
                       Value("present", '["PROCEDURE X(P DATE)","PROCEDURE X(P NUMBER)"]', ("ev-new",)),
                       False, "verified")
    changes[key] = type(changes[key])(key, "PACKAGE_SPEC", "modified", (signature,), ("package_spec.parameter.data_type",), ())
    view = build_mail_view(report, changes, analysis_elapsed_ms=100, ai_phase_started_at=None,
                           ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={})
    assert view["objects"][0]["verification"] == "limited"
    assert any("Overload imzaları" in note for note in view["objects"][0]["limitations_tr"])


def test_bounded_index_partition_clause_is_visible_without_literal_payload() -> None:
    safe = "LOCAL (PARTITION P1 TABLESPACE TS1)"
    displayed, limited = _display_value("index.index_property.partitioning", Value("present", safe, ("ev",)), [])
    assert displayed["value"] == safe and not limited
    hidden, limited = _display_value("index.index_property.partitioning",
                                     Value("present", "LOCAL (PARTITION P1 VALUES ('private'))", ("ev",)), [])
    assert hidden["state"] == "redacted" and hidden["value"] is None and limited


def test_join_type_and_identifier_condition_display_without_sql_literals() -> None:
    safe = "LEFT OUTER JOIN T2 ON T2.ID = T1.ID"
    displayed, limited = _display_value("view.query_property.join", Value("present", safe, ("ev",)), [])
    assert displayed["value"] == safe and not limited
    for unsafe in ("LEFT JOIN T2 ON T2.ID = 12345678901", "LEFT JOIN T2 ON T2.NAME = 'private'",
                   "LEFT JOIN T2 ON T2.ID = T1.ID -- note", "LEFT JOIN <script> ON T2.ID = T1.ID"):
        hidden, limited = _display_value("view.query_property.join", Value("present", unsafe, ("ev",)), [])
        assert hidden["state"] == "redacted" and hidden["value"] is None and limited


def test_analytic_window_display_keeps_identifiers_but_hides_literals() -> None:
    safe = "OVER (PARTITION BY D ORDER BY X)"
    displayed, limited = _display_value("view.query_property.analytic", Value("present", safe, ("ev",)), [])
    assert displayed["value"] == safe and not limited
    for unsafe in ("OVER (ORDER BY 1)", "OVER (PARTITION BY 'private')", "OVER (ORDER BY X /* note */)"):
        hidden, limited = _display_value("view.query_property.analytic", Value("present", unsafe, ("ev",)), [])
        assert hidden["state"] == "redacted" and hidden["value"] is None and limited


def test_presentation_only_source_change_is_routed_as_format_only() -> None:
    report, changes = _report()
    key = report["objects"][0]["identity"]["object_key"]
    source_fact = Change("fact-format", "common.source.format", (), "modified", "presentation",
                         Value("present", "sha256:" + "1" * 64, ("ev-old",)),
                         Value("present", "sha256:" + "2" * 64, ("ev-new",)), False, "verified")
    changes[key] = type(changes[key])(key, "SEQUENCE", "modified", (source_fact,), (), ())
    report["objects"][0]["categories"] = ["format_only"]
    view = build_mail_view(report, changes, analysis_elapsed_ms=100, ai_phase_started_at=None,
                           ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={})
    assert view["objects"][0]["pattern"] == "format_only"
    assert view["objects"][0]["facts"][0]["before"]["value"] == "sha256:" + "1" * 64


def test_package_sql_source_fact_is_redacted_before_mail_and_model() -> None:
    value, limited = _display_value(
        "package_body.body_property.sql_statement",
        Value("present", "UPDATE CUSTOMER SET TOKEN = 'private-value'", ("ev",)),
        [],
    )
    assert value == {"state": "redacted", "value": None, "evidence_ids": ["ev"]}
    assert limited
