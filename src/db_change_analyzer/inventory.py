from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .git_client import GitClient, GitError, TreeEntry
from .oracle.projections import Projection, project
from .oracle.scanner import ScanOccurrence, ScanResult, scan


_PARSER_CONFIG_FINGERPRINT = sha256(
    b"antlr-plsql-4.13.2:b434a051c56dcc2a5de0a0f2d86575ed192b59da:sll-ll-v1"
    + Path(__file__).with_name("oracle").joinpath("parser_worker.py").read_bytes()
    + Path(__file__).with_name("oracle").joinpath("generated", "manifest.json").read_bytes()
).hexdigest()
_PARSE_CACHE_LIMIT = 512
_FRAGMENT_RETRY_MAX_OBJECTS = 32
_parse_cache: OrderedDict[tuple[str, str, int], tuple[bool, tuple[str, ...]]] = OrderedDict()
_fragment_cache: OrderedDict[tuple[str, tuple[str, ...], int], tuple[tuple[bool, tuple[str, ...]], ...] | None] = OrderedDict()


def _cache_put(cache: OrderedDict, key, value) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _PARSE_CACHE_LIMIT:
        cache.popitem(last=False)


def _persistent_cache_get(cache, key: str) -> tuple[bool, tuple[str, ...]] | None:
    if cache is None or not hasattr(cache, "get_cache_entry"):
        return None
    content = cache.get_cache_entry(key, kind="oracle_parser", version_fingerprint=_PARSER_CONFIG_FINGERPRINT)
    if content is None:
        return None
    try:
        value = json.loads(content)
        return bool(value["ok"]), tuple(str(item) for item in value["diagnostics"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _persistent_cache_put(cache, key: str, result: tuple[bool, tuple[str, ...]]) -> None:
    if cache is None or not hasattr(cache, "put_cache_entry"):
        return
    content = json.dumps({"ok": result[0], "diagnostics": list(result[1])}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    cache.put_cache_entry(key, content, kind="oracle_parser", version_fingerprint=_PARSER_CONFIG_FINGERPRINT)


def _persistent_fragment_get(cache, key: str) -> tuple[tuple[bool, tuple[str, ...]], ...] | None:
    if cache is None or not hasattr(cache, "get_cache_entry"):
        return None
    content = cache.get_cache_entry(key, kind="oracle_parser_fragments", version_fingerprint=_PARSER_CONFIG_FINGERPRINT)
    if content is None:
        return None
    try:
        value = json.loads(content)
        return tuple((bool(item["ok"]), tuple(str(code) for code in item["diagnostics"])) for item in value)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _persistent_fragment_put(cache, key: str, result: tuple[tuple[bool, tuple[str, ...]], ...]) -> None:
    if cache is None or not hasattr(cache, "put_cache_entry"):
        return
    content = json.dumps(
        [{"ok": ok, "diagnostics": list(diagnostics)} for ok, diagnostics in result],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    cache.put_cache_entry(key, content, kind="oracle_parser_fragments", version_fingerprint=_PARSER_CONFIG_FINGERPRINT)


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
    raw: bytes | None = None


def _newline_style(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    cr = raw.count(b"\r") - crlf
    if sum(value > 0 for value in (crlf, lf, cr)) > 1:
        return "MIXED"
    return "CRLF" if crlf else "LF" if lf else "CR" if cr else "NONE"


def _parse_in_worker(raw: bytes, timeout_seconds: int, parser_cache=None) -> tuple[bool, tuple[str, ...]]:
    digest = sha256(raw).hexdigest()
    key = (_PARSER_CONFIG_FINGERPRINT, digest, timeout_seconds)
    persistent_key = f"blob:{digest}:timeout:{timeout_seconds}"
    cached = _parse_cache.get(key)
    if cached is not None:
        _parse_cache.move_to_end(key)
        return cached
    cached = _persistent_cache_get(parser_cache, persistent_key)
    if cached is not None:
        _cache_put(_parse_cache, key, cached)
        return cached
    environment = {
        key: value
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "db_change_analyzer.oracle.parser_worker", "--encoding", "utf-8"],
            input=raw,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = (False, ("PARSER_TIMEOUT",))
        _cache_put(_parse_cache, key, result)
        _persistent_cache_put(parser_cache, persistent_key, result)
        return result
    if completed.returncode != 0 or len(completed.stdout) > 1024 * 1024:
        result = (False, ("PARSER_WORKER_FAILED",))
        _cache_put(_parse_cache, key, result)
        _persistent_cache_put(parser_cache, persistent_key, result)
        return result
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = (False, ("PARSER_WORKER_INVALID",))
        _cache_put(_parse_cache, key, parsed)
        _persistent_cache_put(parser_cache, persistent_key, parsed)
        return parsed
    diagnostics = tuple(item["code"] for item in result.get("lexer_errors", [])) + tuple(item["code"] for item in result.get("parser_errors", []))
    parsed = (bool(result.get("ok")), diagnostics)
    _cache_put(_parse_cache, key, parsed)
    _persistent_cache_put(parser_cache, persistent_key, parsed)
    return parsed


def _parse_fragments_in_worker(fragments: tuple[str, ...], timeout_seconds: int, parser_cache=None) -> tuple[tuple[bool, tuple[str, ...]], ...] | None:
    """Parse independently scanned objects in one isolated worker.

    A generated parser can reject a large export as one ``sql_script`` while
    every top-level object remains independently parseable.  The batch mode
    keeps the process isolation boundary but avoids starting one worker per
    object.  ``None`` means the worker did not produce a complete result, so
    callers must retain text fallback.
    """
    fragment_digests = tuple(sha256(fragment.encode("utf-8")).hexdigest() for fragment in fragments)
    key = (_PARSER_CONFIG_FINGERPRINT, fragment_digests, timeout_seconds)
    fragment_key_payload = json.dumps([*fragment_digests, str(timeout_seconds)], separators=(",", ":")).encode()
    persistent_key = f"fragments:{sha256(fragment_key_payload).hexdigest()}"
    if key in _fragment_cache:
        cached = _fragment_cache[key]
        _fragment_cache.move_to_end(key)
        return cached
    cached_content = _persistent_fragment_get(parser_cache, persistent_key)
    if cached_content is not None:
        _cache_put(_fragment_cache, key, cached_content)
        return cached_content
    environment = {
        key: value
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }
    payload = json.dumps({"fragments": list(fragments)}, ensure_ascii=False).encode("utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "db_change_analyzer.oracle.parser_worker", "--encoding", "utf-8", "--fragments"],
            input=payload,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _cache_put(_fragment_cache, key, None)
        return None
    if completed.returncode != 0 or len(completed.stdout) > 4 * 1024 * 1024:
        _cache_put(_fragment_cache, key, None)
        return None
    try:
        result = json.loads(completed.stdout)
        raw_results = result["results"]
    except (KeyError, TypeError, json.JSONDecodeError):
        _cache_put(_fragment_cache, key, None)
        return None
    if not isinstance(raw_results, list) or len(raw_results) != len(fragments):
        _cache_put(_fragment_cache, key, None)
        return None
    parsed: list[tuple[bool, tuple[str, ...]]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            _cache_put(_fragment_cache, key, None)
            return None
        diagnostics = tuple(entry["code"] for entry in item.get("lexer_errors", [])) + tuple(entry["code"] for entry in item.get("parser_errors", []))
        parsed.append((bool(item.get("ok")), diagnostics))
    result = tuple(parsed)
    _cache_put(_fragment_cache, key, result)
    _persistent_fragment_put(parser_cache, persistent_key, result)
    return result


def inventory_bytes(raw: bytes, *, default_schema: str | None, parse_timeout_seconds: int = 20, parse: bool = True, parser_cache=None) -> tuple[ScanResult, tuple[Projection, ...], str, tuple[str, ...]]:
    result = scan(raw, default_schema=default_schema)
    if "ENCODING_UNRESOLVED" in result.diagnostics:
        return result, (), "unresolved", result.diagnostics
    text = raw.decode("utf-8")
    large_combined_export = len(result.occurrences) > _FRAGMENT_RETRY_MAX_OBJECTS
    parse_ok, parse_diagnostics = (
        (_parse_in_worker(raw, parse_timeout_seconds, parser_cache) if parser_cache is not None else _parse_in_worker(raw, parse_timeout_seconds))
        if parse and len(raw) <= 2 * 1024 * 1024 and not large_combined_export
        else (False, ("PARSER_NOT_RUN_LARGE_EXPORT",) if large_combined_export else ("PARSER_NOT_RUN",))
    )
    fragment_results: tuple[tuple[bool, tuple[str, ...]], ...] | None = None
    # A combined export can be too large for the parser as one script while
    # each scanned object remains independently parseable.  This includes
    # large sequence exports from the real GPU repository; keep the full
    # script fallback closed, but still verify every object fragment.
    if parse and not parse_ok and result.occurrences and parse_diagnostics != ("PARSER_NOT_RUN",):
        fragments = tuple(text[item.start_char : item.end_char] for item in result.occurrences)
        fragment_results = (
            _parse_fragments_in_worker(fragments, parse_timeout_seconds, parser_cache)
            if parser_cache is not None else _parse_fragments_in_worker(fragments, parse_timeout_seconds)
        )
        if fragment_results is not None and all(ok for ok, _ in fragment_results):
            parse_ok = True
            parse_diagnostics = ()
    projections = tuple(
        project(item, text[item.start_char : item.end_char], parse_ok=parse_ok)
        for item in result.occurrences
    )
    status = "parsed" if parse_ok else "text_fallback" if result.occurrences else "unresolved"
    extraction_diagnostics = tuple(code for projection in projections for code in projection.diagnostics) if parse_ok else ()
    return result, projections, status, tuple((*result.diagnostics, *parse_diagnostics, *extraction_diagnostics))


def inventory_revision(git: GitClient, revision: str, roots: dict[str, str], *, max_file_bytes: int, timeout_seconds: int, parse_paths: set[bytes] | None = None, parser_cache=None) -> list[FileInventory]:
    inventories: list[FileInventory] = []
    entries = git.list_tree(revision)
    matching_entries = [
        entry for entry in entries
        if entry.kind == "blob" and entry.mode not in {"120000", "160000"}
        and any(entry.path == root.encode() or entry.path.startswith(root.encode() + b"/") for root in roots)
    ]
    raw_by_oid: dict[str, bytes] = {}
    read_errors: dict[str, GitError] = {}
    batch_reader = getattr(git, "read_blobs", None)
    for offset in range(0, len(matching_entries), 32):
        batch = list(dict.fromkeys(entry.oid for entry in matching_entries[offset:offset + 32]))
        if not callable(batch_reader):
            for oid in batch:
                try:
                    raw_by_oid[oid] = git.read_blob(oid, max_file_bytes)
                except GitError as exc:
                    read_errors[oid] = exc
            continue
        try:
            raw_by_oid.update(batch_reader(batch, max_file_bytes))
        except GitError:
            for oid in batch:
                try:
                    raw_by_oid[oid] = git.read_blob(oid, max_file_bytes)
                except GitError as exc:
                    read_errors[oid] = exc
    for entry in entries:
        matching = [(root, schema) for root, schema in roots.items() if entry.path == root.encode() or entry.path.startswith(root.encode() + b"/")]
        if not matching:
            continue
        if entry.mode in {"120000", "160000"} or entry.kind != "blob":
            inventories.append(FileInventory(entry, None, None, None, "unsupported", ("UNSUPPORTED_GIT_MODE",), (), ()))
            continue
        try:
            if entry.oid in read_errors:
                raise read_errors[entry.oid]
            raw = raw_by_oid[entry.oid]
        except (KeyError, GitError) as exc:
            if isinstance(exc, KeyError):
                exc = GitError("BLOB_READ_MISSING", "batched blob read did not return the requested object")
            inventories.append(FileInventory(entry, None, None, None, "unresolved", (exc.code,), (), ()))
            continue
        if not entry.path.lower().endswith(b".sql"):
            inventories.append(FileInventory(entry, len(raw), None, _newline_style(raw), "artifact", (), (), ()))
            continue
        scan_result, projections, status, diagnostics = inventory_bytes(raw, default_schema=matching[0][1], parse_timeout_seconds=timeout_seconds, parse=parse_paths is None or entry.path in parse_paths, parser_cache=parser_cache)
        inventories.append(FileInventory(entry, len(raw), "utf-8" if "ENCODING_UNRESOLVED" not in diagnostics else None, _newline_style(raw), status, diagnostics, scan_result.occurrences, projections, raw))
    return inventories
