#!/usr/bin/env python3
"""Safely prepare local, non-runtime corpus evidence from trusted ZIP inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "1.0"
OWNER_MARKER = ".db-change-analyzer-corpus-owner.json"
MAX_ENTRIES = 20_000
MAX_TOTAL = 250 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
EXPECTED_METADATA_SHA256 = "cb2a5dba5029517a5cdd04ad71ab5c097c80ef6703e60e24a6ecac5fc1731c88"
EXPECTED_APPLICATION_SHA256 = "6f68afc1458bc86e252ad3f020765b3e2b7fd06f2a383004bb4cd3a9aaf99d2d"


class CorpusError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    pure = PurePosixPath(name)
    if not name or name.startswith("/") or name.startswith("\\"):
        raise CorpusError(f"unsafe absolute ZIP path: {name!r}")
    if pure.is_absolute() or pure.drive or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise CorpusError(f"unsafe ZIP path: {name!r}")
    if "\x00" in name:
        raise CorpusError("NUL in ZIP path")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise CorpusError(f"symlink ZIP entry rejected: {name}")
    if info.flag_bits & 0x1:
        raise CorpusError(f"encrypted ZIP entry rejected: {name}")
    return pure


def _newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    bare_cr = data.count(b"\r") - crlf
    kinds = sum(value > 0 for value in (crlf, bare_lf, bare_cr))
    if kinds > 1:
        return "MIXED"
    if crlf:
        return "CRLF"
    if bare_lf:
        return "LF"
    if bare_cr:
        return "CR"
    return "NONE"


def _decode(data: bytes) -> tuple[str, str]:
    if b"\x00" in data:
        return "binary", "BINARY"
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "binary", "BINARY"
    return "utf-8", _newline_style(data)


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _declared_manifest(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"parse_status": "invalid_utf8"}
    result: dict[str, Any] = {"parse_status": "text", "fields": {}}
    fields = result["fields"]
    for line in text.splitlines():
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        canonical = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if canonical in {"total_objects_found", "ddl_files_written", "errors"}:
            try:
                fields[canonical] = int(value)
            except ValueError:
                fields[canonical] = value
        elif canonical == "schema":
            fields[canonical] = value
        elif canonical == "database":
            fields[canonical] = "redacted-db-locator"
    return result


def _owned_output(output: Path) -> None:
    output = output.resolve()
    if output.exists():
        entries = list(output.iterdir())
        marker = output / OWNER_MARKER
        if entries and not marker.is_file():
            raise CorpusError("output is non-empty and has no analyzer owner marker")
        if marker.is_file():
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
            if marker_data != {"schema_version": SCHEMA_VERSION, "owner": "db-change-analyzer-corpus"}:
                raise CorpusError("output owner marker mismatch")
            for child in entries:
                if child.name == OWNER_MARKER:
                    continue
                if child.is_symlink():
                    raise CorpusError("symlink in owned output")
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    else:
        output.mkdir(parents=True)
    (output / OWNER_MARKER).write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "owner": "db-change-analyzer-corpus"}, sort_keys=True),
        encoding="utf-8",
    )


def _read_archive(path: Path, kind: str, output: Path) -> dict[str, Any]:
    archive_hash = sha256_file(path)
    expected = EXPECTED_METADATA_SHA256 if kind == "ddl_snapshot_corpus" else EXPECTED_APPLICATION_SHA256
    prefix = "metadata" if kind == "ddl_snapshot_corpus" else "application-sql"
    warnings: list[str] = []
    if archive_hash != expected:
        warnings.append(f"CORPUS_IDENTITY_MISMATCH expected={expected} observed={archive_hash}")

    copied: list[dict[str, Any]] = []
    roots: Counter[str] = Counter()
    encodings: Counter[str] = Counter()
    newlines: Counter[str] = Counter()
    seen: set[str] = set()
    manifest_bytes: bytes | None = None
    total = 0
    file_count = 0
    sql_count = 0

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise CorpusError("ZIP entry limit exceeded")
        for info in infos:
            pure = _safe_name(info)
            canonical = pure.as_posix()
            if canonical in seen:
                raise CorpusError(f"duplicate ZIP path: {canonical}")
            seen.add(canonical)
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE:
                raise CorpusError(f"ZIP member exceeds file limit: {canonical}")
            total += info.file_size
            if total > MAX_TOTAL:
                raise CorpusError("ZIP total size limit exceeded")
            file_count += 1
            roots[pure.parts[0]] += 1
            with archive.open(info) as source:
                data = source.read(MAX_FILE + 1)
            if len(data) != info.file_size or len(data) > MAX_FILE:
                raise CorpusError(f"ZIP member size mismatch: {canonical}")
            if canonical == "manifest.txt":
                manifest_bytes = data
            is_sql = pure.suffix.lower() == ".sql"
            if is_sql:
                sql_count += 1
            encoding, newline = _decode(data)
            if is_sql:
                encodings[encoding] += 1
                newlines[newline] += 1

            copy_member = is_sql and (kind == "ddl_snapshot_corpus" or "/src/main/resources/sql-scripts/" in f"/{canonical}")
            if not copy_member:
                continue
            destination = (output / prefix / Path(*pure.parts)).resolve()
            prefix_root = (output / prefix).resolve()
            if destination != prefix_root and prefix_root not in destination.parents:
                raise CorpusError("destination containment failure")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            copied.append(
                {
                    "path": canonical,
                    "raw_sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "lines": _line_count(data),
                    "encoding": encoding,
                    "newline_style": newline,
                }
            )

    if kind == "ddl_snapshot_corpus" and manifest_bytes is None:
        raise CorpusError("metadata corpus has no manifest.txt")
    if kind == "application_source_corpus" and not any("gpu-backend" in item["path"] for item in copied):
        raise CorpusError("application archive does not match the expected application-source role")
    return {
        "source_kind": kind,
        "archive_name": path.name,
        "archive_sha256": archive_hash,
        "output_prefix": prefix,
        "observed": {
            "entry_count": len(seen),
            "file_count": file_count,
            "sql_count": sql_count,
            "raw_total_bytes": total,
            "root_counts": dict(sorted(roots.items())),
            "encoding_counts": dict(sorted(encodings.items())),
            "newline_counts": dict(sorted(newlines.items())),
        },
        "declared": _declared_manifest(manifest_bytes),
        "provenance": {
            "git_history_present": False,
            "verified_source_commit": None,
            "snapshot_captured_at": None,
            "history_kind": "none",
        },
        "files": sorted(copied, key=lambda item: item["path"]),
        "warnings": warnings,
    }


def prepare(metadata_archive: Path, output: Path, application_archive: Path | None = None) -> dict[str, Any]:
    metadata_archive = metadata_archive.resolve(strict=True)
    application_archive = application_archive.resolve(strict=True) if application_archive else None
    _owned_output(output)
    sources = [_read_archive(metadata_archive, "ddl_snapshot_corpus", output)]
    if application_archive:
        sources.append(_read_archive(application_archive, "application_source_corpus", output))
    warnings = [warning for source in sources for warning in source["warnings"]]
    manifest = {"schema_version": SCHEMA_VERSION, "sources": sources, "warnings": warnings}
    (output / "corpus-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-archive", type=Path, required=True)
    parser.add_argument("--application-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = prepare(args.metadata_archive, args.output, args.application_archive)
    except (CorpusError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "sources": [{"kind": s["source_kind"], "sha256": s["archive_sha256"], "files": s["observed"]["file_count"], "sql": s["observed"]["sql_count"]} for s in manifest["sources"]], "warnings": manifest["warnings"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
