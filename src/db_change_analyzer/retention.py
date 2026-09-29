from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import RetentionConfig
from .security import contained_path
from .state import SqliteStateStore, StateError


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    path: str
    kind: str
    bytes: int


class RetentionManager:
    def __init__(self, store: SqliteStateStore, policy: RetentionConfig) -> None:
        self.store = store
        self.policy = policy

    def _cutoff(self, days: int) -> float:
        return (datetime.now(UTC) - timedelta(days=days)).timestamp()

    def plan(self) -> dict[str, Any]:
        self.store.verify_markers()
        root = self.store.paths.scope.resolve(strict=True)
        candidates: list[CleanupCandidate] = []
        areas = ((self.store.paths.exports, self.policy.completed_report_days, "completed_export"), (self.store.paths.backups, self.policy.backup_days, "old_backup"))
        for directory, days, kind in areas:
            if not directory.exists() or directory.is_symlink():
                continue
            cutoff = self._cutoff(days)
            for path in directory.iterdir():
                if path.is_symlink():
                    continue
                resolved = contained_path(root, path)
                if resolved.parent != directory.resolve(strict=True) or not resolved.is_file() or resolved.stat().st_mtime >= cutoff:
                    continue
                # Backup database and its manifest are recoverable maintenance data;
                # exports are deletable only when no pending run/report references them.
                if kind == "completed_export" and self._is_protected_export(resolved):
                    continue
                candidates.append(CleanupCandidate(str(resolved), kind, resolved.stat().st_size))
        usage = shutil_disk_percent(root)
        return {
            "schema_version": 1,
            "scope_hash": self.store.config.scope_hash,
            "disk_used_percent": usage,
            "warning": usage >= self.policy.disk_warning_percent,
            "stop_new_ai": usage >= self.policy.disk_stop_percent,
            "candidates": [asdict(candidate) for candidate in sorted(candidates, key=lambda item: item.path)],
            "applied": False,
        }

    def _is_protected_export(self, path: Path) -> bool:
        connection = self.store.connection()
        try:
            active = connection.execute(
                """SELECT
                       COUNT(*)
                   FROM runs
                   WHERE status NOT IN ('BASELINED','NO_CHANGE','OUT_OF_SCOPE_ONLY','COMMITTED','CLOSED')"""
            ).fetchone()[0]
            unknown = connection.execute(
                """SELECT
                       COUNT(*)
                   FROM notifications
                   WHERE status IN ('INFLIGHT','DATA_STARTED','UNKNOWN','READY','FAILED','HELD','PARTIAL')"""
            ).fetchone()[0]
            return bool(active or unknown)
        finally:
            connection.close()

    def apply(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if manifest.get("scope_hash") != self.store.config.scope_hash or manifest.get("applied"):
            raise StateError("cleanup manifest does not match this scope")
        root = self.store.paths.scope.resolve(strict=True)
        removed: list[str] = []
        for item in manifest.get("candidates", []):
            original = Path(item["path"])
            if original.is_symlink():
                raise StateError("cleanup candidate changed since planning")
            path = contained_path(root, original)
            area = {"completed_export": (self.store.paths.exports, self.policy.completed_report_days),
                    "old_backup": (self.store.paths.backups, self.policy.backup_days)}.get(item.get("kind"))
            if area is None or area[0].is_symlink() or path.parent != area[0].resolve(strict=True):
                raise StateError("cleanup candidate is outside its permitted area")
            if not path.is_file() or path.stat().st_mtime >= self._cutoff(area[1]):
                raise StateError("cleanup candidate changed since planning")
            if path.stat().st_size != item["bytes"]:
                raise StateError("cleanup candidate size changed since planning")
            if item["kind"] == "completed_export" and self._is_protected_export(path):
                raise StateError("cleanup export became protected")
            path.unlink()
            removed.append(str(path))
        return {**manifest, "applied": True, "removed": removed}


def shutil_disk_percent(path: Path) -> int:
    stats = os.statvfs(path) if hasattr(os, "statvfs") else None
    if stats is not None:
        total = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        return 0 if total <= 0 else round((total - available) * 100 / total)
    import shutil
    usage = shutil.disk_usage(path)
    return 0 if usage.total <= 0 else round(usage.used * 100 / usage.total)
