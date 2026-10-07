from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db_change_analyzer.archive import ArchiveError, archive_directory, find_range_archive, write_report_archive
from db_change_analyzer.cli import main
from db_change_analyzer.history import RangePlan


def _report() -> dict:
    return {
        "report_id": "11111111-1111-4111-8111-111111111111",
        "run": {
            "repository_id": "gpu-db",
            "branch": "master",
            "base_sha": "a" * 40,
            "target_sha": "b" * 40,
            "analysis_completed_at": "2026-10-07T14:01:25+00:00",
        },
    }


def _files(report: dict, html: bytes = b"<html></html>") -> dict[str, bytes]:
    return {
        "report.json": (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        "report.html": html,
        "report.txt": b"report",
        "mail-view.json": b"{}\n",
        "render-manifest.json": b"{}\n",
    }


def test_report_archive_is_dated_immutable_and_idempotent(tmp_path: Path) -> None:
    report = _report()
    first = write_report_archive(tmp_path, report, _files(report))
    second = write_report_archive(tmp_path, report, _files(report))

    assert first == second
    destination = archive_directory(tmp_path, report)
    assert destination == tmp_path / "db-change-analyzer" / "reports" / "2026" / "2026-10-07" / "gpu-db" / "master" / f"{'a' * 40}_{'b' * 40}_{report['report_id']}"
    assert find_range_archive(tmp_path, "gpu-db", "master", "a" * 40, "b" * 40, when="2026-10-07T20:00:00+00:00") == destination
    manifest = json.loads((destination / "archive-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "db-change-archive/1.0"
    assert manifest["trigger_exclusion_prefix"] == "db-change-analyzer/reports/"


def test_report_archive_rejects_changed_content_at_existing_leaf(tmp_path: Path) -> None:
    report = _report()
    write_report_archive(tmp_path, report, _files(report))
    with pytest.raises(ArchiveError, match="ARCHIVE_COLLISION"):
        write_report_archive(tmp_path, report, _files(report, html=b"changed"))


def test_range_gate_ignores_other_range_repository_and_branch(tmp_path: Path) -> None:
    report = _report()
    write_report_archive(tmp_path, report, _files(report))
    assert find_range_archive(tmp_path, "gpu-db", "master", "b" * 40, "c" * 40, when="2026-10-07T20:00:00+00:00") is None
    assert find_range_archive(tmp_path, "gpu-db", "release", "a" * 40, "b" * 40, when="2026-10-07T20:00:00+00:00") is None
    assert find_range_archive(tmp_path, "sky-db", "master", "a" * 40, "b" * 40, when="2026-10-07T20:00:00+00:00") is None


def test_same_day_ranges_are_grouped_but_deduplicated_separately(tmp_path: Path) -> None:
    first = _report()
    second = _report()
    second["report_id"] = "22222222-2222-4222-8222-222222222222"
    second["run"]["base_sha"] = "b" * 40
    second["run"]["target_sha"] = "c" * 40
    second["run"]["analysis_completed_at"] = first["run"]["analysis_completed_at"]
    write_report_archive(tmp_path, first, _files(first))
    write_report_archive(tmp_path, second, _files(second))

    assert find_range_archive(tmp_path, "gpu-db", "master", "a" * 40, "b" * 40, when="2026-10-07T20:00:00+00:00") is not None
    assert find_range_archive(tmp_path, "gpu-db", "master", "b" * 40, "c" * 40, when="2026-10-07T20:00:00+00:00") is not None


def test_auto_run_skips_before_model_and_mail_when_exact_range_is_archived(tmp_path: Path, monkeypatch, capsys) -> None:
    config_text = (Path(__file__).parents[1] / "config" / "gpu.example.toml").read_text(encoding="utf-8")
    config_text = config_text.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    config_text = config_text.replace("archive_enabled = false", "archive_enabled = true")
    config_text = config_text.replace('archive_root = ""', f'archive_root = "{(tmp_path / "application").as_posix()}"')
    config = tmp_path / "config.toml"
    config.write_text(config_text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()

    report = _report()
    report["run"]["analysis_completed_at"] = datetime.now(UTC).isoformat()
    archive_root = tmp_path / "application"
    write_report_archive(archive_root, report, _files(report))
    plan = RangePlan("ANALYZE", "a" * 40, "b" * 40, (), (), (), ())
    monkeypatch.setattr("db_change_analyzer.cli._automatic_plan", lambda *_args, **_kwargs: plan)

    assert main(["--config", str(config), "run", "--offline"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "RANGE_REPORT_EXISTS"
    assert result["emitted_files"][-1].endswith("archive-manifest.json")
