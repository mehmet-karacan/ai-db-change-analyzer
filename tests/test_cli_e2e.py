from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import pytest
from types import SimpleNamespace
from pathlib import Path

from db_change_analyzer.archive import ArchiveError
from db_change_analyzer.cli import _new_outbox, main
from db_change_analyzer.artifact_consumer import consume_artifact_directory
from db_change_analyzer.config import load_config
from db_change_analyzer.litellm_http import ModelReply
from db_change_analyzer.research_journal import ResearchJournal
from db_change_analyzer.smtp_transport import SmtpDeliveryError, SmtpResult

from tests.helpers.git_fixture import GitFixture


ROOT = Path(__file__).resolve().parents[1]


def config_for(tmp_path: Path) -> Path:
    text = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8")
    state = (tmp_path / "state").as_posix()
    text = text.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{state}"')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def artifact_config_for(tmp_path: Path) -> Path:
    text = (ROOT / "config" / "gpu.artifact.example.toml").read_text(encoding="utf-8")
    text = text.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    text = text.replace('emit_dir = "./out"', f'emit_dir = "{(tmp_path / "out").as_posix()}"')
    for old, new in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "reviewed/smoke.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
    ):
        text = text.replace(old, new)
    path = tmp_path / "artifact-config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_offline_baseline_no_change_out_of_scope_and_dry_run(tmp_path: Path, capsys) -> None:
    config = config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER);\n")
    first = source.commit("initial")
    bare = tmp_path / "state" / "scopes"
    scope_dir = next(bare.iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)

    assert main(["--config", str(config), "run", "--offline"]) == 0
    baseline = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert baseline["outcome"] == "BASELINED" and baseline["checkpoint_after"] == first
    assert main(["--config", str(config), "run", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["outcome"] == "NO_CHANGE"

    source.write("README.md", "outside\n")
    second = source.commit("outside")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    outside = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert outside["outcome"] == "OUT_OF_SCOPE_ONLY" and outside["checkpoint_after"] == second

    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER NOT NULL);\n")
    third = source.commit("db change")
    git("fetch", str(source.root), f"{third}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "run", "--dry-run", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["outcome"] == "DRY_RUN"
    assert main(["--config", str(config), "state", "status"]) == 0
    details = capsys.readouterr().err
    assert second in details and third not in details


def test_artifact_profile_runs_without_smtp_and_does_not_create_notification(tmp_path: Path, capsys) -> None:
    config = artifact_config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER);\n")
    first = source.commit("initial")
    cache = next((tmp_path / "state" / "scopes").iterdir()) / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)

    assert main(["--config", str(config), "run", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["outcome"] == "BASELINED"
    source.write("gpu_user/t.sql", "CREATE  TABLE S.T(ID NUMBER);\n")
    second = source.commit("format change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    out = tmp_path / "out"
    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert result["outcome"] == "ARTIFACT_READY"
    assert result["smtp_attempts"] == 0
    assert (out / "report-email.html").is_file()
    persisted_result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert persisted_result["schema_version"] == "2.0"
    assert persisted_result["delivery_mode"] == "jenkins_artifact"
    assert persisted_result["artifact_ready"] is True
    assert persisted_result["email_html_path"] == "report-email.html"
    persisted_report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert persisted_report["schema_version"] == "2.0"
    assert persisted_report["analysis_fingerprint"] == result["analysis_fingerprint"]
    assert consume_artifact_directory(out).manifest["report_id"] == result["report_id"]
    replay = tmp_path / "replay"
    assert main([
        "--config", str(config), "--emit-dir", str(replay), "report", "emit",
        "--report-id", result["report_id"], "--expected-report-sha256", result["report_sha256"],
    ]) == 0
    replay_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert replay_result["outcome"] == "REPORT_EMITTED"
    for name in ("report.json", "report.html", "report-email.html", "report.txt", "mail-view.json", "render-manifest.json"):
        assert (replay / name).read_bytes() == (out / name).read_bytes()
    connection = sqlite3.connect(next((tmp_path / "state" / "scopes").iterdir()) / "state.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    finally:
        connection.close()


def test_artifact_resume_repairs_outputs_after_archive_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace("archive_enabled = false", "archive_enabled = true")
        .replace('archive_root = ""', f'archive_root = "{(tmp_path / "application").as_posix()}"'),
        encoding="utf-8",
    )
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER);\n")
    first = source.commit("initial")
    cache = next((tmp_path / "state" / "scopes").iterdir()) / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE  TABLE S.T(ID NUMBER);\n")
    second = source.commit("format change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    original_archive = __import__("db_change_analyzer.workflow", fromlist=["write_report_archive"]).write_report_archive

    def fail_archive(*_args, **_kwargs):
        raise ArchiveError("ARCHIVE_COLLISION")

    monkeypatch.setattr("db_change_analyzer.workflow.write_report_archive", fail_archive)
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 50
    failed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert failed["outcome"] == "ARCHIVE_FAILED"
    assert failed["checkpoint_after"] == first

    monkeypatch.setattr("db_change_analyzer.workflow.write_report_archive", original_archive)
    assert main(run_args) == 0
    resumed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed["outcome"] == "ARTIFACT_READY"
    assert resumed["checkpoint_after"] == second
    assert (tmp_path / "out" / "report-email.html").is_file()
    assert list((tmp_path / "application" / "db-change-analyzer" / "reports").rglob("report-email.html"))


def test_source_review_profile_uses_research_receipts_and_application_execution_binding(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class FakeSourceReviewModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            assert payload["research_receipts"]
            assert {receipt["tool_name"] for receipt in payload["research_receipts"]} >= {"get_diff", "read_source", "find_references", "get_history"}
            assert any(receipt["tool_name"] == "find_references" and receipt["items"] for receipt in payload["research_receipts"])
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            content = json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{
                    "explanation_id": "ex-change", "kind": "change",
                    "text_tr": "Kaynakta kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id],
                }],
                "findings": [{
                    "finding_id": "finding-null", "kind": "issue", "category": "correctness",
                    "title_tr": "Null uyumluluğu kontrolü", "detail_tr": "Mevcut null kayıtlar kontrol edilmelidir.",
                    "severity": "high", "basis": "source_backed_inference", "evidence_ids": [evidence_id],
                    "verification_steps": ["Null kayıtları sayın."], "execution": "not_run",
                }], "conclusion": "findings", "limitations": [], "unfinished_research": [],
            })
            return ModelReply(content, "mock-source-review", "stop", {"prompt_tokens": 1, "completion_tokens": 1})

        def complete_with_tools(self, **kwargs):
            payload = kwargs["user_payload"]
            assert {item["function"]["name"] for item in kwargs["tools"]} >= {"read_source", "find_references", "get_history"}
            receipt = kwargs["handlers"]["find_references"](
                {"revision": payload["source_pair"]["target_sha"], "symbol": "T"}, "loop-call-1"
            )
            result = self.complete(**kwargs)
            return SimpleNamespace(final_content=result.content, returned_model=result.returned_model, tool_receipts=[receipt])

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeSourceReviewModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "ARTIFACT_READY"
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    review = report["objects"][0]["source_review"]
    assert review["schema_version"] == "source-review/1.0"
    assert review["research_receipts"]
    assert any(item["tool_call_id"] == "loop-call-1" for item in review["research_receipts"])
    assert review["explanations"][0]["author_execution_id"]
    assert review["findings"][0]["author_execution_id"]
    journal = ResearchJournal(scope_dir, scope_hash=load_config(config).scope_hash, run_id=result["run_id"], generation=0)
    journal_records = journal.verify()
    assert {record["record_type"] for record in journal_records} == {"tool_receipt", "unit_status"}
    assert any(record.get("tool_call_id") == "loop-call-1" for record in journal_records)
    assert any(record.get("status") == "VALIDATED" for record in journal_records if record["record_type"] == "unit_status")
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
    assert view["objects"][0]["ai_comments"][0]["origin"] == "source_review"
    assert view["objects"][0]["ai_comments"][0]["author_execution_id"] == review["explanations"][0]["author_execution_id"]
    assert any("Null uyumluluğu" in comment["text_tr"] for comment in view["objects"][0]["ai_comments"])


def test_source_review_profile_includes_added_and_removed_objects(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/removed.sql", "CREATE TABLE S.REMOVED_T(ID NUMBER);\n")
    source.write("gpu_user/kept.sql", "CREATE TABLE S.KEPT_T(ID NUMBER);\n")
    source.write("gpu_user/consumer.sql", "CREATE VIEW S.REMOVED_CONSUMER AS SELECT * FROM S.REMOVED_T;\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    (source.root / "gpu_user" / "removed.sql").unlink()
    source.write("gpu_user/added.sql", "CREATE TABLE S.ADDED_T(ID NUMBER);\n")
    second = source.commit("add and remove")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    seen: list[str] = []
    research_by_operation: dict[str, list[dict[str, object]]] = {}

    class FakeSourceReviewModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            seen.append(payload["object"]["operation"])
            assert payload["research_receipts"]
            research_by_operation[payload["object"]["operation"]] = payload["research_receipts"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"] if payload["evidence_registry"] else payload["facts"][0]["before"]["evidence_ids"][0]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Nesne değişikliği kaynakta gözlendi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "mock-source-review", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeSourceReviewModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "ARTIFACT_READY"
    assert sorted(seen) == ["added", "removed"]
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert {item["net_operation"] for item in report["objects"]} == {"added", "removed"}
    removed_receipts = research_by_operation["removed"]
    removed_reference_receipts = [item for item in removed_receipts if item["tool_name"] == "find_references"]
    assert {item["revision"] for item in removed_reference_receipts} == {first, second}
    target_items = [item for receipt in removed_reference_receipts if receipt["revision"] == second for item in receipt["items"]]
    assert any("consumer.sql" in item["path_display"] for item in target_items)
    history_receipts = [item for item in removed_receipts if item["tool_name"] == "get_history"]
    assert history_receipts and history_receipts[0]["revision"] == first


def test_source_review_profile_keeps_repaired_caller_in_history_only_context(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/removed.sql", "CREATE TABLE S.REMOVED_T(ID NUMBER);\n")
    source.write("gpu_user/kept.sql", "CREATE TABLE S.KEPT_T(ID NUMBER);\n")
    source.write("gpu_user/consumer.sql", "CREATE VIEW S.CONSUMER AS SELECT * FROM S.REMOVED_T;\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()

    (source.root / "gpu_user" / "removed.sql").unlink()
    (source.root / "gpu_user" / "consumer.sql").write_text(
        "CREATE VIEW S.CONSUMER AS SELECT * FROM S.KEPT_T;\n", encoding="utf-8"
    )
    second = source.commit("remove object and repair caller")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    captured: dict[str, list[dict[str, object]]] = {}

    class FakeSourceReviewModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            captured[payload["object"]["operation"]] = payload["research_receipts"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Nesne değişikliği kaynakta gözlendi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "mock-source-review", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeSourceReviewModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "ARTIFACT_READY"
    removed_receipts = captured["removed"]
    reference_receipts = [item for item in removed_receipts if item["tool_name"] == "find_references"]
    assert {item["revision"] for item in reference_receipts} == {first, second}
    base_items = [item for receipt in reference_receipts if receipt["revision"] == first for item in receipt["items"]]
    target_items = [item for receipt in reference_receipts if receipt["revision"] == second for item in receipt["items"]]
    assert any("consumer.sql" in item["path_display"] for item in base_items)
    assert target_items == []


def test_source_review_profile_sends_body_text_changes_to_model(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    baseline = "CREATE OR REPLACE PACKAGE BODY S.P AS\nPROCEDURE RUN AS\nBEGIN\n  NULL;\nEND RUN;\nEND P;\n/\n"
    changed = baseline.replace("  NULL;", "  NULL;\n  NULL;")
    source.write("gpu_user/p.sql", baseline)
    first = source.commit("initial package body")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/p.sql", changed)
    second = source.commit("body-only change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    seen: list[list[str]] = []

    class FakeSourceReviewModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            seen.append([fact["taxonomy_id"] for fact in payload["facts"]])
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Paket gövdesi kaynakta değişti.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "mock-source-review", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeSourceReviewModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "ARTIFACT_READY" and result["checkpoint_after"] == second
    assert seen and any(item.startswith("package_body.body_property") for item in seen[0])


def test_dry_run_rejects_live_permissions_before_network(tmp_path: Path, capsys) -> None:
    config = config_for(tmp_path)
    assert main(["--config", str(config), "run", "--dry-run", "--allow-ai"]) == 20
    result = json.loads(capsys.readouterr().out.strip())
    assert result["error_code"] == "DRY_RUN_PERMISSION_CONFLICT"


def test_format_equivalent_index_definitions_keep_one_structural_change(tmp_path: Path, capsys, monkeypatch) -> None:
    config = config_for(tmp_path)
    text = config.read_text(encoding="utf-8")
    for old, new in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "reviewed/smoke.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
        ('host = "<SMTP_HOST>"', 'host = "smtp.example.test"'),
        ('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"'),
        ('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"'),
    ):
        text = text.replace(old, new)
    config.write_text(text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    paths = ("gpu_user/index_a.sql", "gpu_user/index_b.sql")
    source.write(paths[0], "CREATE INDEX S.I ON S.T (ID ASC);\n")
    source.write(paths[1], "CREATE  INDEX S.I ON S.T (ID  ASC);\n")
    first = source.commit("duplicate index baseline")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write(paths[0], "CREATE INDEX S.I ON S.T (ID DESC);\n")
    source.write(paths[1], "CREATE  INDEX S.I ON S.T (ID  DESC);\n")
    second = source.commit("duplicate index direction change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class FakeModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            content = json.dumps({"schema_version": "mail-commentary/1.1", "report_id": payload["report_id"],
                                  "unit_id": payload["unit_id"], "input_digest": payload["input_digest"],
                                  "summary": None, "interpretations": [], "uncertainties": [], "recommended_checks": []})
            return ModelReply(content, "mock-model", "stop", None)

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _mime, recipients, **kwargs):
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeModel)
    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FakeMail)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    out = tmp_path / "out"
    assert main(["--config", str(config), "--emit-dir", str(out), "run", "--offline", "--allow-ai", "--allow-mail"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["checkpoint_after"] == second
    view = json.loads((out / "mail-view.json").read_text(encoding="utf-8"))
    assert len(view["objects"]) == 1
    assert any(fact["taxonomy_id"] == "index.index_property.direction" for fact in view["objects"][0]["facts"])

    source.write(paths[1], "CREATE INDEX S.I ON S.T (ID ASC);\n")
    third = source.commit("conflicting index definitions")
    git("fetch", str(source.root), f"{third}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "--emit-dir", str(out), "run", "--offline", "--allow-ai", "--allow-mail"]) == 11
    conflicting = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert len(conflicting["objects"]) == 1
    assert "CONFLICTING_DEFINITIONS" in conflicting["objects"][0]["diagnostics"]


def test_format_and_definition_order_changes_are_reported_without_model_calls(tmp_path: Path, capsys, monkeypatch) -> None:
    config = config_for(tmp_path)
    text = config.read_text(encoding="utf-8")
    for old, new in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "reviewed/smoke.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
        ('host = "<SMTP_HOST>"', 'host = "smtp.example.test"'),
        ('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"'),
        ('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"'),
    ):
        text = text.replace(old, new)
    config.write_text(text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    first_sql = "CREATE TABLE A (ID NUMBER);\nCREATE TABLE B (ID NUMBER);\n"
    source.write("gpu_user/tables.sql", first_sql)
    source.write("gpu_user/package.sql", "CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); END P; /\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()

    class NoModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **_kwargs):
            raise AssertionError("source-only changes must not call the model")

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _mime, recipients, **kwargs):
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", NoModel)
    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FakeMail)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    for sql, expected_pattern in (
        ("CREATE  TABLE A (ID NUMBER);\nCREATE TABLE B (ID NUMBER);\n", "format_only"),
        ("CREATE TABLE B (ID NUMBER);\nCREATE  TABLE A (ID NUMBER);\n", "source_order_only"),
    ):
        source.write("gpu_user/tables.sql", sql)
        target = source.commit(expected_pattern)
        git("fetch", str(source.root), f"{target}:refs/remotes/source/master", cwd=cache)
        assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) == 0
        result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert result["checkpoint_after"] == target
        view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
        assert view["analysis"]["ai"]["http_attempts"] == 0
        assert expected_pattern in {item["pattern"] for item in view["objects"]}

    baseline_sql = "CREATE TABLE B (ID NUMBER);\nCREATE  TABLE A (ID NUMBER);\n"
    source.write("gpu_user/tables.sql", baseline_sql.replace("ID NUMBER", "ID NUMBER NOT NULL", 1))
    intermediate = source.commit("temporary change")
    source.write("gpu_user/tables.sql", baseline_sql)
    reverted = source.commit("revert temporary change")
    git("fetch", str(source.root), f"{reverted}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["checkpoint_after"] == reverted
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["net_files"] == 0
    assert report["counts"]["change_events"] == 2
    assert [event["commit_sha"] for event in report["events"]] == [intermediate, reverted]
    assert report["artifacts"][0]["net_operation"] == "unchanged"
    assert "2 dosya geçişi" in (tmp_path / "out" / "report.txt").read_text(encoding="utf-8")

    source.write("gpu_user/package.sql", "CREATE PACKAGE S.P AS PROCEDURE X(P  NUMBER); END P; /\n")
    package_format = source.commit("package whitespace")
    git("fetch", str(source.root), f"{package_format}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["checkpoint_after"] == package_format
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
    assert view["analysis"]["ai"]["http_attempts"] == 0
    assert len(view["objects"]) == 1
    assert view["objects"][0]["pattern"] == "format_only"
    assert [fact["taxonomy_id"] for fact in view["objects"][0]["facts"]] == ["common.source.format"]


def test_stateful_analysis_uses_one_model_unit_and_one_smtp_transaction(tmp_path: Path, capsys, monkeypatch) -> None:
    config = config_for(tmp_path)
    text = config.read_text(encoding="utf-8")
    text = text.replace("route_verified = false", "route_verified = true")
    text = text.replace("capabilities_verified = false", "capabilities_verified = true")
    text = text.replace('capability_record = ""', 'capability_record = "reviewed/smoke.json"')
    text = text.replace("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768")
    text = text.replace('host = "<SMTP_HOST>"', 'host = "smtp.example.test"')
    text = text.replace('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"')
    text = text.replace('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"')
    config.write_text(text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER NOT NULL);\n")
    second = source.commit("change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    calls = {"model": 0, "smtp": 0}

    class FakeModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            calls["model"] += 1
            payload = kwargs["user_payload"]
            fact = next((item for item in payload["facts"] if item["before"]["state"] == item["after"]["state"] == "present"), None)
            summary = None if fact is None else {
                "text_tr": f"Git kaynağında {fact['subject_name']} değeri {fact['before']['value']} iken {fact['after']['value']} oldu.",
                "fact_ids": [fact["fact_id"]],
                "evidence_ids": list(dict.fromkeys(fact["before"]["evidence_ids"] + fact["after"]["evidence_ids"])),
                "claim_kind": "change_restatement",
            }
            content = json.dumps({"schema_version": "mail-commentary/1.1", "report_id": payload["report_id"], "unit_id": payload["unit_id"], "input_digest": payload["input_digest"], "summary": summary, "interpretations": [], "uncertainties": [], "recommended_checks": []})
            return ModelReply(content, "mock-model", "stop", None)

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _mime, recipients, **kwargs):
            calls["smtp"] += 1
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeModel)
    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FakeMail)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) in {0, 10}
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["checkpoint_after"] == second
    assert calls == {"model": 1, "smtp": 1}
    assert (tmp_path / "out" / "report.json").is_file()
    assert (tmp_path / "out" / "mail-view.json").is_file()
    assert (tmp_path / "out" / "render-manifest.json").is_file()
    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        report = connection.execute("SELECT report_id,rendered_version FROM reports").fetchone()
        assert report["rendered_version"] == "v5.0"
        sidecar = connection.execute("SELECT * FROM report_render_sidecars WHERE report_id=? AND render_generation=0", (report["report_id"],)).fetchone()
        sent = connection.execute("SELECT mime_bytes,mime_sha256 FROM notifications WHERE report_id=? AND generation=0", (report["report_id"],)).fetchone()
        manifest = json.loads(sidecar["manifest_json"])
        view = json.loads(sidecar["mail_view_json"])
        assert view["analysis"]["ai"]["status"] == "complete"
        assert view["analysis"]["ai"]["http_attempts"] == 1
        assert view["analysis"]["ai"]["models"][0]["returned_models"] == ["mock-model"]
        assert json.loads(connection.execute("SELECT returned_models_json FROM units WHERE run_id=?", (result["run_id"],)).fetchone()[0]) == ["mock-model"]
        assert view["objects"][0]["ai_status"] == "displayed"
        assert view["objects"][0]["ai_comments"][0]["acceptance_method"] == "deterministic_rule"
        accepted_execution_ids = {item["execution_id"] for item in view["analysis"]["ai"]["executions"] if item["status"] == "accepted"}
        assert view["objects"][0]["ai_comments"][0]["author_execution_id"] in accepted_execution_ids
        execution = next(item for item in view["analysis"]["ai"]["executions"] if item["execution_id"] == view["objects"][0]["ai_comments"][0]["author_execution_id"])
        assert execution["requested_route"].endswith("/chat/completions")
        assert execution["requested_model"] == load_config(config).model.id
        assert execution["returned_model"] == "mock-model"
        assert execution["policy_versions"] and len(execution["policy_fingerprint"]) == 64
        assert execution["response_schema"] == "mail-commentary/1.1"
        assert execution["generation"] == 0 and execution["started_at"] <= execution["completed_at"]
        assert sent["mime_bytes"] == sidecar["mime"]
        assert hashlib.sha256(sent["mime_bytes"]).hexdigest() == manifest["mime_sha256"] == sent["mime_sha256"]
        assert manifest["source_report_sha256"] == result["report_sha256"]
        assert sidecar["html"] == (tmp_path / "out" / "report.html").read_bytes()
        new_notification_id = _new_outbox(connection, load_config(config), report["report_id"])
        second_sidecar = connection.execute("SELECT * FROM report_render_sidecars WHERE report_id=? AND render_generation=1", (report["report_id"],)).fetchone()
        second_notification = connection.execute("SELECT mime_bytes FROM notifications WHERE notification_id=?", (new_notification_id,)).fetchone()
        assert second_notification["mime_bytes"] == second_sidecar["mime"]
        assert second_notification["mime_bytes"] != sent["mime_bytes"]
    finally:
        connection.close()

    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER NULL);\n")
    third = source.commit("restore nullable")
    git("fetch", str(source.root), f"{third}:refs/remotes/source/master", cwd=cache)

    class InvalidModel(FakeModel):
        def complete(self, **_kwargs):
            calls["model"] += 1
            return ModelReply('{"invalid":true}', "provider/invalid-attempt", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", InvalidModel)
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out-invalid"), "run", "--offline", "--allow-ai", "--allow-mail"]) == 31
    invalid_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert invalid_result["checkpoint_after"] == second
    assert calls == {"model": 3, "smtp": 1}
    assert not (tmp_path / "out-invalid" / "mail-view.json").exists()

    source.write("gpu_user/t.sql", "CREATE TABLE t(id VARCHAR2(20));\n")
    fourth = source.commit("later source change")
    git("fetch", str(source.root), f"{fourth}:refs/remotes/source/master", cwd=cache)
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeModel)
    attempted_mime = []

    class FailsOnceMail(FakeMail):
        def send(self, mime, recipients, **kwargs):
            attempted_mime.append(mime)
            if len(attempted_mime) == 1:
                calls["smtp"] += 1
                raise SmtpDeliveryError("TEST_PRE_DATA_FAILURE")
            return super().send(mime, recipients, **kwargs)

    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FailsOnceMail)
    monkeypatch.setattr("db_change_analyzer.smtp_transport.SmtpTransport", FailsOnceMail)
    run_args = ["--config", str(config), "--emit-dir", str(tmp_path / "out-retry"),
                "run", "--offline", "--allow-ai", "--allow-mail"]
    assert main(run_args) == 40
    failed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert failed_result["checkpoint_after"] == second
    assert calls == {"model": 4, "smtp": 2}
    assert main(run_args) in {0, 10}
    retry_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert retry_result["checkpoint_after"] == third
    assert calls == {"model": 4, "smtp": 3}
    assert attempted_mime[0] == attempted_mime[1]
    assert retry_result["run_id"] == failed_result["run_id"]
    retried_report = json.loads((tmp_path / "out-retry" / "report.json").read_text(encoding="utf-8"))
    retried_view = json.loads((tmp_path / "out-retry" / "mail-view.json").read_text(encoding="utf-8"))
    assert retried_report["counts"]["ai_http_attempts"] == 3
    assert retried_view["analysis"]["ai"]["http_attempts"] == 3
    assert retried_view["analysis"]["ai"]["phase_started_at"] is not None
    assert retried_view["analysis"]["duration_basis"] == "timestamp_difference"
    assert retried_view["analysis"]["ai"]["models"][0]["returned_models"] == ["mock-model", "provider/invalid-attempt"]
    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM runs WHERE mode='AUTO' AND target_sha=?", (third,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs WHERE mode='AUTO' AND target_sha=?", (fourth,)).fetchone()[0] == 0
    finally:
        connection.close()

    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out-manual"),
                 "manual", "--base", first, "--target", second, "--allow-ai"]) == 0
    manual_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert manual_result["outcome"] == "MANUAL_COMPLETE"
    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        manual_id = manual_result["report_id"]
        assert connection.execute("SELECT COUNT(*) FROM notifications WHERE report_id=?", (manual_id,)).fetchone()[0] == 0
        notification_id = _new_outbox(connection, load_config(config), manual_id)
        generation = connection.execute("SELECT generation FROM notifications WHERE notification_id=?", (notification_id,)).fetchone()[0]
        assert generation == 1
    finally:
        connection.close()


