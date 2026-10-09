from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JENKINSFILE = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")


def test_analyzer_pipeline_is_downstream_of_gpu_sync_and_archives_artifacts() -> None:
    assert "upstreamProjects: analyzerUpstream" in JENKINSFILE
    assert "def analyzerUpstream = 'sky/GPU/GPU-FUSION/10-ORACLE_DB_DDL_SYNC'" in JENKINSFILE
    assert "archiveArtifacts" in JENKINSFILE
    assert "artifacts: 'out/**'" in JENKINSFILE
    assert "skipDefaultCheckout(true)" in JENKINSFILE
    assert "disableConcurrentBuilds()" in JENKINSFILE


def test_analyzer_pipeline_is_git_only_and_sends_only_verified_artifacts() -> None:
    assert "run --allow-ai" in JENKINSFILE
    assert "run --allow-mail" not in JENKINSFILE
    assert "consume_report_artifact.groovy" in JENKINSFILE
    assert "result.outcome == 'ARTIFACT_READY'" in JENKINSFILE
    assert "mkaracan@innova.com.tr" in JENKINSFILE
    assert "ORACLE_DSN" not in JENKINSFILE
    assert "oracle-db-ddl-sync" not in JENKINSFILE


def test_analyzer_pipeline_uses_locked_offline_runtime() -> None:
    assert "python3.13 -m venv .venv" in JENKINSFILE
    assert "--no-index" in JENKINSFILE
    assert "--require-hashes" in JENKINSFILE
    assert "doctor --offline" in JENKINSFILE


def test_analyzer_pipeline_is_manually_startable_with_parameters() -> None:
    assert "parameters {" in JENKINSFILE
    assert "name: 'ANALYZER_CONFIG'" in JENKINSFILE
    assert "name: 'ANALYZER_WHEELHOUSE'" in JENKINSFILE
    assert "name: 'ANALYZER_GIT_CREDENTIAL_ID'" in JENKINSFILE
    assert "name: 'ANALYZER_MODEL_KEY_CREDENTIAL_ID'" in JENKINSFILE
    assert "disableJob()" not in JENKINSFILE
