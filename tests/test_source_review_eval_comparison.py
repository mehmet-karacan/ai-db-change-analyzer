from __future__ import annotations

from tools.compare_source_review_eval import compare_results


def _result(policy: str, passed: int, *, changed: bool = False) -> dict:
    case = {
        "case_id": "case-1", "repetition": 1, "accepted": True,
        "rubric_pass": not changed, "finding_count": 1 if not changed else 0,
        "target_evidence_pass": True, "severity_pass": True,
    }
    return {
        "mode": "mock", "status": "MOCK_ONLY", "rubric_version": "review-eval-rubric/1.0",
        "corpus_sha256": "a" * 64, "policy": {"fingerprint": policy, "documents": {}},
        "passed_cases": passed, "cases": [case],
    }


def test_comparison_binds_same_corpus_and_reports_policy_delta() -> None:
    result = compare_results(
        _result("b" * 64, 1), _result("c" * 64, 0, changed=True),
        baseline_exit=0, candidate_exit=0,
    )

    assert result["status"] == "MOCK_ONLY"
    assert result["passed_case_delta"] == -1
    assert result["baseline_policy"]["fingerprint"] != result["candidate_policy"]["fingerprint"]
    assert result["changed_cases"][0]["case_id"] == "case-1"
