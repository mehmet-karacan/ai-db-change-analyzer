from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from db_change_analyzer.cli import main
from db_change_analyzer.litellm_http import ModelReply
from tests.helpers.git_fixture import GitFixture


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, *, individual: bool) -> Path:
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
    if individual:
        text = text.replace('review_mode = "source_review"', 'review_mode = "legacy_commentary"')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _prepare(tmp_path: Path, count: int, *, individual: bool) -> tuple[Path, GitFixture, Path]:
    config = _config(tmp_path, individual=individual)
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    source = GitFixture(tmp_path / "repo")
    initial = "\n".join(f"CREATE TABLE T_{index} (ID NUMBER NULL);" for index in range(count)) + "\n"
    source.write("gpu_user/tables.sql", initial)
    first = source.commit("initial")
    scope_dir = next((tmp_path / "state" / "scopes").iterdir())
    cache = scope_dir / "source.git"
    cache.rmdir()
    _git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
    _git("update-ref", "refs/remotes/source/master", first, cwd=cache)
    assert main(["--config", str(config), "run", "--offline"]) == 0
    source.write("gpu_user/tables.sql", "\n".join(f"CREATE TABLE T_{index} (ID NUMBER NOT NULL);" for index in range(count)) + "\n")
    second = source.commit("change")
    _git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)
    return config, source, scope_dir


class _FakeModel:
    calls: list[dict]

    def __init__(self, _config):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def complete(self, **kwargs):
        payload = kwargs["user_payload"]
        self.calls.append(payload)
        if payload["schema_version"] == "aggregate-review-input/1.0":
            evidence_id = payload["evidence_registry"][0]["evidence_id"]
            response = {
                "schema_version": "aggregate-review/1.0",
                "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "commentary_tr": "Toplu değişikliklerde öncelikli nesne türleri ve deterministik risk dağılımı birlikte değerlendirilmelidir.",
                "evidence_ids": [evidence_id],
                "limitations": ["Değerlendirme yalnız sağlanan deterministik özetle sınırlıdır."],
            }
        else:
            response = {
                "schema_version": "mail-commentary/1.1",
                "report_id": payload["report_id"],
                "unit_id": payload["unit_id"],
                "input_digest": payload["input_digest"],
                "summary": None,
                "interpretations": [],
                "uncertainties": [],
                "recommended_checks": [],
            }
        return ModelReply(json.dumps(response, ensure_ascii=False), "mock-model", "stop", None)


@pytest.mark.parametrize("count,expected_calls", [(11, 1), (46, 1)])
def test_more_than_threshold_uses_one_aggregate_call(tmp_path, capsys, monkeypatch, count, expected_calls) -> None:
    config, _source, _scope_dir = _prepare(tmp_path, count, individual=False)
    calls: list[dict] = []
    _FakeModel.calls = calls
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", _FakeModel)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))

    assert len(calls) == expected_calls
    assert calls[0]["summary"]["object_count"] == count
    assert all("raw_sql" not in call and "source_snippet" not in call for call in calls)
    assert view["analysis"]["ai"]["mode"] == "aggregate"
    assert view["analysis"]["ai"]["requested_units"] == 1
    assert view["analysis"]["ai"]["suppressed_object_count"] == count
    assert all(item["ai_status"] == "withheld_threshold" for item in view["objects"])
    assert result["quality"] in {"complete", "limited"}


def test_at_threshold_keeps_individual_calls(tmp_path, capsys, monkeypatch) -> None:
    config, _source, _scope_dir = _prepare(tmp_path, 10, individual=True)
    calls: list[dict] = []
    _FakeModel.calls = calls
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", _FakeModel)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    capsys.readouterr()
    assert len(calls) == 10


