from __future__ import annotations

import json
import subprocess
from pathlib import Path

from db_change_analyzer.cli import main
from db_change_analyzer.litellm_http import ModelReply
from db_change_analyzer.smtp_transport import SmtpResult

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
    source.write("gpu_user/t.sql", "CREATE TABLE t(id NUMBER);\n")
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
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            content = json.dumps({"schema_version": "1.0", "unit_id": payload["unit_id"], "summary_tr": "Kaynak degisikligi incelendi.", "summary_evidence_ids": [evidence_id], "findings": [], "limitations": []})
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
