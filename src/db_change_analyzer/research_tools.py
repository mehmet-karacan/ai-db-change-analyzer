from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from .git_client import GitClient, GitError, RawDelta, TreeEntry


class ResearchToolError(RuntimeError):
    def __init__(self, code: str, message: str = "research tool request rejected") -> None:
        self.code = code
        super().__init__(f"{code}:{message}")


ToolStatus = Literal["ok", "partial", "not_found", "denied", "error"]


@dataclass(frozen=True, slots=True)
class ResearchReceipt:
    tool_call_id: str
    tool_name: str
    arguments_digest: str
    revision: str
    scope_hash: str
    status: ToolStatus
    evidence_ids: tuple[str, ...]
    items: tuple[dict[str, Any], ...]
    continuation: str | None
    coverage: dict[str, Any]
    diagnostics: tuple[str, ...]
    elapsed_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id, "tool_name": self.tool_name,
            "arguments_digest": self.arguments_digest, "revision": self.revision,
            "scope_hash": self.scope_hash, "status": self.status,
            "evidence_ids": list(self.evidence_ids), "items": list(self.items),
            "continuation": self.continuation, "coverage": self.coverage,
            "diagnostics": list(self.diagnostics), "elapsed_ms": self.elapsed_ms,
        }


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cursor(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        result = json.loads(raw)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchToolError("CURSOR_INVALID") from exc
    if not isinstance(result, dict):
        raise ResearchToolError("CURSOR_INVALID")
    return result


class ResearchToolDispatcher:
    """Bounded, read-only research access over an owned bare Git cache."""

    def __init__(self, git: GitClient, roots: dict[str, str], *, scope_hash: str,
                 max_file_bytes: int = 64 * 1024 * 1024, page_size: int = 100) -> None:
        self.git = git
        self.roots = {PurePosixPath(key.replace("\\", "/")).as_posix().rstrip("/"): value for key, value in roots.items()}
        self.scope_hash = scope_hash
        self.max_file_bytes = max_file_bytes
        self.page_size = max(1, min(page_size, 500))

    def _revision(self, value: str) -> str:
        try:
            return self.git.resolve_commit(value)
        except GitError as exc:
            raise ResearchToolError(exc.code) from exc

    def _entry(self, revision: str, path: str) -> TreeEntry:
        normalized = path.replace("\\", "/")
        candidate = PurePosixPath(normalized)
        if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
            raise ResearchToolError("PATH_REJECTED")
        normalized = candidate.as_posix()
        if not any(normalized == root or normalized.startswith(root + "/") for root in self.roots):
            raise ResearchToolError("PATH_OUT_OF_SCOPE")
        for entry in self.git.list_tree(revision):
            if entry.path_display == normalized:
                if entry.kind != "blob" or entry.mode in {"120000", "160000"}:
                    raise ResearchToolError("UNSUPPORTED_GIT_ENTRY")
                return entry
        raise ResearchToolError("SOURCE_NOT_FOUND")

    def _receipt(self, started: float, call_id: str, name: str, args: dict[str, Any], revision: str,
                 status: ToolStatus, items: list[dict[str, Any]], evidence: list[str],
                 continuation: str | None = None, coverage: dict[str, Any] | None = None,
                 diagnostics: list[str] | None = None) -> ResearchReceipt:
        return ResearchReceipt(call_id, name, _digest(args), revision, self.scope_hash, status,
                               tuple(evidence), tuple(items), continuation, coverage or {},
                               tuple(diagnostics or []), int((time.monotonic() - started) * 1000))

    def read_source(self, *, tool_call_id: str, revision: str, path: str,
                    start_line: int = 1, end_line: int | None = None) -> ResearchReceipt:
        started = time.monotonic()
        oid = self._revision(revision)
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ResearchToolError("LINE_RANGE_INVALID")
        entry = self._entry(oid, path)
        raw = self.git.read_blob(entry.oid, self.max_file_bytes)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResearchToolError("SOURCE_ENCODING_UNRESOLVED") from exc
        lines = text.splitlines(keepends=True)
        last = end_line or len(lines)
        selected = lines[start_line - 1:last]
        fragment = "".join(selected)
        start_byte = len("".join(lines[:start_line - 1]).encode("utf-8"))
        end_byte = start_byte + len(fragment.encode("utf-8"))
        evidence_id = "source-" + hashlib.sha256(f"{oid}:{entry.path_b64}:{start_line}:{last}:{hashlib.sha256(fragment.encode('utf-8')).hexdigest()}".encode()).hexdigest()[:88]
        item = {"evidence_id": evidence_id, "revision": oid, "path_display": entry.path_display,
                "path_b64": entry.path_b64, "blob_oid": entry.oid,
                "source_sha256": hashlib.sha256(raw).hexdigest(), "start_line": start_line,
                "end_line": min(last, len(lines)), "start_byte": start_byte,
                "end_byte_exclusive": end_byte, "fragment_sha256": hashlib.sha256(fragment.encode()).hexdigest(),
                "snippet": fragment, "complete": last >= len(lines)}
        return self._receipt(started, tool_call_id, "read_source", {"revision": revision, "path": path, "start_line": start_line, "end_line": end_line}, oid, "ok", [item], [evidence_id], coverage={"files_scanned": 1, "complete": last >= len(lines)})

    def _scoped_entries(self, revision: str) -> list[TreeEntry]:
        return [entry for entry in self.git.list_tree(revision)
                if entry.kind == "blob" and entry.mode not in {"120000", "160000"}
                and any(entry.path_display == root or entry.path_display.startswith(root + "/") for root in self.roots)]

    def search_sources(self, *, tool_call_id: str, revision: str, query: str,
                       literal: bool = True, cursor: str | None = None,
                       scope_paths: list[str] | None = None) -> ResearchReceipt:
        started = time.monotonic()
        if not query or len(query) > 500 or "\x00" in query:
            raise ResearchToolError("QUERY_INVALID")
        oid = self._revision(revision)
        offset = 0
        if cursor:
            state = _decode_cursor(cursor)
            if state.get("revision") != oid or state.get("scope_hash") != self.scope_hash or state.get("query") != query:
                raise ResearchToolError("CURSOR_SCOPE_MISMATCH")
            offset = int(state.get("offset", 0))
        wanted = {PurePosixPath(item.replace("\\", "/")).as_posix() for item in (scope_paths or [])}
        pattern = re.compile(re.escape(query) if literal else query, re.IGNORECASE)
        matches: list[dict[str, Any]] = []
        scanned = unreadable = 0
        for entry in self._scoped_entries(oid):
            if wanted and entry.path_display not in wanted:
                continue
            if not entry.path_display.lower().endswith(".sql"):
                continue
            try:
                raw = self.git.read_blob(entry.oid, self.max_file_bytes)
                text = raw.decode("utf-8")
            except (GitError, UnicodeDecodeError):
                unreadable += 1
                continue
            scanned += 1
            for line_no, line in enumerate(text.splitlines(), 1):
                match = pattern.search(line)
                if match:
                    item = {"revision": oid, "path_display": entry.path_display, "path_b64": entry.path_b64,
                            "blob_oid": entry.oid, "line": line_no, "text": line[:2000],
                            "match_start": match.start(), "match_end": match.end(),
                            "classification": "source_text"}
                    item["evidence_id"] = "search-" + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:88]
                    matches.append(item)
        page = matches[offset:offset + self.page_size]
        next_cursor = _cursor({"revision": oid, "scope_hash": self.scope_hash, "query": query, "offset": offset + len(page)}) if offset + len(page) < len(matches) else None
        status: ToolStatus = "ok" if not unreadable else "partial"
        return self._receipt(started, tool_call_id, "search_sources", {"revision": revision, "query": query, "literal": literal, "scope_paths": scope_paths}, oid, status, page, [item["evidence_id"] for item in page], next_cursor, {"files_scanned": scanned, "files_unreadable": unreadable, "match_count": len(matches), "complete": next_cursor is None}, ["SEARCH_SCOPE_PARTIAL"] if unreadable else [])

    def get_diff(self, *, tool_call_id: str, base_revision: str, target_revision: str,
                 path: str | None = None) -> ResearchReceipt:
        started = time.monotonic()
        base, target = self._revision(base_revision), self._revision(target_revision)
        deltas = self.git.diff(base, target)
        filtered = [item for item in deltas if path is None or item.path_display == path]
        scoped = [item for item in filtered if any(item.path_display == root or item.path_display.startswith(root + "/") for root in self.roots)]
        items = [{"old_oid": item.old_oid if set(item.old_oid) != {"0"} else None,
                  "new_oid": item.new_oid if set(item.new_oid) != {"0"} else None,
                  "old_mode": item.old_mode, "new_mode": item.new_mode,
                  "operation": item.status, "path_display": item.path_display,
                  "path_b64": item.path_b64} for item in scoped]
        return self._receipt(started, tool_call_id, "get_diff", {"base_revision": base_revision, "target_revision": target_revision, "path": path}, target, "ok" if len(filtered) == len(scoped) else "partial", items, [], coverage={"base_revision": base, "target_revision": target, "complete": len(filtered) == len(scoped)})

    def _source_lines(self, revision: str, scope_paths: list[str] | None = None) -> tuple[list[tuple[TreeEntry, list[str]]], int]:
        wanted = {PurePosixPath(item.replace("\\", "/")).as_posix() for item in (scope_paths or [])}
        sources: list[tuple[TreeEntry, list[str]]] = []
        unreadable = 0
        for entry in self._scoped_entries(revision):
            if wanted and entry.path_display not in wanted:
                continue
            if not entry.path_display.lower().endswith(".sql"):
                continue
            try:
                text = self.git.read_blob(entry.oid, self.max_file_bytes).decode("utf-8")
            except (GitError, UnicodeDecodeError):
                unreadable += 1
                continue
            sources.append((entry, text.splitlines()))
        return sources, unreadable

    def lookup_symbol(self, *, tool_call_id: str, revision: str, symbol: str,
                      symbol_kind: str | None = None, scope_paths: list[str] | None = None) -> ResearchReceipt:
        started = time.monotonic()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?", symbol or ""):
            raise ResearchToolError("SYMBOL_INVALID")
        oid = self._revision(revision)
        pattern = re.compile(rf"\b{re.escape(symbol.split('.')[-1])}\b", re.IGNORECASE)
        definition = re.compile(r"\b(CREATE|ALTER)\s+(OR\s+REPLACE\s+)?(TABLE|VIEW|PACKAGE|PACKAGE\s+BODY|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|TYPE)\b", re.IGNORECASE)
        items: list[dict[str, Any]] = []
        sources, unreadable = self._source_lines(oid, scope_paths)
        for entry, lines in sources:
            for line_no, line in enumerate(lines, 1):
                match = pattern.search(line)
                kind_match = definition.search(line)
                if not match or not kind_match:
                    continue
                found_kind = re.sub(r"\s+", "_", kind_match.group(3).upper())
                if symbol_kind and found_kind != symbol_kind.upper():
                    continue
                item = {"revision": oid, "path_display": entry.path_display, "path_b64": entry.path_b64,
                        "blob_oid": entry.oid, "line": line_no, "text": line[:2000],
                        "symbol": symbol, "symbol_kind": found_kind, "classification": "definition"}
                item["evidence_id"] = "symbol-" + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:88]
                items.append(item)
        status: ToolStatus = "ok" if not unreadable else "partial"
        return self._receipt(started, tool_call_id, "lookup_symbol", {"revision": revision, "symbol": symbol, "symbol_kind": symbol_kind, "scope_paths": scope_paths}, oid, status, items[:self.page_size], [item["evidence_id"] for item in items[:self.page_size]], coverage={"files_scanned": len(sources), "files_unreadable": unreadable, "complete": len(items) <= self.page_size}, diagnostics=["LOOKUP_SCOPE_PARTIAL"] if unreadable else [])

    def find_references(self, *, tool_call_id: str, revision: str, symbol: str,
                        scope_paths: list[str] | None = None) -> ResearchReceipt:
        started = time.monotonic()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?", symbol or ""):
            raise ResearchToolError("SYMBOL_INVALID")
        oid = self._revision(revision)
        pattern = re.compile(rf"\b{re.escape(symbol.split('.')[-1])}\b", re.IGNORECASE)
        items: list[dict[str, Any]] = []
        sources, unreadable = self._source_lines(oid, scope_paths)
        for entry, lines in sources:
            for line_no, line in enumerate(lines, 1):
                match = pattern.search(line)
                if not match:
                    continue
                item = {"revision": oid, "path_display": entry.path_display, "path_b64": entry.path_b64,
                        "blob_oid": entry.oid, "line": line_no, "text": line[:2000], "symbol": symbol,
                        "classification": "reference"}
                item["evidence_id"] = "reference-" + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:88]
                items.append(item)
        status: ToolStatus = "ok" if not unreadable else "partial"
        return self._receipt(started, tool_call_id, "find_references", {"revision": revision, "symbol": symbol, "scope_paths": scope_paths}, oid, status, items[:self.page_size], [item["evidence_id"] for item in items[:self.page_size]], coverage={"files_scanned": len(sources), "files_unreadable": unreadable, "reference_count": len(items), "complete": len(items) <= self.page_size}, diagnostics=["REFERENCE_SCOPE_PARTIAL"] if unreadable else [])

    def get_history(self, *, tool_call_id: str, revision: str, path: str | None = None,
                    limit: int = 20) -> ResearchReceipt:
        started = time.monotonic()
        if limit < 1 or limit > 100:
            raise ResearchToolError("HISTORY_LIMIT_INVALID")
        oid = self._revision(revision)
        if path is not None:
            self._entry(oid, path)
        rows = self.git.history(oid, limit, path)
        items = []
        for row in rows:
            item = dict(row)
            item["evidence_id"] = "history-" + hashlib.sha256(json.dumps({"path": path, **row}, sort_keys=True).encode()).hexdigest()[:88]
            item["path_display"] = path
            items.append(item)
        return self._receipt(started, tool_call_id, "get_history", {"revision": revision, "path": path, "limit": limit}, oid, "ok", items, [item["evidence_id"] for item in items], coverage={"commit_count": len(items), "complete": len(items) < limit})

    def execute(self, tool_name: str, arguments: dict[str, Any], *, tool_call_id: str) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ResearchToolError("ARGUMENTS_INVALID")
        if tool_name == "read_source":
            return self.read_source(tool_call_id=tool_call_id, **arguments).as_dict()
        if tool_name == "search_sources":
            return self.search_sources(tool_call_id=tool_call_id, **arguments).as_dict()
        if tool_name == "get_diff":
            return self.get_diff(tool_call_id=tool_call_id, **arguments).as_dict()
        if tool_name == "lookup_symbol":
            return self.lookup_symbol(tool_call_id=tool_call_id, **arguments).as_dict()
        if tool_name == "find_references":
            return self.find_references(tool_call_id=tool_call_id, **arguments).as_dict()
        if tool_name == "get_history":
            return self.get_history(tool_call_id=tool_call_id, **arguments).as_dict()
        raise ResearchToolError("UNKNOWN_TOOL")


