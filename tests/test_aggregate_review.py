import json

import pytest

from db_change_analyzer.config import load_config
from db_change_analyzer.aggregate_review import (
    AggregateReviewError,
    accept_aggregate_review,
    build_aggregate_review_input,
)


def _objects(count=11):
    return [
        {
            "object_type": "SEQUENCE" if index % 2 == 0 else "TABLE",
            "object_name": f"S.OBJ_{index}",
            "operation": "modified" if index % 3 else "added",
            "risk_level": "low" if index % 2 else "medium",
            "priority": index + 1,
            "deterministic_summary": "1 kaynak alanı farkı kaydedildi.",
            "categories": ["sequence_observed_value"] if index % 2 == 0 else ["contract"],
        }
        for index in range(count)
    ]


def test_aggregate_threshold_is_explicit_in_artifact_config() -> None:
    config = load_config("config/gpu.artifact.example.toml")
    assert config.analysis.max_objects_for_individual_ai_review == 10


def test_aggregate_input_is_compact_and_contains_no_source_payload() -> None:
    payload = build_aggregate_review_input(
        report_id="report-1", unit_id="aggregate-1", threshold=10,
        object_summaries=_objects(),
    )

    assert payload["schema_version"] == "aggregate-review-input/1.0"
    assert payload["summary"]["object_count"] == 11
    assert payload["summary"]["object_type_counts"] == {"SEQUENCE": 6, "TABLE": 5}
    assert payload["summary"]["operation_counts"] == {"added": 4, "modified": 7, "removed": 0}
    assert payload["summary"]["risk_counts"] == {"critical": 0, "high": 0, "medium": 6, "low": 5, "unknown": 0}
    assert payload["summary"]["priority_objects"]
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "select " not in serialized
    assert "<script" not in serialized
    assert "http://" not in serialized and "https://" not in serialized
    assert "raw_sql" not in payload
    assert "source_snippet" not in payload


def test_aggregate_response_rejects_unsafe_or_unknown_evidence() -> None:
    payload = build_aggregate_review_input(
        report_id="report-1", unit_id="aggregate-1", threshold=10,
        object_summaries=_objects(),
    )
    evidence_id = payload["evidence_registry"][0]["evidence_id"]
    valid = {
        "schema_version": "aggregate-review/1.0",
        "unit_id": "aggregate-1",
        "input_digest": payload["input_digest"],
        "commentary_tr": "Değişikliklerin çoğu kaynak sözleşmesini etkiliyor; dağıtım öncesi bağımlılık kontrolleri yapılmalı.",
        "evidence_ids": [evidence_id],
        "limitations": ["Değerlendirme yalnız sağlanan deterministik özetle sınırlıdır."],
    }
    accepted = accept_aggregate_review(json.dumps(valid, ensure_ascii=False), payload)
    assert accepted["commentary_tr"].startswith("Değişikliklerin çoğu")

    invalid = dict(valid, commentary_tr="<script>alert(1)</script>")
    with pytest.raises(AggregateReviewError, match="AGGREGATE_REVIEW_UNSAFE_TEXT"):
        accept_aggregate_review(json.dumps(invalid, ensure_ascii=False), payload)

    invalid = dict(valid, evidence_ids=["not-in-input"])
    with pytest.raises(AggregateReviewError, match="AGGREGATE_REVIEW_UNKNOWN_EVIDENCE"):
        accept_aggregate_review(json.dumps(invalid, ensure_ascii=False), payload)


def test_aggregate_response_rejects_sql_url_and_live_runtime_claims() -> None:
    payload = build_aggregate_review_input(
        report_id="report-1", unit_id="aggregate-1", threshold=10,
        object_summaries=_objects(),
    )
    evidence_id = payload["evidence_registry"][0]["evidence_id"]
    base = {
        "schema_version": "aggregate-review/1.0",
        "unit_id": "aggregate-1",
        "input_digest": payload["input_digest"],
        "commentary_tr": "Tamam.",
        "evidence_ids": [evidence_id],
        "limitations": [],
    }
    for text in ("SELECT * FROM USERS", "Ayrıntı için https://example.test", "Canlı veritabanı bu değişikliği kullanıyor."):
        with pytest.raises(AggregateReviewError):
            accept_aggregate_review(json.dumps(dict(base, commentary_tr=text), ensure_ascii=False), payload)
