from __future__ import annotations

import json

import pytest

from db_change_analyzer.mail_commentary import accept_commentary, build_unit_input, synthetic_probe_input
from db_change_analyzer.validation import ResponseValidationError
from tests.test_v5_adapter import _report
from db_change_analyzer.v5_adapter import build_mail_view


def _input() -> dict:
    report, changes = _report()
    view = build_mail_view(report, changes, analysis_elapsed_ms=100, ai_phase_started_at=None,
                           ai_phase_completed_at=None, ai_phase_elapsed_ms=None, returned_models_by_unit={})
    return build_unit_input(view, view["objects"][0], "unit-test")


def _reply(unit_input: dict, text: str, claim_kind: str = "change_restatement") -> dict:
    fact = unit_input["facts"][0]
    return {
        "schema_version": "mail-commentary/1.1", "report_id": unit_input["report_id"],
        "unit_id": unit_input["unit_id"], "input_digest": unit_input["input_digest"],
        "summary": {"text_tr": text, "fact_ids": [fact["fact_id"]],
                    "evidence_ids": list(dict.fromkeys(fact["before"]["evidence_ids"] + fact["after"]["evidence_ids"])),
                    "claim_kind": claim_kind},
        "interpretations": [], "uncertainties": [], "recommended_checks": [],
    }


def test_synthetic_smoke_input_uses_v5_contract() -> None:
    unit_input = synthetic_probe_input()
    fact = unit_input["facts"][0]
    reply = _reply(unit_input, f"Git kaynağında {fact['subject_name']} değeri 1 iken 2 oldu.")
    _, comments = accept_commentary(json.dumps(reply), unit_input)
    assert unit_input["synthetic"] is True
    assert comments[0]["gate"] == "accepted"


def test_exact_fact_restatement_accepted_and_free_interpretation_withheld() -> None:
    unit_input = _input()
    fact = unit_input["facts"][0]
    text = f"Git kaynağında {fact['subject_name']} değeri {fact['before']['value']} iken {fact['after']['value']} oldu."
    response, comments = accept_commentary(json.dumps(_reply(unit_input, text)), unit_input)
    assert response["input_digest"] == unit_input["input_digest"]
    assert len(comments) == 1 and comments[0]["acceptance_method"] == "deterministic_rule"
    _, comments = accept_commentary(json.dumps(_reply(unit_input, "Bu değişiklik risklidir.")), unit_input)
    assert comments == []


@pytest.mark.parametrize("mutation", ["digest", "unknown_fact", "markup", "extra_field", "disallowed_claim", "pii"])
def test_binding_schema_and_markup_fail_closed(mutation: str) -> None:
    unit_input = _input()
    reply = _reply(unit_input, "Kaynakta değişiklik görüldü.")
    if mutation == "digest":
        reply["input_digest"] = "0" * 64
    elif mutation == "unknown_fact":
        reply["summary"]["fact_ids"] = ["unknown-fact"]
    elif mutation == "markup":
        reply["summary"]["text_tr"] = "https://example.test/report"
    elif mutation == "disallowed_claim":
        reply["summary"]["claim_kind"] = "source_interpretation"
    elif mutation == "pii":
        reply["summary"]["text_tr"] = "Kişi adresi test@example.com olabilir."
    else:
        reply["risk_score"] = 9
    with pytest.raises(ResponseValidationError):
        accept_commentary(json.dumps(reply), unit_input)
