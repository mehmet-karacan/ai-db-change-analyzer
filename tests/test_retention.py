from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from db_change_analyzer.config import load_config
from db_change_analyzer.retention import RetentionManager
from db_change_analyzer.state import SqliteStateStore


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
