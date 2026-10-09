from __future__ import annotations

import json

import pytest

from db_change_analyzer.review_contracts import ReviewContractError, bind_source_review, validate_source_review


def _payload(**overrides):
    value = {
        "schema_version": "source-review/1.0",
        "unit_id": "unit-1",
        "input_digest": "a" * 64,
        "explanations": [{
            "explanation_id": "exp-1", "kind": "change", "text_tr": "Kolon tanımı değişti.",
            "evidence_ids": ["ev-1"],
        }],
        "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
    }
    value.update(overrides)
    return value


def test_source_review_requires_runtime_bindings() -> None:
    result = validate_source_review(
        json.dumps(_payload(), ensure_ascii=False), unit_id="unit-1", input_digest="a" * 64,
        evidence_ids={"ev-1"},
    )
    bound = bind_source_review(result, author_execution_id="exec-1")
    assert bound["explanations"][0]["author_execution_id"] == "exec-1"


@pytest.mark.parametrize("field", ["unit_id", "input_digest"])
def test_source_review_rejects_binding_mismatch(field: str) -> None:
    value = _payload(**{field: "wrong" if field == "unit_id" else "b" * 64})
    with pytest.raises(ReviewContractError, match="SOURCE_REVIEW_BINDING_MISMATCH"):
        validate_source_review(
            json.dumps(value), unit_id="unit-1", input_digest="a" * 64,
            evidence_ids={"ev-1"},
        )


def test_source_review_rejects_unknown_evidence_and_execution() -> None:
    value = _payload(explanations=[{
        "explanation_id": "exp-1", "kind": "impact", "text_tr": "Etki olasılığı var.",
        "evidence_ids": ["ev-missing"],
    }])
    with pytest.raises(ReviewContractError, match="SOURCE_REVIEW_UNKNOWN_EVIDENCE"):
        validate_source_review(
            json.dumps(value), unit_id="unit-1", input_digest="a" * 64,
            evidence_ids={"ev-1"},
        )


def test_source_review_rejects_model_owned_execution_field() -> None:
    value = _payload(explanations=[{
        "explanation_id": "exp-1", "kind": "change", "text_tr": "Kolon tanımı değişti.",
        "evidence_ids": ["ev-1"], "author_execution_id": "model-claimed",
    }])
    with pytest.raises(ReviewContractError, match="SOURCE_REVIEW_INVALID"):
        validate_source_review(
            json.dumps(value), unit_id="unit-1", input_digest="a" * 64,
            evidence_ids={"ev-1"},
        )


@pytest.mark.parametrize("text", [
    "<script>alert(1)</script>",
    "{{ BUILD_URL }}",
    "$BUILD_URL",
    "```groovy\nemailext()\n```",
])
def test_source_review_rejects_unsafe_finding_text(text: str) -> None:
    value = _payload(findings=[{
        "finding_id": "finding-1", "kind": "issue", "category": "security",
        "title_tr": text, "detail_tr": "Kaynak kanıtı ayrıca kontrol edilmelidir.",
        "severity": "medium", "basis": "source_backed_inference", "evidence_ids": ["ev-1"],
        "verification_steps": ["İlgili kaynak çağrılarını kontrol edin."], "execution": "not_run",
    }], conclusion="findings")
    with pytest.raises(ReviewContractError, match="SOURCE_REVIEW_UNSAFE_TEXT"):
        validate_source_review(
            json.dumps(value), unit_id="unit-1", input_digest="a" * 64,
            evidence_ids={"ev-1"},
        )

