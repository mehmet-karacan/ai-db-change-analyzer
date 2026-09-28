from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "db_change_analyzer", *args],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_doctor_offline_emits_strict_result_and_makes_no_network_call() -> None:
    completed = _run(
            "--config",
            str(ROOT / "config" / "gpu.example.toml"),
            "doctor",
            "--offline",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    schema = json.loads((ROOT / "schemas" / "result.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)
    diagnostic = json.loads(completed.stderr)
    assert diagnostic["checks"]["network_performed"] is False
    assert diagnostic["checks"]["model_performed"] is False
    assert diagnostic["checks"]["smtp_performed"] is False


def test_state_init_and_status_cli(tmp_path: Path) -> None:
    text = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8")
    text = text.replace('/var/lib/ai-db-change-analyzer', tmp_path.as_posix() + '/state')
    text = text.replace('emit_dir = "./out"', f'emit_dir = "{tmp_path.as_posix()}/out"')
    config = tmp_path / "test.toml"
    config.write_text(text, encoding="utf-8")
    initialized = _run("--config", str(config), "state", "init", "--confirm-new-install")
    assert initialized.returncode == 0, initialized.stderr
    assert json.loads(initialized.stdout)["outcome"] == "STATE_INITIALIZED"
    status = _run("--config", str(config), "state", "status")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["outcome"] == "STATE_OK"
