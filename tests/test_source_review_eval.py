from __future__ import annotations

import json
from pathlib import Path

from tools.evaluate_source_review import main


ROOT = Path(__file__).resolve().parents[1]


def test_source_review_eval_mock_is_explicitly_not_a_quality_pass(tmp_path: Path, capsys) -> None:
    output = tmp_path / "eval.json"
    config = ROOT / "config" / "gpu.artifact.example.toml"
    corpus = ROOT / "tests" / "fixtures" / "review_eval" / "corpus.json"
    assert main(["--config", str(config), "--corpus", str(corpus), "--output", str(output), "--mode", "mock", "--repetitions", "2"]) == 0
    capsys.readouterr()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "MOCK_ONLY"
    assert result["metrics_status"] == "not_a_model_quality_pass"
    assert result["passed_cases"] == result["total_cases"] == 20
    assert len(result["policy"]["fingerprint"]) == 64
    assert set(result["policy"]["documents"]) == {"analysis_policy.tr.md", "report_language.tr.md"}
    assert all(item["accepted"] and item["rubric_pass"] for item in result["cases"])
