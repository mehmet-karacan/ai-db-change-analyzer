from __future__ import annotations

import json
from pathlib import Path

import pytest

from db_change_analyzer.config import AppConfig, load_config
from db_change_analyzer.locking import LockBusyError, ScopeLock
from db_change_analyzer.state import SqliteStateStore, StateError


ROOT = Path(__file__).resolve().parents[1]


def config_for(tmp_path: Path) -> AppConfig:
    base = load_config(ROOT / "config" / "gpu.example.toml").model_dump()
    base["state"]["root"] = str(tmp_path / "state")
    base["reports"]["emit_dir"] = str(tmp_path / "out")
    return AppConfig.model_validate(base, strict=True)


def test_g07_init_creates_empty_checkpoint_and_verified_identity(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        initialized = store.initialize()
        status = store.verify()
    assert initialized["installation_uuid"] == status["installation_uuid"]
    assert status["checkpoint_sha"] is None
    assert store.paths.lock.exists()


def test_explicit_v1_to_v5_migration_preserves_state_and_creates_backup(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        store.initialize()
        connection = store.connection()
        try:
            with connection:
                connection.execute("INSERT INTO audit_events(action,actor,reason,created_at) VALUES ('LEGACY_EVENT','test','preserve','2026-09-29T00:00:00+00:00')")
                connection.execute("DROP TABLE report_render_sidecars")
                connection.execute("ALTER TABLE units DROP COLUMN returned_models_json")
                connection.execute("ALTER TABLE runs DROP COLUMN ai_phase_started_at")
                connection.execute("ALTER TABLE runs DROP COLUMN analysis_started_at")
                connection.execute("UPDATE installation SET schema_version=1 WHERE singleton=1")
                connection.execute("PRAGMA user_version=1")
        finally:
            connection.close()
        with pytest.raises(StateError):
            store.verify()
        backup = store.migrate_v5()
        assert backup.is_file()
        assert store.verify()["schema_version"] == 2
        connection = store.connection()
        try:
            assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='LEGACY_EVENT'").fetchone()[0] == 1
            assert connection.execute("SELECT name FROM sqlite_master WHERE name='report_render_sidecars'").fetchone()[0] == "report_render_sidecars"
            assert "returned_models_json" in {row[1] for row in connection.execute("PRAGMA table_info(units)")}
            assert "ai_phase_started_at" in {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            assert "analysis_started_at" in {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        finally:
            connection.close()


def test_g08_run_open_does_not_recreate_missing_database(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        store.initialize()
    store.paths.database.unlink()
    with pytest.raises(StateError, match="missing"):
        store.verify()


def test_s02_owner_uuid_mismatch_fails_closed(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        store.initialize()
    owner = json.loads(store.paths.owner.read_text(encoding="utf-8"))
    owner["installation_uuid"] = "00000000-0000-4000-8000-000000000000"
    store.paths.owner.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(StateError, match="mismatch"):
        store.verify()


def test_s03_second_scope_lock_is_busy_and_lock_file_is_not_deleted(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with ScopeLock(path):
        with pytest.raises(LockBusyError):
            with ScopeLock(path):
                pass
    assert path.exists()


def test_checkpoint_compare_and_swap_and_backup_restore(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        store.initialize()
        store.compare_and_swap_checkpoint(None, "a" * 40, 1)
        backup = store.backup()
        store.compare_and_swap_checkpoint("a" * 40, "b" * 40, 1)
        assert store.verify()["checkpoint_sha"] == "b" * 40
        store.restore(backup, "fault test restore")
        assert store.verify()["checkpoint_sha"] == "a" * 40


def test_checkpoint_compare_and_swap_rejects_stale_expected_base(tmp_path: Path) -> None:
    store = SqliteStateStore(config_for(tmp_path))
    with store.lock():
        store.initialize()
        store.compare_and_swap_checkpoint(None, "a" * 40, 1)
        with pytest.raises(StateError, match="compare-and-swap"):
            store.compare_and_swap_checkpoint(None, "b" * 40, 1)
