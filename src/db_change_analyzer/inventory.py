from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .git_client import GitClient, GitError, TreeEntry
from .oracle.projections import Projection, project
from .oracle.scanner import ScanOccurrence, ScanResult, scan


@dataclass(frozen=True, slots=True)
class FileInventory:
    entry: TreeEntry
    bytes: int | None
    encoding: str | None
    newline_style: str | None
    parse_status: str
    diagnostics: tuple[str, ...]
    occurrences: tuple[ScanOccurrence, ...]
    projections: tuple[Projection, ...]


def _newline_style(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    cr = raw.count(b"\r") - crlf
    if sum(value > 0 for value in (crlf, lf, cr)) > 1:
        return "MIXED"
    return "CRLF" if crlf else "LF" if lf else "CR" if cr else "NONE"


def _parse_in_worker(raw: bytes, timeout_seconds: int) -> tuple[bool, tuple[str, ...]]:
    environment = {
        key: value
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }
    source_root = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = source_root
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "db_change_analyzer.oracle.parser_worker", "--encoding", "utf-8"],
            input=raw,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, ("PARSER_TIMEOUT",)
    if completed.returncode != 0 or len(completed.stdout) > 1024 * 1024:
        return False, ("PARSER_WORKER_FAILED",)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, ("PARSER_WORKER_INVALID",)
    diagnostics = tuple(item["code"] for item in result.get("lexer_errors", [])) + tuple(item["code"] for item in result.get("parser_errors", []))
    return bool(result.get("ok")), diagnostics


def inventory_bytes(raw: bytes, *, default_schema: str | None, parse_timeout_seconds: int = 20, parse: bool = True) -> tuple[ScanResult, tuple[Projection, ...], str, tuple[str, ...]]:
    result = scan(raw, default_schema=default_schema)
    if "ENCODING_UNRESOLVED" in result.diagnostics:
        return result, (), "unresolved", result.diagnostics
    text = raw.decode("utf-8")
    parse_ok, parse_diagnostics = _parse_in_worker(raw, parse_timeout_seconds) if parse and len(raw) <= 2 * 1024 * 1024 else (False, ("PARSER_NOT_RUN",))
    projections = tuple(
        project(item, text[item.start_char : item.end_char], parse_ok=parse_ok)
        for item in result.occurrences
    )
    status = "parsed" if parse_ok else "text_fallback" if result.occurrences else "unresolved"
    return result, projections, status, tuple((*result.diagnostics, *parse_diagnostics))


def inventory_revision(git: GitClient, revision: str, roots: dict[str, str], *, max_file_bytes: int, timeout_seconds: int, parse_paths: set[bytes] | None = None) -> list[FileInventory]:
    inventories: list[FileInventory] = []
    for entry in git.list_tree(revision):
        matching = [(root, schema) for root, schema in roots.items() if entry.path == root.encode() or entry.path.startswith(root.encode() + b"/")]
        if not matching:
            continue
        if entry.mode in {"120000", "160000"} or entry.kind != "blob":
            inventories.append(FileInventory(entry, None, None, None, "unsupported", ("UNSUPPORTED_GIT_MODE",), (), ()))
            continue
        try:
            raw = git.read_blob(entry.oid, max_file_bytes)
        except GitError as exc:
            inventories.append(FileInventory(entry, None, None, None, "unresolved", (exc.code,), (), ()))
            continue
        if not entry.path.lower().endswith(b".sql"):
            inventories.append(FileInventory(entry, len(raw), None, _newline_style(raw), "artifact", (), (), ()))
            continue
        scan_result, projections, status, diagnostics = inventory_bytes(raw, default_schema=matching[0][1], parse_timeout_seconds=timeout_seconds, parse=parse_paths is None or entry.path in parse_paths)
        inventories.append(FileInventory(entry, len(raw), "utf-8" if "ENCODING_UNRESOLVED" not in diagnostics else None, _newline_style(raw), status, diagnostics, scan_result.occurrences, projections))
    return inventories
