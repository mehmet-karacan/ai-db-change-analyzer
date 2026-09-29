from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
from pathlib import Path

from db_change_analyzer.cli import _new_outbox, main
from db_change_analyzer.config import load_config
from db_change_analyzer.litellm_http import ModelReply
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


def test_unknown_oracle_type_keeps_generic_v5_source_notice(tmp_path: Path, capsys, monkeypatch) -> None:
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
            raise AssertionError("unsupported type must not be sent to the model")

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
    assert view["objects"][0]["identity"]["object_type"] == "UNKNOWN"
    assert view["objects"][0]["verification"] == "limited"
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
