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
    assert manifest["schema_version"] == "db-change-archive/2.0"
    assert manifest["trigger_exclusion_prefix"] == "db-change-analyzer/reports/"
    assert manifest["analysis_fingerprint"] is None


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


def test_range_archive_fingerprint_is_a_cache_key(tmp_path: Path) -> None:
    report = _report()
    report["analysis_fingerprint"] = "a" * 64
    write_report_archive(tmp_path, report, _files(report))

    assert find_range_archive(
        tmp_path, "gpu-db", "master", "a" * 40, "b" * 40,
        when="2026-10-07T20:00:00+00:00", analysis_fingerprint="a" * 64,
    ) is not None
    assert find_range_archive(
        tmp_path, "gpu-db", "master", "a" * 40, "b" * 40,
        when="2026-10-07T20:00:00+00:00", analysis_fingerprint="b" * 64,
    ) is None


def test_legacy_archive_manifest_remains_readable(tmp_path: Path) -> None:
    report = _report()
    write_report_archive(tmp_path, report, _files(report))
    manifest_path = archive_directory(tmp_path, report) / "archive-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "db-change-archive/1.0"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    assert find_range_archive(tmp_path, "gpu-db", "master", "a" * 40, "b" * 40, when="2026-10-07T20:00:00+00:00") == archive_directory(tmp_path, report)


def test_new_email_html_is_archived_in_the_same_immutable_leaf(tmp_path: Path) -> None:
    report = _report()
    files = _files(report)
    files["report-email.html"] = b"<html>email</html>"
    paths = write_report_archive(tmp_path, report, files)
    destination = archive_directory(tmp_path, report)
    manifest = json.loads((destination / "archive-manifest.json").read_text(encoding="utf-8"))
    assert destination / "report-email.html" in paths
    assert any(item["name"] == "report-email.html" for item in manifest["files"])


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
    report["analysis_fingerprint"] = "a" * 64
    archive_root = tmp_path / "application"
    monkeypatch.setattr("db_change_analyzer.workflow.compute_analysis_fingerprint", lambda *_args, **_kwargs: "a" * 64)
    write_report_archive(archive_root, report, _files(report))
    plan = RangePlan("ANALYZE", "a" * 40, "b" * 40, (), (), (), ())
    monkeypatch.setattr("db_change_analyzer.cli._automatic_plan", lambda *_args, **_kwargs: plan)

    assert main(["--config", str(config), "run", "--offline"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["outcome"] == "RANGE_REPORT_EXISTS"
    assert result["emitted_files"][-1].endswith("archive-manifest.json")


def test_auto_run_does_not_use_archive_from_another_analysis_fingerprint(tmp_path: Path, monkeypatch, capsys) -> None:
    config_text = (Path(__file__).parents[1] / "config" / "gpu.artifact.example.toml").read_text(encoding="utf-8")
    config_text = config_text.replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    config_text = config_text.replace('emit_dir = "./out"', f'emit_dir = "{(tmp_path / "out").as_posix()}"')
    config_text = config_text.replace("archive_enabled = false", "archive_enabled = true")
    config_text = config_text.replace('archive_root = ""', f'archive_root = "{(tmp_path / "application").as_posix()}"')
    config_text = config_text.replace("route_verified = false", "route_verified = true")
    config_text = config_text.replace("capabilities_verified = false", "capabilities_verified = true")
    config_text = config_text.replace('capability_record = ""', 'capability_record = "reviewed/smoke.json"')
    config_text = config_text.replace("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768")
    config = tmp_path / "config.toml"
    config.write_text(config_text, encoding="utf-8")
    assert main(["--config", str(config), "state", "init", "--confirm-new-install"]) == 0
    capsys.readouterr()

    report = _report()
    report["run"]["analysis_completed_at"] = datetime.now(UTC).isoformat()
    report["analysis_fingerprint"] = "a" * 64
    write_report_archive(tmp_path / "application", report, _files(report))
    plan = RangePlan("ANALYZE", "a" * 40, "b" * 40, (), (), (), ())
    monkeypatch.setattr("db_change_analyzer.cli._automatic_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("db_change_analyzer.workflow.compute_analysis_fingerprint", lambda *_args, **_kwargs: "b" * 64)
    called = []
    monkeypatch.setattr("db_change_analyzer.workflow.execute_analysis", lambda *args, **kwargs: called.append(True) or 0)

    assert main(["--config", str(config), "run", "--offline", "--allow-ai"]) == 0
    assert called == [True]
    assert "RANGE_REPORT_EXISTS" not in capsys.readouterr().out