def test_trigger_projection_keeps_generic_v5_source_notice(tmp_path: Path, capsys, monkeypatch) -> None:
    config = config_for(tmp_path)
    text = config.read_text(encoding="utf-8")
    for before, after in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "reviewed/smoke.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
        ('host = "<SMTP_HOST>"', 'host = "smtp.example.test"'),
        ('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"'),
        ('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"'),
    ):
        text = text.replace(before, after)
    config.write_text(text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/tr.sql", "CREATE OR REPLACE TRIGGER TR BEFORE INSERT ON T BEGIN NULL; END;\n")
    first = source.commit("trigger")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/tr.sql", "CREATE OR REPLACE TRIGGER TR BEFORE INSERT ON T BEGIN NULL; NULL; END;\n")
    second = source.commit("trigger change")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class NoModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **_kwargs):
            raise AssertionError("source-only trigger change must not be sent to the model")

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _mime, recipients, **kwargs):
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", NoModel)
    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FakeMail)
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) in {0, 10}
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
    assert view["objects"][0]["identity"]["object_type"] == "TRIGGER"
    assert view["objects"][0]["verification"] == "verified"
    assert view["analysis"]["ai"]["status"] == "not_used"


def test_standalone_table_context_file_changes_parent_object(tmp_path: Path, capsys, monkeypatch) -> None:
    config = config_for(tmp_path)
    text = config.read_text(encoding="utf-8")
    for before, after in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "reviewed/smoke.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
        ('host = "<SMTP_HOST>"', 'host = "smtp.example.test"'),
        ('sender = "<APPROVED_SENDER_ADDRESS>"', 'sender = "sender@example.test"'),
        ('message_id_domain = "<APPROVED_MESSAGE_ID_DOMAIN>"', 'message_id_domain = "example.test"'),
    ):
        text = text.replace(before, after)
    config.write_text(text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE T (ID NUMBER);\n")
    first = source.commit("table")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t_context.sql", "ALTER TABLE T ADD (X NUMBER); COMMENT ON COLUMN T.X IS 'public';\n")
    second = source.commit("table context")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class FakeModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            content = {"schema_version": "mail-commentary/1.1", "report_id": payload["report_id"],
                       "unit_id": payload["unit_id"], "input_digest": payload["input_digest"],
                       "summary": None, "interpretations": [], "uncertainties": [], "recommended_checks": []}
            return ModelReply(json.dumps(content), "mock-model", "stop", None)

    class FakeMail:
        def __init__(self, _config):
            pass

        def send(self, _mime, recipients, **kwargs):
            kwargs["on_data_started"]()
            return SmtpResult(tuple(recipients), {})

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", FakeModel)
    monkeypatch.setattr("db_change_analyzer.workflow.SmtpTransport", FakeMail)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out"), "run", "--offline", "--allow-ai", "--allow-mail"]) in {0, 10}
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["checkpoint_after"] == second
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
    obj = view["objects"][0]
    assert any(fact["taxonomy_id"] == "table.column.definition" for fact in obj["facts"])
    assert "gpu_user/t_context.sql" in obj["source_paths"]
    assert not view["artifact_notices"]

    (source.root / "gpu_user" / "t_context.sql").unlink()
    third = source.commit("remove table context")
    git("fetch", str(source.root), f"{third}:refs/remotes/source/master", cwd=cache)
    assert main(["--config", str(config), "--emit-dir", str(tmp_path / "out-removed"), "run", "--offline", "--allow-ai", "--allow-mail"]) in {0, 10}
    removed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert removed_result["checkpoint_after"] == third
    removed = json.loads((tmp_path / "out-removed" / "mail-view.json").read_text(encoding="utf-8"))
    assert any(fact["taxonomy_id"] == "table.column.definition" and fact["change_action"] == "removed"
               for fact in removed["objects"][0]["facts"])


def test_source_review_budget_progresses_and_final_report_contains_all_units(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0").replace("max_new_units_per_invocation = 40", "max_new_units_per_invocation = 2"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    for index in range(3):
        source.write(f"gpu_user/t{index:02d}.sql", f"CREATE TABLE S.T{index:02d}(ID NUMBER NULL);\n")
    first = source.commit("initial tables")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    for index in range(3):
        source.write(f"gpu_user/t{index:02d}.sql", f"CREATE TABLE S.T{index:02d}(ID NUMBER NOT NULL);\n")
    second = source.commit("make all columns required")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    calls = 0

    class BudgetModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            nonlocal calls
            calls += 1
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "budget-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", BudgetModel)
    monkeypatch.setattr("db_change_analyzer.workflow._research_receipts", lambda *args, **kwargs: [])
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 11
    first_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first_result["outcome"] == "RETRY_PENDING"
    assert first_result["error_code"] == "UNIT_BUDGET"
    assert calls == 2
    source.write("gpu_user/remote.sql", "CREATE TABLE S.REMOTE_ONLY(ID NUMBER);\n")
    remote_target = source.commit("remote commit while analysis is pending")
    git("fetch", str(source.root), f"{remote_target}:refs/remotes/source/master", cwd=cache)
    assert main(run_args) == 20
    remote_conflict = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert remote_conflict["outcome"] == "PINNED_RUN_CONFLICT"
    assert remote_conflict["error_code"] == "PINNED_RUN_CONTEXT_CHANGED"
    git("update-ref", "refs/remotes/source/master", second, cwd=cache)
    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM units WHERE run_id=? AND status='VALIDATED'", (first_result["run_id"],)).fetchone()[0] == 2
        original_fingerprint = connection.execute("SELECT fingerprint_json FROM runs WHERE run_id=?", (first_result["run_id"],)).fetchone()[0]
        corrupted_fingerprint = json.loads(original_fingerprint)
        corrupted_fingerprint["analysis_fingerprint"] = "0" * 64
        connection.execute(
            "UPDATE runs SET fingerprint_json=? WHERE run_id=?",
            (json.dumps(corrupted_fingerprint, sort_keys=True, separators=(",", ":")), first_result["run_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    assert main(run_args) == 20
    conflict_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert conflict_result["outcome"] == "PINNED_RUN_CONFLICT"
    assert conflict_result["error_code"] == "ANALYSIS_FINGERPRINT_MISMATCH"

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        connection.execute("UPDATE runs SET fingerprint_json=? WHERE run_id=?", ("[]", first_result["run_id"]))
        connection.commit()
    finally:
        connection.close()

    assert main(run_args) == 23
    invalid_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert invalid_result["outcome"] == "STATE_INVALID"
    assert invalid_result["error_code"] == "RUN_FINGERPRINT_INVALID"

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        connection.execute("UPDATE runs SET fingerprint_json=? WHERE run_id=?", (original_fingerprint, first_result["run_id"]))
        connection.commit()
    finally:
        connection.close()

    assert main(run_args) == 0
    second_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert second_result["outcome"] == "ARTIFACT_READY"
    assert calls == 3
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert len(report["objects"]) == 3


@pytest.mark.scale
@pytest.mark.parametrize("object_count", [41, 100])
def test_source_review_budget_processes_real_scale_without_losing_units(tmp_path: Path, capsys, monkeypatch, object_count: int) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    for index in range(object_count):
        source.write(f"gpu_user/t{index:03d}.sql", f"CREATE TABLE S.T{index:03d}(ID NUMBER NULL);\n")
    first = source.commit("initial scale tables")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    for index in range(object_count):
        source.write(f"gpu_user/t{index:03d}.sql", f"CREATE TABLE S.T{index:03d}(ID NUMBER NOT NULL);\n")
    second = source.commit("required scale columns")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    calls = 0

    class ScaleModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            nonlocal calls
            calls += 1
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "scale-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ScaleModel)
    monkeypatch.setattr("db_change_analyzer.workflow._research_receipts", lambda *args, **kwargs: [])
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    expected_pending = (object_count + 39) // 40 - 1
    for _ in range(expected_pending):
        assert main(run_args) == 11
        pending = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert pending["outcome"] == "RETRY_PENDING"
        assert pending["error_code"] == "UNIT_BUDGET"
    assert main(run_args) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "ARTIFACT_READY"
    assert calls == object_count
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert len(report["objects"]) == object_count


def test_source_review_resumes_same_run_after_model_process_crash(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("required column")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class CrashModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **_kwargs):
            raise RuntimeError("simulated process crash")

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", CrashModel)
    monkeypatch.setattr("db_change_analyzer.workflow._research_receipts", lambda *args, **kwargs: [])
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 90
    crash_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert crash_result["outcome"] == "INTERNAL_ERROR"

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        run = connection.execute("SELECT run_id,status,report_id FROM runs WHERE base_sha=? AND target_sha=?", (first, second)).fetchone()
        checkpoint = connection.execute("SELECT checkpoint_sha FROM scopes").fetchone()[0]
    finally:
        connection.close()
    assert run is not None and run[1] == "ANALYZING" and run[2] is None
    assert checkpoint == first

    class ResumeModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "resume-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ResumeModel)
    assert main(run_args) == 0
    resumed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed_result["outcome"] == "ARTIFACT_READY"
    assert resumed_result["run_id"] == run[0]
    assert resumed_result["checkpoint_after"] == second
    resumed_view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))
    executions = resumed_view["analysis"]["ai"]["executions"]
    assert executions and {item["returned_model"] for item in executions} == {"resume-model"}
    accepted_execution_ids = {item["execution_id"] for item in executions if item["status"] == "accepted"}
    assert accepted_execution_ids
    assert all(comment["author_execution_id"] in accepted_execution_ids
               for obj in resumed_view["objects"] for comment in obj["ai_comments"])


def test_source_review_resumes_after_tool_result_process_crash(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("required column")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

    class ToolCrashModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete_with_tools(self, **kwargs):
            payload = kwargs["user_payload"]
            kwargs["handlers"]["find_references"](
                {"revision": payload["source_pair"]["target_sha"], "symbol": "T"}, "crash-tool-call"
            )
            raise RuntimeError("simulated process crash after tool result")

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ToolCrashModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 90
    crash_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert crash_result["outcome"] == "INTERNAL_ERROR"

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        run = connection.execute(
            "SELECT run_id,status,report_id FROM runs WHERE base_sha=? AND target_sha=?", (first, second)
        ).fetchone()
        checkpoint = connection.execute("SELECT checkpoint_sha FROM scopes").fetchone()[0]
    finally:
        connection.close()
    assert run is not None and run[1] == "ANALYZING" and run[2] is None
    assert checkpoint == first
    journal = ResearchJournal(scope_dir, scope_hash=load_config(config).scope_hash, run_id=run[0], generation=0)
    assert any(record.get("tool_call_id") == "crash-tool-call" for record in journal.verify())

    class ResumeToolModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete_with_tools(self, **kwargs):
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return SimpleNamespace(
                final_content=json.dumps({
                    "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                    "input_digest": payload["input_digest"],
                    "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                    "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
                }),
                returned_model="resume-tool-model",
                tool_receipts=[],
            )

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ResumeToolModel)
    assert main(run_args) == 0
    resumed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed_result["outcome"] == "ARTIFACT_READY"
    assert resumed_result["run_id"] == run[0]
    assert resumed_result["checkpoint_after"] == second


def test_source_review_resumes_after_validation_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("required column")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    invalid_calls = 0

    class InvalidModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            nonlocal invalid_calls
            invalid_calls += 1
            return ModelReply(json.dumps({"schema_version": "source-review/1.0"}), "invalid-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", InvalidModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 31
    invalid_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert invalid_result["outcome"] == "AI_CONTEXT_INVALID"
    assert invalid_calls == 2

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        run = connection.execute(
            "SELECT run_id,status,report_id FROM runs WHERE base_sha=? AND target_sha=?", (first, second)
        ).fetchone()
        unit = connection.execute(
            "SELECT status,http_attempts FROM units WHERE run_id=?", (run[0],)
        ).fetchone()
        checkpoint = connection.execute("SELECT checkpoint_sha FROM scopes").fetchone()[0]
    finally:
        connection.close()
    assert run is not None and run[1] == "ANALYZING" and run[2] is None
    assert unit is not None and unit[0] == "INVALID" and unit[1] == 2
    assert checkpoint == first

    class ResumeModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "resume-validation-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ResumeModel)
    assert main(run_args) == 0
    resumed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed_result["outcome"] == "ARTIFACT_READY"
    assert resumed_result["run_id"] == run[0]
    assert resumed_result["checkpoint_after"] == second


def test_source_review_resumes_after_render_process_crash_without_model_retry(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("required column")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    model_calls = 0

    class ValidModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            nonlocal model_calls
            model_calls += 1
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "render-resume-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ValidModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    from db_change_analyzer import workflow as workflow_module
    original_render = workflow_module.render_innova_view

    def crash_render(*_args, **_kwargs):
        raise RuntimeError("simulated render process crash")

    monkeypatch.setattr(workflow_module, "render_innova_view", crash_render)
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 90
    crash_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert crash_result["outcome"] == "INTERNAL_ERROR"
    assert model_calls == 1

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        run = connection.execute(
            "SELECT run_id,status,report_id FROM runs WHERE base_sha=? AND target_sha=?", (first, second)
        ).fetchone()
        unit = connection.execute(
            "SELECT status FROM units WHERE run_id=?", (run[0],)
        ).fetchone()
        checkpoint = connection.execute("SELECT checkpoint_sha FROM scopes").fetchone()[0]
    finally:
        connection.close()
    assert run is not None and run[1] == "ANALYZING" and run[2] is None
    assert unit is not None and unit[0] == "VALIDATED"
    assert checkpoint == first

    monkeypatch.setattr(workflow_module, "render_innova_view", original_render)
    assert main(run_args) == 0
    resumed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed_result["outcome"] == "ARTIFACT_READY"
    assert resumed_result["run_id"] == run[0]
    assert resumed_result["checkpoint_after"] == second
    assert model_calls == 1


def test_source_review_resumes_after_final_validation_process_crash_without_model_retry(tmp_path: Path, capsys, monkeypatch) -> None:
    config = artifact_config_for(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("dependency_depth = 1", "dependency_depth = 0"), encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()
    source = GitFixture(tmp_path / "repo")
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NULL);\n")
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    capsys.readouterr()
    source.write("gpu_user/t.sql", "CREATE TABLE S.T(ID NUMBER NOT NULL);\n")
    second = source.commit("required column")
    git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    model_calls = 0

    class ValidModel:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def complete(self, **kwargs):
            nonlocal model_calls
            model_calls += 1
            payload = kwargs["user_payload"]
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            return ModelReply(json.dumps({
                "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "explanations": [{"explanation_id": "exp", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
            }), "final-validation-model", "stop", None)

    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", ValidModel)
    monkeypatch.setenv("LITELLM_API_KEY", "local-test-key")
    from db_change_analyzer import workflow as workflow_module
    original_report = workflow_module.render_report

    def crash_after_validation(*_args, **_kwargs):
        raise RuntimeError("simulated crash after final validation")

    monkeypatch.setattr(workflow_module, "render_report", crash_after_validation)
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 90
    crash_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert crash_result["outcome"] == "INTERNAL_ERROR"
    assert model_calls == 1

    connection = sqlite3.connect(scope_dir / "state.sqlite3")
    try:
        run = connection.execute(
            "SELECT run_id,status,report_id FROM runs WHERE base_sha=? AND target_sha=?", (first, second)
        ).fetchone()
        unit = connection.execute("SELECT status FROM units WHERE run_id=?", (run[0],)).fetchone()
        checkpoint = connection.execute("SELECT checkpoint_sha FROM scopes").fetchone()[0]
    finally:
        connection.close()
    assert run is not None and run[1] == "ANALYZING" and run[2] is None
    assert unit is not None and unit[0] == "VALIDATED"
    assert checkpoint == first

    monkeypatch.setattr(workflow_module, "render_report", original_report)
    assert main(run_args) == 0
    resumed_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed_result["outcome"] == "ARTIFACT_READY"
    assert resumed_result["run_id"] == run[0]
    assert resumed_result["checkpoint_after"] == second
    assert model_calls == 1
