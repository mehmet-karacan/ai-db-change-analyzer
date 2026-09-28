from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import AppConfig
from .locking import ScopeLock


SCHEMA_VERSION = 1


class StateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = (_canonical(value) + "\n").encode("utf-8")
    with temporary.open("xb") as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


@dataclass(frozen=True, slots=True)
class ScopePaths:
    root: Path
    installation: Path
    scope: Path
    owner: Path
    lock: Path
    database: Path
    source: Path
    exports: Path
    backups: Path

    @classmethod
    def from_config(cls, config: AppConfig) -> "ScopePaths":
        root = Path(config.state.root).expanduser().resolve()
        scope = root / "scopes" / config.scope_hash
        return cls(
            root=root,
            installation=root / "INSTALLATION.json",
            scope=scope,
            owner=scope / "owner.json",
            lock=scope / "run.lock",
            database=scope / "state.sqlite3",
            source=scope / "source.git",
            exports=scope / "exports",
            backups=scope / "backups",
        )


class SqliteStateStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = ScopePaths.from_config(config)

    def lock(self) -> ScopeLock:
        return ScopeLock(self.paths.lock, self.config.state.lock_wait_seconds)

    def initialize(self) -> dict[str, str]:
        paths = self.paths
        paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if paths.installation.exists():
            installation = self._load_json(paths.installation)
            installation_uuid = self._required_uuid(installation, "installation_uuid")
        else:
            root_entries = list(paths.root.iterdir())
            unexpected_root = [item for item in root_entries if item.name != "scopes"]
            scopes_root = paths.root / "scopes"
            unexpected_scopes = [] if not scopes_root.exists() else [item for item in scopes_root.iterdir() if item.resolve() != paths.scope]
            unexpected_scope_entries = [] if not paths.scope.exists() else [item for item in paths.scope.iterdir() if item.name != "run.lock"]
            if unexpected_root or unexpected_scopes or unexpected_scope_entries:
                raise StateError("state root is non-empty and has no installation marker")
            installation_uuid = str(uuid.uuid4())
            _atomic_json(paths.installation, {"schema_version": 1, "installation_uuid": installation_uuid, "created_at": _now()})

        paths.scope.mkdir(parents=True, exist_ok=True, mode=0o700)
        if paths.owner.exists() or paths.database.exists():
            self.verify_markers()
            raise StateError("scope is already initialized")
        owner = {
            "schema_version": 1,
            "installation_uuid": installation_uuid,
            "scope_hash": self.config.scope_hash,
            "repository_id": self.config.repository.id,
            "created_at": _now(),
        }
        _atomic_json(paths.owner, owner)
        paths.source.mkdir(mode=0o700)
        paths.exports.mkdir(mode=0o700)
        paths.backups.mkdir(mode=0o700)
        connection = self._connect(create=True)
        try:
            migration = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
            connection.executescript(migration)
            with connection:
                connection.execute(
                    "INSERT INTO installation(singleton, installation_uuid, schema_version, created_at) VALUES (1, ?, ?, ?)",
                    (installation_uuid, SCHEMA_VERSION, _now()),
                )
                connection.execute(
                    "INSERT INTO scopes(scope_hash, repository_id, remote_digest, branch, scope_json, epoch, checkpoint_sha, initialized_at) VALUES (?, ?, ?, ?, ?, 1, NULL, ?)",
                    (
                        self.config.scope_hash,
                        self.config.repository.id,
                        hashlib.sha256(self.config.repository.url.encode()).hexdigest(),
                        self.config.repository.branch,
                        _canonical(self.config.scope.model_dump(mode="json")),
                        _now(),
                    ),
                )
                connection.execute(
                    "INSERT INTO audit_events(scope_hash, action, actor, reason, created_at) VALUES (?, 'STATE_INITIALIZED', 'operator', 'confirm-new-install', ?)",
                    (self.config.scope_hash, _now()),
                )
        except Exception:
            connection.close()
            if paths.database.exists():
                paths.database.unlink()
            raise
        finally:
            try:
                connection.close()
            except Exception:
                pass
        os.chmod(paths.database, 0o600)
        return {"installation_uuid": installation_uuid, "scope_hash": self.config.scope_hash}

    def _connect(self, *, create: bool = False) -> sqlite3.Connection:
        if not create and not self.paths.database.is_file():
            raise StateError("state database is missing")
        connection = sqlite3.connect(self.paths.database, isolation_level=None, timeout=self.config.state.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(f"PRAGMA busy_timeout = {self.config.state.busy_timeout_ms:d}")
        return connection

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise StateError("marker is missing or is not a regular file")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateError("marker is unreadable") from exc
        if not isinstance(value, dict):
            raise StateError("marker has invalid shape")
        return value

    @staticmethod
    def _required_uuid(value: dict[str, Any], key: str) -> str:
        try:
            return str(uuid.UUID(str(value[key])))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("marker UUID is invalid") from exc

    def verify_markers(self) -> str:
        installation = self._load_json(self.paths.installation)
        owner = self._load_json(self.paths.owner)
        installation_uuid = self._required_uuid(installation, "installation_uuid")
        if self._required_uuid(owner, "installation_uuid") != installation_uuid:
            raise StateError("installation and owner UUID mismatch")
        if owner.get("scope_hash") != self.config.scope_hash:
            raise StateError("owner scope mismatch")
        return installation_uuid

    def verify(self) -> dict[str, Any]:
        installation_uuid = self.verify_markers()
        connection = self._connect()
        try:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            row = connection.execute(
                """SELECT
                       installation_uuid,
                       schema_version
                   FROM installation
                   WHERE singleton = 1"""
            ).fetchone()
            scope = connection.execute(
                """SELECT
                       epoch,
                       checkpoint_sha
                   FROM scopes
                   WHERE scope_hash = ?""",
                (self.config.scope_hash,),
            ).fetchone()
        finally:
            connection.close()
        if quick != "ok" or version != SCHEMA_VERSION or row is None or scope is None:
            raise StateError("state integrity or schema validation failed")
        if row["installation_uuid"] != installation_uuid or row["schema_version"] != SCHEMA_VERSION:
            raise StateError("database installation identity mismatch")
        return {"installation_uuid": installation_uuid, "schema_version": version, "epoch": scope["epoch"], "checkpoint_sha": scope["checkpoint_sha"]}

    def recover_inflight_notifications(self) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT
                       attempt_id,
                       notification_id
                   FROM notification_attempts
                   WHERE state = 'INFLIGHT'"""
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE notification_attempts SET state = 'UNKNOWN', completed_at = ? WHERE attempt_id = ? AND state = 'INFLIGHT'",
                    (_now(), row["attempt_id"]),
                )
                connection.execute(
                    "UPDATE notifications SET status = 'UNKNOWN' WHERE notification_id = ?",
                    (row["notification_id"],),
                )
                connection.execute(
                    "UPDATE notification_recipients SET status = 'UNKNOWN' WHERE notification_id = ? AND status = 'PENDING'",
                    (row["notification_id"],),
                )
            connection.commit()
            return len(rows)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def compare_and_swap_checkpoint(self, expected: str | None, target: str, epoch: int, *, run_id: str | None = None) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE scopes
                   SET checkpoint_sha = ?
                   WHERE scope_hash = ?
                     AND epoch = ?
                     AND checkpoint_sha IS ?""",
                (target, self.config.scope_hash, epoch, expected),
            )
            if cursor.rowcount != 1:
                raise StateError("checkpoint compare-and-swap failed")
            connection.execute(
                "INSERT INTO audit_events(scope_hash, run_id, action, actor, expected_base, target, created_at) VALUES (?, ?, 'CHECKPOINT_ADVANCED', 'system', ?, ?, ?)",
                (self.config.scope_hash, run_id, expected, target, _now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self) -> Path:
        status = self.verify()
        self.paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        destination = self.paths.backups / f"state-{backup_id}.sqlite3"
        source = self._connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(destination, 0o600)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        _atomic_json(
            destination.with_suffix(".manifest.json"),
            {
                "schema_version": 1,
                "installation_uuid": status["installation_uuid"],
                "scope_hash": self.config.scope_hash,
                "database_sha256": digest,
                "created_at": _now(),
            },
        )
        check = sqlite3.connect(destination)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StateError("backup integrity check failed")
        finally:
            check.close()
        return destination

    def restore(self, backup: Path, reason: str) -> None:
        if not reason.strip():
            raise StateError("restore reason is required")
        backup = backup.resolve(strict=True)
        manifest = self._load_json(backup.with_suffix(".manifest.json"))
        installation_uuid = self.verify_markers()
        if manifest.get("installation_uuid") != installation_uuid or manifest.get("scope_hash") != self.config.scope_hash:
            raise StateError("backup identity mismatch")
        if hashlib.sha256(backup.read_bytes()).hexdigest() != manifest.get("database_sha256"):
            raise StateError("backup digest mismatch")
        check = sqlite3.connect(backup)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StateError("backup integrity check failed")
        finally:
            check.close()
        temporary = self.paths.database.with_name(f"state.restore.{uuid.uuid4().hex}.sqlite3")
        source = sqlite3.connect(backup)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.replace(temporary, self.paths.database)
        self.verify()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO audit_events(scope_hash, action, actor, reason, evidence_reference, created_at) VALUES (?, 'STATE_RESTORED', 'operator', ?, ?, ?)",
                    (self.config.scope_hash, reason, manifest["database_sha256"], _now()),
                )
        finally:
            connection.close()

    def connection(self) -> sqlite3.Connection:
        self.verify_markers()
        return self._connect()
