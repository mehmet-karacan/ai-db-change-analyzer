from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


def test_doctor_offline_emits_strict_result_and_makes_no_network_call() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "db_change_analyzer",
            "--config",
            str(ROOT / "config" / "gpu.example.toml"),
            "doctor",
            "--offline",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    schema = json.loads((ROOT / "schemas" / "result.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)
    diagnostic = json.loads(completed.stderr)
    assert diagnostic["checks"]["network_performed"] is False
    assert diagnostic["checks"]["model_performed"] is False
    assert diagnostic["checks"]["smtp_performed"] is False