def test_aggregate_model_failure_keeps_deterministic_report(tmp_path, capsys, monkeypatch) -> None:
    config, _source, _scope_dir = _prepare(tmp_path, 11, individual=False)
    calls: list[dict] = []

    class InvalidAggregateModel(_FakeModel):
        def complete(self, **kwargs):
            self.calls.append(kwargs["user_payload"])
            return ModelReply("{}", "mock-model", "stop", None)

    _FakeModel.calls = calls
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", InvalidAggregateModel)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    view = json.loads((tmp_path / "out" / "mail-view.json").read_text(encoding="utf-8"))

    assert len(calls) == 1
    assert result["quality"] == "limited"
    assert view["analysis"]["ai"]["status"] == "failed"
    assert view["analysis"]["ai"]["aggregate_error_code"] == "AGGREGATE_REVIEW_INVALID"
    assert all(item["ai_status"] == "withheld_threshold" for item in view["objects"])


def test_successful_aggregate_call_is_not_repeated_after_render_resume(tmp_path, capsys, monkeypatch) -> None:
    config, _source, scope_dir = _prepare(tmp_path, 11, individual=False)
    calls: list[dict] = []
    _FakeModel.calls = calls
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", _FakeModel)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    from db_change_analyzer import workflow as workflow_module

    original_render = workflow_module.render_innova_view

    def crash_render(*_args, **_kwargs):
        raise RuntimeError("simulated render process crash")

    monkeypatch.setattr(workflow_module, "render_innova_view", crash_render)
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 90
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first["outcome"] == "INTERNAL_ERROR"
    assert len(calls) == 1
    with sqlite3.connect(scope_dir / "state.sqlite3") as connection:
        run_id = connection.execute("SELECT run_id FROM runs WHERE report_id IS NULL ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    monkeypatch.setattr(workflow_module, "render_innova_view", original_render)
    assert main(run_args) == 0
    resumed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed["outcome"] == "ARTIFACT_READY"
    assert resumed["run_id"] == run_id
    assert len(calls) == 1


def test_stale_completed_render_run_is_abandoned_when_target_advances(tmp_path, capsys, monkeypatch) -> None:
    config, source, scope_dir = _prepare(tmp_path, 11, individual=False)
    first = _git("rev-parse", "HEAD~1", cwd=source.root)
    calls: list[dict] = []
    _FakeModel.calls = calls
    monkeypatch.setattr("db_change_analyzer.workflow.LiteLLMClient", _FakeModel)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    from db_change_analyzer import workflow as workflow_module
    from db_change_analyzer.reporting import ReportError

    original_render = workflow_module.render_innova_view

    def crash_render(*_args, **_kwargs):
        raise ReportError("REPORT_SCHEMA_INVALID:evidence_registry/103:oneOf")

    monkeypatch.setattr(workflow_module, "render_innova_view", crash_render)
    run_args = ["--config", str(config), "run", "--offline", "--allow-ai"]
    assert main(run_args) == 50
    failed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert failed["outcome"] == "SAFETY_POLICY_VIOLATION"
    assert len(calls) == 1
    with sqlite3.connect(scope_dir / "state.sqlite3") as connection:
        old_run_id = connection.execute(
            "SELECT run_id FROM runs WHERE report_id IS NULL ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        cache = scope_dir / "source.git"
    config.write_text(
        config.read_text(encoding="utf-8").replace("max_job_seconds = 1500", "max_job_seconds = 1501"),
        encoding="utf-8",
    )

    source.branch("alternate", first)
    source.write(
        "gpu_user/tables.sql",
        "\n".join(f"CREATE TABLE T_{index} (ID NUMBER NOT NULL);" for index in range(11)) + "\n",
    )
    source.write("gpu_user/new.sql", "CREATE TABLE S.NEW_OBJECT(ID NUMBER);\n")
    third = source.commit("new change after failed render")
    _git("fetch", str(source.root), f"+{third}:refs/remotes/source/master", cwd=cache)

    monkeypatch.setattr(workflow_module, "render_innova_view", original_render)
    assert main(run_args) == 0
    resumed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert resumed["outcome"] == "ARTIFACT_READY"
    assert resumed["run_id"] != old_run_id
    assert resumed["checkpoint_after"] == third
    assert len(calls) == 2
    with sqlite3.connect(scope_dir / "state.sqlite3") as connection:
        old_status = connection.execute("SELECT status FROM runs WHERE run_id=?", (old_run_id,)).fetchone()[0]
    assert old_status == "CLOSED"
