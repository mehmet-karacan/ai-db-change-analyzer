"""Evaluate source-review responses against a hidden-free synthetic corpus.

The harness deliberately keeps the rubric outside the model payload.  Mock mode
exercises the contract and rubric plumbing only; it never reports model quality.
Live mode requires an already verified model capability record and --allow-ai.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_change_analyzer.config import load_config, read_secret
from db_change_analyzer.git_client import GitClient
from db_change_analyzer.litellm_http import LiteLLMClient
from db_change_analyzer.policy import PolicyBundle, load_policy_bundle
from db_change_analyzer.research_tools import ResearchToolDispatcher
from db_change_analyzer.review_contracts import ReviewContractError, validate_source_review
from db_change_analyzer.source_review import build_source_review_input, source_review_evidence_ids
from db_change_analyzer.oracle.changes import Change, ChangeSet, Value


ROOT = Path(__file__).resolve().parents[1]
RUBRIC_VERSION = "review-eval-rubric/1.0"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    return env


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=_git_env())
    return result.stdout.strip()


def _write_snapshot(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _commit_fixture(case: dict[str, Any], work: Path) -> tuple[GitClient, str, str]:
    repo = work / "repo"
    repo.mkdir()
    _git(repo.parent, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Source Review Evaluator")
    _git(repo, "config", "user.email", "source-review-evaluator@example.invalid")
    _write_snapshot(repo, case["base_files"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "synthetic baseline")
    base_sha = _git(repo, "rev-parse", "HEAD")
    _write_snapshot(repo, case["target_files"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "synthetic change")
    target_sha = _git(repo, "rev-parse", "HEAD")
    cache = work / "cache.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(cache)], check=True, capture_output=True, text=True, env=_git_env())
    client = GitClient(cache, timeout_seconds=30, allow_file_protocol=True)
    client.ensure_cache()
    return client, base_sha, target_sha


def _source_review_input(case: dict[str, Any], work: Path) -> dict[str, Any]:
    git, base_sha, target_sha = _commit_fixture(case, work)
    path = case["source_path"]
    dispatcher = ResearchToolDispatcher(git, {"gpu_user": "GPU_USER", "innova_odi": "INNOVA_ODI"}, scope_hash="synthetic-scope")
    base_read = dispatcher.read_source(tool_call_id="base-read", revision=base_sha, path=path, end_line=200)
    target_read = dispatcher.read_source(tool_call_id="target-read", revision=target_sha, path=path, end_line=200)
    diff = dispatcher.get_diff(tool_call_id="diff", base_revision=base_sha, target_revision=target_sha, path=path)
    references = dispatcher.find_references(tool_call_id="references", revision=target_sha, symbol=case.get("reference_symbol", "T"))
    history = dispatcher.get_history(tool_call_id="history", revision=target_sha, path=path, limit=20)
    old_item = dict(base_read.items[0])
    new_item = dict(target_read.items[0])
    fact_spec = case["fact"]
    change = Change(
        fact_id=fact_spec["fact_id"], taxonomy_id=fact_spec["taxonomy_id"],
        component_path=tuple(fact_spec.get("component_path", [])), action=fact_spec["action"],
        category=fact_spec["category"], before=Value("present", fact_spec["before"], (old_item["evidence_id"],)),
        after=Value("present", fact_spec["after"], (new_item["evidence_id"],)),
        context_only=False, verification="verified",
    )
    changes = ChangeSet(case["object_key"], case["object_type"], case["operation"], (change,), (), ())
    receipts = [diff.as_dict(), base_read.as_dict(), target_read.as_dict(), references.as_dict(), history.as_dict()]
    return build_source_review_input(
        report_id=f"synthetic-{case['id']}", unit_id=f"unit-{case['id']}", changes=changes,
        old_evidence=[old_item], new_evidence=[new_item], research_receipts=receipts,
        base_sha=base_sha, target_sha=target_sha,
    )


def _mock_response(case: dict[str, Any], payload: dict[str, Any]) -> str:
    target_ids = [item["evidence_id"] for item in payload["evidence_registry"] if item.get("side") == "target"]
    evidence_id = target_ids[0] if target_ids else payload["evidence_registry"][0]["evidence_id"]
    response: dict[str, Any] = {
        "schema_version": "source-review/1.0", "unit_id": payload["unit_id"], "input_digest": payload["input_digest"],
        "explanations": [{"explanation_id": "explanation-1", "kind": "change", "text_tr": case["mock"]["explanation"], "evidence_ids": [evidence_id]}],
        "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
    }
    if case["mock"].get("finding"):
        finding = case["mock"]["finding"]
        response["findings"] = [{
            "finding_id": "finding-1", "kind": finding["kind"], "category": finding["category"],
            "title_tr": finding["title"], "detail_tr": finding["detail"], "severity": finding["severity"],
            "basis": finding["basis"], "evidence_ids": [evidence_id],
            "verification_steps": finding.get("verification_steps", ["İlgili kaynak çağrılarını kontrol edin."]), "execution": "not_run",
        }]
        response["conclusion"] = "findings"
    return json.dumps(response, ensure_ascii=False)


def _evaluate(case: dict[str, Any], payload: dict[str, Any], content: str) -> dict[str, Any]:
    try:
        response = validate_source_review(
            content, unit_id=payload["unit_id"], input_digest=payload["input_digest"],
            evidence_ids=source_review_evidence_ids(payload),
        )
    except ReviewContractError as exc:
        return {"accepted": False, "rubric_pass": False, "failure": str(exc)}
    evidence = {item for value in (*response.explanations, *response.findings) for item in value.evidence_ids}
    target_ids = {item["evidence_id"] for item in payload["evidence_registry"] if item.get("side") == "target"}
    must_find = bool(case["rubric"].get("must_have_finding"))
    has_finding = bool(response.findings)
    target_evidence_pass = not case["rubric"].get("must_use_target_source", False) or bool(evidence & target_ids)
    severity_pass = not case["rubric"].get("forbid_high_or_critical", False) or all(item.severity not in {"high", "critical"} for item in response.findings)
    rubric_pass = (has_finding == must_find) and target_evidence_pass and severity_pass
    return {
        "accepted": True, "rubric_pass": rubric_pass, "finding_count": len(response.findings),
        "target_evidence_pass": target_evidence_pass, "severity_pass": severity_pass,
    }


def _run_case(case: dict[str, Any], mode: str, config: Any | None, allow_ai: bool,
              repetition: int, root: Path, policy_bundle: PolicyBundle) -> dict[str, Any]:
    work = root / f"{case['id']}-{repetition}"
    work.mkdir(parents=True)
    payload = _source_review_input(case, work)
    if mode == "mock":
        content = _mock_response(case, payload)
    else:
        if not allow_ai:
            raise RuntimeError("live mode requires --allow-ai")
        if config is None or not (config.model.route_verified and config.model.capabilities_verified):
            raise RuntimeError("live mode requires a verified route and capability record")
        schema = json.loads((ROOT / "schemas" / "source-review.schema.json").read_text(encoding="utf-8"))
        with LiteLLMClient(config.model) as client:
            reply = client.complete(
                api_key=read_secret("LITELLM_API_KEY").get_secret_value(),
                system_message=(policy_bundle.analysis.text + "\n\n" + policy_bundle.language.text + "\n\n" +
                                (ROOT / "src/db_change_analyzer/prompts/source_review.tr.txt").read_text(encoding="utf-8")),
                user_payload=payload, response_schema=schema, output_tokens=config.analysis.output_tokens,
            )
        content = reply.content
    result = _evaluate(case, payload, content)
    result.update({"case_id": case["id"], "repetition": repetition, "input_digest": payload["input_digest"]})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--allow-ai", action="store_true")
    parser.add_argument("--policy-root", default=str(ROOT / "src" / "db_change_analyzer"))
    args = parser.parse_args(argv)
    if args.repetitions < 1 or args.repetitions > 20:
        parser.error("--repetitions must be between 1 and 20")
    corpus_path = Path(args.corpus).resolve(strict=True)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if corpus.get("schema_version") != "review-eval/1.0" or not isinstance(corpus.get("cases"), list):
        parser.error("invalid evaluation corpus")
    config = load_config(args.config) if args.mode == "live" else None
    if args.mode == "live" and not args.allow_ai:
        parser.error("live mode requires --allow-ai")
    try:
        policy_bundle = load_policy_bundle(Path(args.policy_root).resolve(strict=True))
    except Exception as exc:
        parser.error(f"invalid runtime policy: {exc}")
    with tempfile.TemporaryDirectory(prefix="db-change-review-eval-") as temp:
        root = Path(temp)
        cases = []
        for repetition in range(1, args.repetitions + 1):
            for case in corpus["cases"]:
                cases.append(_run_case(case, args.mode, config, args.allow_ai, repetition, root, policy_bundle))
    passed = sum(item["rubric_pass"] for item in cases)
    output = {
        "schema_version": "review-eval-result/1.0", "mode": args.mode, "status": "MOCK_ONLY" if args.mode == "mock" else ("PASS" if passed == len(cases) else "FAIL"),
        "metrics_status": "not_a_model_quality_pass" if args.mode == "mock" else "measured_live_route",
        "rubric_version": RUBRIC_VERSION, "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "config_digest": config.config_digest if config is not None else None,
        "policy": {"fingerprint": policy_bundle.fingerprint, "documents": policy_bundle.metadata()},
        "repetitions": args.repetitions, "cases": cases, "passed_cases": passed, "total_cases": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"mode": args.mode, "status": output["status"], "passed_cases": passed, "total_cases": len(cases)}, ensure_ascii=False, separators=(",", ":")))
    return 0 if (args.mode == "mock" or passed == len(cases)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
