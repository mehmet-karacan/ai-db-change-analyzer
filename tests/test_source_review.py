from __future__ import annotations

from db_change_analyzer.oracle.changes import Change, ChangeSet, Value
from db_change_analyzer.source_review import build_source_review_input, source_review_evidence_ids


def _change_set() -> ChangeSet:
    return ChangeSet(
        object_key="GPU_USER|TABLE|T",
        object_type="TABLE",
        operation="modified",
        facts=(Change(
            fact_id="fact-nullable", taxonomy_id="table.column.nullable", component_path=("ID",),
            action="modified", category="observed_value",
            before=Value("present", "true", ("ev-base",)),
            after=Value("present", "false", ("ev-target",)),
            context_only=False, verification="verified",
        ),),
        unsupported_families=(), diagnostics=(),
    )


def _evidence(evidence_id: str, revision: str, snippet: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id, "revision": revision, "path_display": "gpu_user/t.sql",
        "path_b64": "Z3B1X3VzZXIvdC5zcWw", "blob_oid": "a" * 40,
        "start_line": 1, "end_line": 2, "fragment_sha256": "b" * 64,
        "snippet": snippet,
    }


def test_source_review_input_binds_facts_receipts_and_redacts_secret() -> None:
    payload = build_source_review_input(
        report_id="report-1", unit_id="unit-1", changes=_change_set(),
        old_evidence=[_evidence("ev-base", "a" * 40, "CREATE TABLE T (ID NUMBER);\n")],
        new_evidence=[_evidence("ev-target", "b" * 40, "CREATE TABLE T (ID NUMBER NOT NULL);\nsecret=token")],
        research_receipts=[{
            "tool_call_id": "call-1", "tool_name": "find_references", "revision": "b" * 40,
            "status": "ok", "evidence_ids": ["ev-ref"],
            "items": [{"evidence_id": "ev-ref", "text": "SELECT ID FROM T"}],
        }],
        base_sha="a" * 40, target_sha="b" * 40,
        extra_secret_patterns=[r"secret=\w+"],
    )

    assert payload["schema_version"] == "source-review-input/1.0"
    assert payload["input_digest"]
    target = next(item for item in payload["evidence_registry"] if item["evidence_id"] == "ev-target")
    assert target["redacted"] is True
    assert target["snippet"] == "[GİZLENDİ]"
    assert source_review_evidence_ids(payload) == {"ev-base", "ev-target", "ev-ref"}


def test_source_review_input_digest_ignores_runtime_receipt_timing() -> None:
    common = {
        "tool_call_id": "call-1", "tool_name": "find_references", "revision": "b" * 40,
        "status": "ok", "evidence_ids": ["ev-ref"], "items": [{"evidence_id": "ev-ref"}],
    }
    first = build_source_review_input(
        report_id="report-1", unit_id="unit-1", changes=_change_set(),
        old_evidence=[_evidence("ev-base", "a" * 40, "CREATE TABLE T (ID NUMBER);\n")],
        new_evidence=[_evidence("ev-target", "b" * 40, "CREATE TABLE T (ID NUMBER NOT NULL);\n")],
        research_receipts=[{**common, "elapsed_ms": 1}], base_sha="a" * 40, target_sha="b" * 40,
    )
    second = build_source_review_input(
        report_id="report-1", unit_id="unit-1", changes=_change_set(),
        old_evidence=[_evidence("ev-base", "a" * 40, "CREATE TABLE T (ID NUMBER);\n")],
        new_evidence=[_evidence("ev-target", "b" * 40, "CREATE TABLE T (ID NUMBER NOT NULL);\n")],
        research_receipts=[{**common, "elapsed_ms": 999}], base_sha="a" * 40, target_sha="b" * 40,
    )

    assert first["research_receipts"][0]["elapsed_ms"] == 1
    assert second["research_receipts"][0]["elapsed_ms"] == 999
    assert first["input_digest"] == second["input_digest"]
