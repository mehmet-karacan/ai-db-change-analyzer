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


SCHEMA_VERSION = 2


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
    research: Path

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
            research=scope / "research",
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
        paths.research.mkdir(mode=0o700)
        connection = self._connect(create=True)
        try:
            migration = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
            connection.executescript(migration)
            v5_migration = (Path(__file__).parent / "migrations" / "002_v5_sidecars.sql").read_text(encoding="utf-8")
            connection.executescript(v5_migration)
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
        # DML under `with connection:` must be a real transaction. Explicit
        # BEGIN IMMEDIATE in state transitions still overrides this default.
        connection = sqlite3.connect(self.paths.database, isolation_level="DEFERRED", timeout=self.config.state.busy_timeout_ms / 1000)
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

    def migrate_v5(self) -> Path:
        """Upgrade a verified v1 state in place after a SQLite backup.

        The caller holds the scope lock. Existing reports and MIME rows are
        untouched; only a linked sidecar table and the schema version change.
        """
        installation_uuid = self.verify_markers()
        connection = self._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            row = connection.execute("SELECT installation_uuid,schema_version FROM installation WHERE singleton=1").fetchone()
            if version != 1 or row is None or row["schema_version"] != 1 or row["installation_uuid"] != installation_uuid:
                raise StateError("v1 state migration preconditions failed")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StateError("state integrity check failed before migration")
            self.paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup_path = self.paths.backups / f"pre-v5-{uuid.uuid4().hex}.sqlite3"
            backup_connection = sqlite3.connect(backup_path)
            try:
                connection.backup(backup_connection)
            finally:
                backup_connection.close()
            os.chmod(backup_path, 0o600)
            migration = (Path(__file__).parent / "migrations" / "002_v5_sidecars.sql").read_text(encoding="utf-8")
            try:
                connection.executescript(migration)
            except sqlite3.Error as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise StateError("v5 state migration failed") from exc
        finally:
            connection.close()
        self.verify()
        return backup_path

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

    def get_cache_entry(self, cache_key: str, *, kind: str, version_fingerprint: str) -> bytes | None:
        """Read a verified optimization cache entry without using it as state."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT validated_content, content_hash FROM cache_entries WHERE cache_key=? AND kind=? AND version_fingerprint=?",
                (cache_key, kind, version_fingerprint),
            ).fetchone()
            if row is None:
                return None
            content = bytes(row["validated_content"])
            if hashlib.sha256(content).hexdigest() != row["content_hash"]:
                with connection:
                    connection.execute("DELETE FROM cache_entries WHERE cache_key=?", (cache_key,))
                return None
            with connection:
                connection.execute("UPDATE cache_entries SET accessed_at=? WHERE cache_key=?", (_now(), cache_key))
            return content
        finally:
            connection.close()

    def put_cache_entry(self, cache_key: str, content: bytes, *, kind: str, version_fingerprint: str) -> None:
        """Persist a bounded, content-hashed optimization result."""
        if len(content) > 1024 * 1024:
            raise StateError("cache entry exceeds configured limit")
        digest = hashlib.sha256(content).hexdigest()
        now = _now()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO cache_entries(cache_key,kind,version_fingerprint,validated_content,content_hash,created_at,accessed_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(cache_key) DO UPDATE SET kind=excluded.kind,
                       version_fingerprint=excluded.version_fingerprint,validated_content=excluded.validated_content,
                       content_hash=excluded.content_hash,accessed_at=excluded.accessed_at""",
                    (cache_key, kind, version_fingerprint, content, digest, now, now),
                )
        finally:
            connection.close()

    def status(self) -> dict[str, Any]:
        base = self.verify()
        connection = self._connect()
        try:
            base.update(
                {
                    "unfinished_runs": connection.execute("""SELECT
                        COUNT(*)
                    FROM runs
                    WHERE status NOT IN ('BASELINED','NO_CHANGE','OUT_OF_SCOPE_ONLY','COMMITTED','CLOSED')""").fetchone()[0],
                    "pending_units": connection.execute("""SELECT
                        COUNT(*)
                    FROM units
                    WHERE status NOT IN ('VALIDATED','CLOSED')""").fetchone()[0],
                    "unknown_notifications": connection.execute("""SELECT
                        COUNT(*)
                    FROM notifications
                    WHERE status='UNKNOWN'""").fetchone()[0],
                    "pending_notifications": connection.execute("""SELECT
                        COUNT(*)
                    FROM notifications
                    WHERE status IN ('READY','FAILED','HELD','PARTIAL')""").fetchone()[0],
                }
            )
            return base
        finally:
            connection.close()

    def rebaseline(self, expected_base: str, target: str, reason: str) -> None:
        if not reason.strip():
            raise StateError("rebaseline reason is required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("""SELECT
                COUNT(*)
            FROM notifications
            WHERE status IN ('INFLIGHT','DATA_STARTED','UNKNOWN')""").fetchone()[0]:
                raise StateError("active or unknown notification prevents rebaseline")
            row = connection.execute("""SELECT
                epoch,
                checkpoint_sha
            FROM scopes
            WHERE scope_hash=?""", (self.config.scope_hash,)).fetchone()
            if row is None or row["checkpoint_sha"] != expected_base:
                raise StateError("expected checkpoint mismatch")
            connection.execute("UPDATE scopes SET epoch=epoch+1, checkpoint_sha=? WHERE scope_hash=? AND epoch=? AND checkpoint_sha=?", (target, self.config.scope_hash, row["epoch"], expected_base))
            connection.execute("INSERT INTO audit_events(scope_hash,action,actor,expected_base,target,reason,created_at) VALUES (?,'REBASELINED','operator',?,?,?,?)", (self.config.scope_hash, expected_base, target, reason, _now()))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def retry_blocked(self, run_id: str, expected_base: str, report_sha256: str, reason: str) -> int:
        return self._new_generation(run_id, expected_base, report_sha256, reason, action="RETRY_BLOCKED", required_status="REVIEW_REQUIRED")

    def acknowledge_limits(self, run_id: str, expected_base: str, report_sha256: str, reason: str) -> None:
        if not reason.strip():
            raise StateError("acknowledgement reason is required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT
                r.target_sha,
                r.status,
                p.content_sha256,
                n.status notification_status
            FROM runs r
            JOIN reports p ON p.report_id=r.report_id
            JOIN notifications n ON n.report_id=p.report_id
            WHERE r.run_id=? AND r.base_sha=?""", (run_id, expected_base)).fetchone()
            if row is None or row["status"] != "REVIEW_REQUIRED" or row["notification_status"] != "ACCEPTED" or row["content_sha256"] != report_sha256:
                raise StateError("run is not eligible for limit acknowledgement")
            cursor = connection.execute("UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=? AND checkpoint_sha=?", (row["target_sha"], self.config.scope_hash, expected_base))
            if cursor.rowcount != 1:
                raise StateError("checkpoint compare-and-swap failed")
            connection.execute("UPDATE runs SET status='COMMITTED' WHERE run_id=?", (run_id,))
            connection.execute("INSERT INTO audit_events(scope_hash,run_id,action,actor,expected_base,target,reason,evidence_reference,created_at) VALUES (?,?,'LIMITS_ACKNOWLEDGED','operator',?,?,?,?,?)", (self.config.scope_hash, run_id, expected_base, row["target_sha"], reason, report_sha256, _now()))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _new_generation(self, run_id: str, expected_base: str, report_sha256: str, reason: str, *, action: str, required_status: str) -> int:
        if not reason.strip():
            raise StateError("generation change reason is required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT
                r.analysis_generation,
                r.status,
                p.content_sha256,
                n.status notification_status
            FROM runs r
            JOIN reports p ON p.report_id=r.report_id
            JOIN notifications n ON n.report_id=p.report_id
            WHERE r.run_id=? AND r.base_sha=?""", (run_id, expected_base)).fetchone()
            if row is None or row["status"] != required_status or row["notification_status"] != "ACCEPTED" or row["content_sha256"] != report_sha256:
                raise StateError("run is not eligible for a new analysis generation")
            generation = int(row["analysis_generation"]) + 1
            connection.execute("UPDATE runs SET analysis_generation=?,status='PLANNED',report_id=NULL,last_attempt_at=? WHERE run_id=?", (generation, _now(), run_id))
            connection.execute("INSERT INTO audit_events(scope_hash,run_id,action,actor,expected_base,reason,evidence_reference,created_at) VALUES (?,?,?,?,?,?,?,?)", (self.config.scope_hash, run_id, action, "operator", expected_base, reason, report_sha256, _now()))
            connection.commit()
            return generation
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate_run(self, run_id: str, reason: str) -> int:
        if not reason.strip():
            raise StateError("migration reason is required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT
                analysis_generation,
                status,
                report_id
            FROM runs
            WHERE run_id=?""", (run_id,)).fetchone()
            if row is None or row["report_id"] is not None or row["status"] in ('COMMITTED','CLOSED'):
                raise StateError("run cannot be migrated")
            generation = int(row["analysis_generation"]) + 1
            connection.execute("UPDATE runs SET analysis_generation=?,config_digest=?,fingerprint_json=?,status='PLANNED',last_attempt_at=? WHERE run_id=?", (generation, self.config.config_digest, _canonical({"config_digest": self.config.config_digest}), _now(), run_id))
            connection.execute("INSERT INTO audit_events(scope_hash,run_id,action,actor,reason,created_at) VALUES (?,?,'RUN_MIGRATED','operator',?,?)", (self.config.scope_hash, run_id, reason, _now()))
            connection.commit()
            return generation
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
