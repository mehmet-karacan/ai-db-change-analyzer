"""Create a read-only final integrity audit for the local Analyzer checkout."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _run(*command: str) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return {
        "command": list(command), "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-1000:], "stderr_tail": completed.stderr[-1000:],
    }


def _file(path: str) -> dict[str, Any]:
    candidate = ROOT / path
    exists = candidate.is_file()
    return {
        "path": path, "exists": exists,
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest() if exists else None,
    }


def build_audit() -> dict[str, Any]:
    jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    artifact_config = (ROOT / "config" / "gpu.artifact.example.toml").read_text(encoding="utf-8")
    consumer = (ROOT / "jenkins" / "consume_report_artifact.groovy").read_text(encoding="utf-8")
    required = [
        "src/db_change_analyzer/prompts/analysis_policy.tr.md",
        "src/db_change_analyzer/prompts/report_language.tr.md",
        "src/db_change_analyzer/templates/innova_v1/report_email.html.j2",
        "src/db_change_analyzer/templates/innova_v1/report_email.txt.j2",
        "src/db_change_analyzer/assets/innova-logo-approved.png",
        "tools/compare_source_review_eval.py",
        "schemas/review-eval-comparison.schema.json",
        "docs/GERCEK_MODEL_DEGERLENDIRME_FORMU.md",
    ]
    contract = _run(sys.executable, "tools/check_contracts.py")
    release = _run(sys.executable, "tools/check_release_manifest.py")
    git = _run("git", "rev-parse", "HEAD")
    status = _run("git", "status", "--short")
    checks = {
        "required_files": all(item["exists"] for item in map(_file, required)),
        "artifact_config_without_smtp": "[smtp]" not in artifact_config and 'mode = "jenkins_artifact"' in artifact_config,
        "analyzer_jenkinsfile_artifact_pipeline": all(
            marker in jenkinsfile
            for marker in (
                "upstreamProjects: analyzerUpstream",
                "10-ORACLE_DB_DDL_SYNC",
                "run --allow-ai",
                "archiveArtifacts",
                "artifacts: 'out/**'",
                "consume_report_artifact.groovy",
                "mkaracan@innova.com.tr",
            )
        ) and "run --allow-mail" not in jenkinsfile and "ORACLE_DSN" not in jenkinsfile,
        "consumer_has_single_emailext": consumer.count("emailext(") == 1,
        "contracts": contract["exit_code"] == 0,
        "release_manifest": release["exit_code"] == 0,
    }
    return {
        "schema_version": "local-final-audit/1.0",
        "status": "LOCAL_COMPLETE_EXTERNAL_NOT_RUN" if all(checks.values()) else "LOCAL_AUDIT_FAILED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": {"root": str(ROOT), "head": git["stdout_tail"].strip(), "working_tree_dirty": bool(status["stdout_tail"].strip())},
        "checks": checks,
        "required_files": [_file(path) for path in required],
        "commands": {"contracts": contract, "release_manifest": release},
        "external_gates": {
            "bitbucket": "NOT_RUN_AUTH_REQUIRED",
            "jenkins": "NOT_RUN_AUTH_REQUIRED",
            "classic_outlook": "NOT_RUN_SURFACE_UNAVAILABLE",
            "litellm_model_quality": "NOT_RUN_DEFERRED",
        },
    }


def main(argv: list[str] | None = None) -> int:
    output = Path(argv[0]).resolve() if argv else ROOT / "final-audit.json"
    audit = build_audit()
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": audit["status"], "output": str(output), "checks": audit["checks"]}, ensure_ascii=False, separators=(",", ":")))
    return 0 if audit["status"] == "LOCAL_COMPLETE_EXTERNAL_NOT_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