def research_tool_definitions() -> tuple[dict[str, Any], ...]:
    """Return the closed, read-only tool surface exposed to the model."""
    revision = {"type": "string", "minLength": 40, "maxLength": 64}
    path = {"type": "string", "minLength": 1, "maxLength": 512}
    scope_paths = {"type": "array", "maxItems": 64, "items": path}
    return (
        {"type": "function", "function": {"name": "read_source", "description": "Read bounded source lines at a fixed revision.", "parameters": {"type": "object", "additionalProperties": False, "required": ["revision", "path"], "properties": {"revision": revision, "path": path, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}}}}},
        {"type": "function", "function": {"name": "search_sources", "description": "Search in-scope SQL source at a fixed revision.", "parameters": {"type": "object", "additionalProperties": False, "required": ["revision", "query"], "properties": {"revision": revision, "query": {"type": "string", "minLength": 1, "maxLength": 500}, "literal": {"type": "boolean"}, "cursor": {"type": ["string", "null"]}, "scope_paths": scope_paths}}}},
        {"type": "function", "function": {"name": "get_diff", "description": "Read the bounded source delta between two fixed revisions.", "parameters": {"type": "object", "additionalProperties": False, "required": ["base_revision", "target_revision"], "properties": {"base_revision": revision, "target_revision": revision, "path": path}}}},
        {"type": "function", "function": {"name": "lookup_symbol", "description": "Find bounded symbol definitions in source.", "parameters": {"type": "object", "additionalProperties": False, "required": ["revision", "symbol"], "properties": {"revision": revision, "symbol": {"type": "string", "minLength": 1, "maxLength": 200}, "symbol_kind": {"type": ["string", "null"], "maxLength": 80}, "scope_paths": scope_paths}}}},
        {"type": "function", "function": {"name": "find_references", "description": "Find bounded source references for a symbol.", "parameters": {"type": "object", "additionalProperties": False, "required": ["revision", "symbol"], "properties": {"revision": revision, "symbol": {"type": "string", "minLength": 1, "maxLength": 200}, "scope_paths": scope_paths}}}},
        {"type": "function", "function": {"name": "get_history", "description": "Read bounded path history at a fixed revision.", "parameters": {"type": "object", "additionalProperties": False, "required": ["revision"], "properties": {"revision": revision, "path": {"type": ["string", "null"], "maxLength": 512}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}}},
    )

