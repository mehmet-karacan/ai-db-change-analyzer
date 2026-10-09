"""Run the same source-review corpus under two runtime policy versions.

The command produces a machine-readable A/B delta.  Mock mode validates the
corpus, policy loading and comparison plumbing only; it is never a model
quality result.  Live mode remains explicitly gated by --allow-ai and a
verified route in the evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "tools" / "evaluate_source_review.py"


def _run_eval(args: argparse.Namespace, policy_root: str, output: Path) -> tuple[int, dict[str, Any]]:
    command = [
        sys.executable, str(EVALUATOR), "--config", str(Path(args.config).resolve()),
        "--corpus", str(Path(args.corpus).resolve()), "--output", str(output),
        "--mode", args.mode, "--repetitions", str(args.repetitions),
        "--policy-root", str(Path(policy_root).resolve()),
    ]
    if args.allow_ai:
        command.append("--allow-ai")
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if not output.is_file():
        raise RuntimeError(f"evaluation did not produce output: {policy_root}: {completed.stderr[-500:]}")
    return completed.returncode, json.loads(output.read_text(encoding="utf-8"))


def _case_map(result: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(item["case_id"], int(item["repetition"])): item for item in result.get("cases", [])}


def compare_results(baseline: dict[str, Any], candidate: dict[str, Any], *, baseline_exit: int, candidate_exit: int) -> dict[str, Any]:
    if baseline.get("corpus_sha256") != candidate.get("corpus_sha256"):
        raise ValueError("EVAL_CORPUS_MISMATCH")
    left, right = _case_map(baseline), _case_map(candidate)
    if set(left) != set(right):
        raise ValueError("EVAL_CASE_SET_MISMATCH")
    changed: list[dict[str, Any]] = []
    for key in sorted(left):
        before, after = left[key], right[key]
        fields = ("accepted", "rubric_pass", "finding_count", "target_evidence_pass", "severity_pass", "failure")
        delta = {field: {"baseline": before.get(field), "candidate": after.get(field)}
                 for field in fields if before.get(field) != after.get(field)}
        if delta:
            changed.append({"case_id": key[0], "repetition": key[1], "changes": delta})
    status = "MOCK_ONLY" if baseline.get("mode") == "mock" else (
        "PASS" if baseline.get("status") == "PASS" and candidate.get("status") == "PASS" and not changed else "FAIL"
    )
    return {
        "schema_version": "review-eval-comparison/1.0",
        "mode": baseline.get("mode"), "status": status,
        "metrics_status": "not_a_model_quality_pass" if baseline.get("mode") == "mock" else "measured_live_route",
        "rubric_version": baseline.get("rubric_version"), "corpus_sha256": baseline.get("corpus_sha256"),
        "baseline_policy": baseline.get("policy"), "candidate_policy": candidate.get("policy"),
        "baseline_exit_code": baseline_exit, "candidate_exit_code": candidate_exit,
        "baseline_passed_cases": baseline.get("passed_cases"), "candidate_passed_cases": candidate.get("passed_cases"),
        "passed_case_delta": (candidate.get("passed_cases", 0) - baseline.get("passed_cases", 0)),
        "changed_cases": changed, "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-policy-root", required=True)
    parser.add_argument("--candidate-policy-root", required=True)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--allow-ai", action="store_true")
    args = parser.parse_args(argv)
    if args.repetitions < 1 or args.repetitions > 20:
        parser.error("--repetitions must be between 1 and 20")
    corpus_path = Path(args.corpus).resolve(strict=True)
    corpus_sha256 = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="db-change-review-compare-") as temp:
        temp_root = Path(temp)
        baseline_exit, baseline = _run_eval(args, args.baseline_policy_root, temp_root / "baseline.json")
        candidate_exit, candidate = _run_eval(args, args.candidate_policy_root, temp_root / "candidate.json")
    if baseline.get("corpus_sha256") != corpus_sha256 or candidate.get("corpus_sha256") != corpus_sha256:
        parser.error("evaluation output corpus digest mismatch")
    comparison = compare_results(baseline, candidate, baseline_exit=baseline_exit, candidate_exit=candidate_exit)
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": comparison["status"], "changed_cases": len(comparison["changed_cases"]), "passed_case_delta": comparison["passed_case_delta"]}, ensure_ascii=False, separators=(",", ":")))
    return 0 if comparison["status"] == "MOCK_ONLY" or comparison["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
