"""Atomic, hash-chained metadata journal for bounded source research."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .compat import UTC


class ResearchJournalError(RuntimeError):
    pass


_FILE = re.compile(r"^(?P<sequence>[0-9]{8})\.json$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
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
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


class ResearchJournal:
    """Own one immutable journal directory for a run/generation pair."""

    def __init__(self, scope_root: Path, *, scope_hash: str, run_id: str, generation: int) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", scope_hash) or not run_id or generation < 0:
            raise ResearchJournalError("journal identity is invalid")
        self.scope_root = scope_root.resolve()
        self.scope_hash = scope_hash
        self.run_id = run_id
        self.generation = generation
        self.directory = self.scope_root / "research" / run_id / str(generation)
        self.index_path = self.directory / "journal-index.json"

    def _assert_owned(self, path: Path, *, regular: bool = False) -> None:
        if path.is_symlink():
            raise ResearchJournalError("journal path is a symlink")
        try:
            resolved = path.resolve(strict=regular)
        except OSError as exc:
            raise ResearchJournalError("journal path is unreadable") from exc
        if resolved != self.scope_root and self.scope_root not in resolved.parents:
            raise ResearchJournalError("journal path escapes scope")
        if regular and not path.is_file():
            raise ResearchJournalError("journal record is not a regular file")

    def ensure(self) -> None:
        self._assert_owned(self.scope_root)
        self.scope_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        research = self.scope_root / "research"
        run = research / self.run_id
        for directory in (research, run, self.directory):
            self._assert_owned(directory)
            directory.mkdir(exist_ok=True, mode=0o700)
        if self.directory.stat().st_mode & 0o077:
            os.chmod(self.directory, 0o700)

    def _records(self) -> list[tuple[int, Path, dict[str, Any]]]:
        self.ensure()
        rows: list[tuple[int, Path, dict[str, Any]]] = []
        for path in sorted(self.directory.iterdir(), key=lambda item: item.name):
            if path.name == self.index_path.name:
                continue
            match = _FILE.fullmatch(path.name)
            if match is None or path.is_symlink():
                raise ResearchJournalError("journal contains an unexpected file")
            self._assert_owned(path, regular=True)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ResearchJournalError("journal record is torn or unreadable") from exc
            if not isinstance(value, dict):
                raise ResearchJournalError("journal record shape is invalid")
            rows.append((int(match.group("sequence")), path, value))
        return rows

    def verify(self) -> list[dict[str, Any]]:
        rows = self._records()
        expected_sequence = 1
        previous: str | None = None
        verified: list[dict[str, Any]] = []
        for sequence, _, record in rows:
            if sequence != expected_sequence:
                raise ResearchJournalError("journal sequence has a gap")
            if record.get("schema_version") != "research-journal/1.0" or record.get("scope_hash") != self.scope_hash or record.get("run_id") != self.run_id or record.get("generation") != self.generation:
                raise ResearchJournalError("journal identity mismatch")
            if record.get("sequence") != sequence or record.get("previous_record_sha256") != previous:
                raise ResearchJournalError("journal hash chain is broken")
            digest = record.get("record_sha256")
            unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
            if not isinstance(digest, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
                raise ResearchJournalError("journal record hash mismatch")
            verified.append(record)
            previous = digest
            expected_sequence += 1
        if not self.index_path.is_file() or self.index_path.is_symlink():
            if rows:
                raise ResearchJournalError("journal index is missing")
            return verified
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchJournalError("journal index is unreadable") from exc
        last = verified[-1]["record_sha256"] if verified else None
        if index != {
            "schema_version": "research-journal-index/1.0", "scope_hash": self.scope_hash,
            "run_id": self.run_id, "generation": self.generation,
            "record_count": len(verified), "last_sequence": len(verified),
            "last_record_sha256": last,
        }:
            raise ResearchJournalError("journal index does not match records")
        return verified

    def append(self, record_type: str, *, unit_id: str, attempt: int, turn: int,
               payload: dict[str, Any]) -> dict[str, Any]:
        if record_type not in {"tool_receipt", "unit_status"} or not unit_id or attempt < 0 or turn < 0:
            raise ResearchJournalError("journal record metadata is invalid")
        existing = self.verify()
        sequence = len(existing) + 1
        previous = existing[-1]["record_sha256"] if existing else None
        body = {
            "schema_version": "research-journal/1.0", "scope_hash": self.scope_hash,
            "run_id": self.run_id, "generation": self.generation, "sequence": sequence,
            "record_type": record_type, "unit_id": unit_id, "attempt": attempt, "turn": turn,
            "previous_record_sha256": previous, "recorded_at": _now(), **payload,
        }
        body["record_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        self.ensure()
        _atomic_bytes(self.directory / f"{sequence:08d}.json", _canonical(body) + b"\n")
        index = {
            "schema_version": "research-journal-index/1.0", "scope_hash": self.scope_hash,
            "run_id": self.run_id, "generation": self.generation,
            "record_count": sequence, "last_sequence": sequence,
            "last_record_sha256": body["record_sha256"],
        }
        _atomic_bytes(self.index_path, _canonical(index) + b"\n")
        return body

    def append_receipt(self, receipt: dict[str, Any], *, unit_id: str, attempt: int, turn: int) -> dict[str, Any]:
        items_digest = hashlib.sha256(_canonical(receipt.get("items", []))).hexdigest()
        return self.append(
            "tool_receipt", unit_id=unit_id, attempt=attempt, turn=turn,
            payload={
                "tool_call_id": receipt.get("tool_call_id"), "tool_name": receipt.get("tool_name"),
                "arguments_digest": receipt.get("arguments_digest"), "result_digest": items_digest,
                "status": receipt.get("status"), "evidence_ids": list(receipt.get("evidence_ids", [])),
                "continuation": receipt.get("continuation"), "coverage": receipt.get("coverage", {}),
                "diagnostics": list(receipt.get("diagnostics", [])), "elapsed_ms": receipt.get("elapsed_ms", 0),
            },
        )

    def has_receipt(self, *, unit_id: str, tool_call_id: str) -> bool:
        return any(
            record.get("record_type") == "tool_receipt"
            and record.get("unit_id") == unit_id
            and record.get("tool_call_id") == tool_call_id
            for record in self.verify()
        )

    def append_unit_status(self, *, unit_id: str, attempt: int, status: str,
                           request_digest: str, result_digest: str | None, diagnostics: list[str]) -> dict[str, Any]:
        return self.append(
            "unit_status", unit_id=unit_id, attempt=attempt, turn=0,
            payload={"status": status, "request_digest": request_digest,
                     "result_digest": result_digest, "diagnostics": list(diagnostics)},
        )
