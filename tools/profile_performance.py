"""Measure local workflow stages without contacting external services.

The scale fixture uses the same 41-object shape as the regression test and a
deterministic local model.  The output is diagnostic data only; it is not a
model-quality or production acceptance result.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_change_analyzer.cli import main
from db_change_analyzer.litellm_http import ModelReply
from tests.helpers.git_fixture import GitFixture


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _artifact_config(tmp_path: Path) -> Path:
    text = (ROOT / "config" / "gpu.artifact.example.toml").read_text(encoding="utf-8")
    text = text.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    text = text.replace('emit_dir = "./out"', f'emit_dir = "{(tmp_path / "out").as_posix()}"')
    for old, new in (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = "profile/local.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
        ("dependency_depth = 1", "dependency_depth = 0"),
    ):
        text = text.replace(old, new)
    path = tmp_path / "profile-config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class _Trace:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, stage: str, started: float, finished: float, started_at: datetime, finished_at: datetime, *, objects: int | None = None, detail: dict[str, Any] | None = None) -> None:
        self.items.append({
            "stage": stage,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "elapsed_ms": round((finished - started) * 1000, 3),
            "objects": objects,
            "average_ms_per_object": round((finished - started) * 1000 / objects, 3) if objects else None,
            "detail": detail or {},
        })

    def wrap(self, module: Any, name: str, *, objects: Callable[[Any], int | None] | None = None, detail: Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]] | None = None) -> None:
        original = getattr(module, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            started_at = datetime.now(UTC)
            result = original(*args, **kwargs)
            finished = time.perf_counter()
            finished_at = datetime.now(UTC)
            count = objects(result) if objects else None
            extra = detail(args, kwargs, result) if detail else None
            self.add(name, started, finished, started_at, finished_at, objects=count, detail=extra)
            return result

        setattr(module, name, wrapped)


def _run(object_count: int) -> dict[str, Any]:
    trace = _Trace()
    with tempfile.TemporaryDirectory(prefix="db-change-profile-") as temporary:
        tmp_path = Path(temporary)
        config = _artifact_config(tmp_path)
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet:
            init_code = main(["--config", str(config), "state", "init", "--confirm-new-install"])
        source = GitFixture(tmp_path / "repo")
        for index in range(object_count):
            source.write(f"gpu_user/t{index:03d}.sql", f"CREATE TABLE S.T{index:03d}(ID NUMBER NULL);\n")
        first = source.commit("profile initial")
        scope_dir = next((tmp_path / "state" / "scopes").iterdir())
        cache = scope_dir / "source.git"
        cache.rmdir()
        _git("clone", "--bare", str(source.root), str(cache), cwd=tmp_path)
        _git("update-ref", "refs/remotes/source/master", first, cwd=cache)
        with quiet:
            baseline_code = main(["--config", str(config), "run", "--offline"])
        for index in range(object_count):
            source.write(f"gpu_user/t{index:03d}.sql", f"CREATE TABLE S.T{index:03d}(ID NUMBER NOT NULL);\n")
        second = source.commit("profile changed")
        _git("fetch", str(source.root), f"{second}:refs/remotes/source/master", cwd=cache)

        import db_change_analyzer.workflow as workflow

        trace.wrap(workflow, "inventory_revision", objects=lambda result: len(result), detail=lambda args, kwargs, result: {"revision": args[1], "parse_paths": len(kwargs.get("parse_paths", set()))})
        trace.wrap(workflow, "_index", objects=lambda result: len(result))
        trace.wrap(workflow, "_attach_table_contexts", objects=lambda result: sum(len(item) for item in result[0].values()))
        trace.wrap(workflow, "_verify_changed_refs")
        trace.wrap(workflow, "render_report", objects=lambda result: None)
        trace.wrap(workflow, "build_innova_mail_view", objects=lambda result: len(result.get("objects", [])))
        trace.wrap(workflow, "render_innova_view", objects=lambda result: getattr(result, "object_cards", None))
        trace.wrap(workflow, "build_innova_render_manifest")
        trace.wrap(workflow, "_stage_file", objects=lambda result: 1)
        trace.wrap(workflow, "validate_source_review", objects=lambda result: 1)
        workflow._research_receipts = lambda *args, **kwargs: []

        class ProfileModel:
            def __init__(self, _config):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def complete(self, **kwargs):
                started = time.perf_counter()
                started_at = datetime.now(UTC)
                payload = kwargs["user_payload"]
                evidence_id = payload["evidence_registry"][0]["evidence_id"]
                result = ModelReply(json.dumps({
                    "schema_version": "source-review/1.0", "unit_id": payload["unit_id"],
                    "input_digest": payload["input_digest"],
                    "explanations": [{"explanation_id": "profile", "kind": "change", "text_tr": "Kolon zorunlu hale getirildi.", "evidence_ids": [evidence_id]}],
                    "findings": [], "conclusion": "no_finding", "limitations": [], "unfinished_research": [],
                }), "profile-local-model", "stop", None)
                trace.add("model.complete", started, time.perf_counter(), started_at, datetime.now(UTC), objects=1, detail={"returned_model": result.returned_model})
                return result

        workflow.LiteLLMClient = ProfileModel
        os.environ["LITELLM_API_KEY"] = "local-profile-key"
        results: list[dict[str, Any]] = []
        pending = True
        invocation = 0
        while pending:
            invocation += 1
            started = time.perf_counter()
            with quiet:
                code = main(["--config", str(config), "run", "--offline", "--allow-ai"])
            elapsed = time.perf_counter() - started
            pending = code == 11
            results.append({"invocation": invocation, "exit_code": code, "elapsed_ms": round(elapsed * 1000, 3), "objects": object_count if not pending else min(40, object_count)})
            if code not in {0, 11}:
                break
        return {
            "tool": "profile_performance",
            "mode": "offline_local_mock",
            "object_count": object_count,
            "init_exit_code": init_code,
            "baseline_exit_code": baseline_code,
            "invocations": results,
            "stages": trace.items,
            "note": "Mock model, local Git/SQLite and artifact renderer only; external model/Jenkins/Oracle/Outlook acceptance is not measured.",
        }


def main_profile() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objects", type=int, default=41)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = _run(args.objects)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_profile())
