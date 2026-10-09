from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from db_change_analyzer.config import load_config
from db_change_analyzer.research_journal import ResearchJournal
from db_change_analyzer.retention import RetentionManager
from db_change_analyzer.state import SqliteStateStore
from db_change_analyzer.state import StateError


ROOT = Path(__file__).resolve().parents[1]


def test_cleanup_requires_owned_scope_and_explicit_apply(tmp_path: Path) -> None:
    text = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8").replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    config = load_config(path)
    store = SqliteStateStore(config)
    store.initialize()
    old = store.paths.backups / "old.sqlite3"
    old.write_bytes(b"old")
    timestamp = (datetime.now(UTC) - timedelta(days=10)).timestamp()
    os.utime(old, (timestamp, timestamp))
    manager = RetentionManager(store, config.retention)
    manifest = manager.plan()
    assert old.exists() and [item["path"] for item in manifest["candidates"]] == [str(old)]
    applied = manager.apply(manifest)
    assert not old.exists() and applied["removed"] == [str(old)]


def test_cleanup_rejects_symlinks_and_forged_paths(tmp_path: Path) -> None:
    text = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8").replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    config_path = tmp_path / "config.toml"
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    store = SqliteStateStore(config)
    store.initialize()
    protected = store.paths.scope / "protected.txt"
    protected.write_bytes(b"keep")
    old_time = (datetime.now(UTC) - timedelta(days=10)).timestamp()
    os.utime(protected, (old_time, old_time))
    manager = RetentionManager(store, config.retention)
    forged = {"scope_hash": config.scope_hash, "applied": False,
              "candidates": [{"path": str(protected), "kind": "old_backup", "bytes": 4}]}
    with pytest.raises(StateError, match="permitted area"):
        manager.apply(forged)
    assert protected.read_bytes() == b"keep"
    link = store.paths.backups / "old-link.txt"
    try:
        link.symlink_to(protected)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")
    assert manager.plan()["candidates"] == []
    assert protected.read_bytes() == b"keep"


def test_cleanup_removes_only_old_inactive_research_generation(tmp_path: Path) -> None:
    text = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8").replace('root = "/var/lib/ai-db-change-analyzer"', f'root = "{(tmp_path / "state").as_posix()}"')
    config_path = tmp_path / "config.toml"
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    store = SqliteStateStore(config)
    store.initialize()
    journal = ResearchJournal(store.paths.scope, scope_hash=config.scope_hash, run_id="old-run", generation=0)
    journal.append_unit_status(
        unit_id="unit-1", attempt=1, status="VALIDATED", request_digest="c" * 64,
        result_digest="d" * 64, diagnostics=[],
    )
    old_time = (datetime.now(UTC) - timedelta(days=config.retention.receipt_days + 1)).timestamp()
    os.utime(journal.directory, (old_time, old_time))
    os.utime(journal.directory / "00000001.json", (old_time, old_time))
    os.utime(journal.index_path, (old_time, old_time))

    manager = RetentionManager(store, config.retention)
    manifest = manager.plan()
    candidates = [item for item in manifest["candidates"] if item["kind"] == "old_research_journal"]
    assert len(candidates) == 1
    applied = manager.apply(manifest)
    assert not journal.directory.exists()
    assert str(journal.directory) in applied["removed"]
