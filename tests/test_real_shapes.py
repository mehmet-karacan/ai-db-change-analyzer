from __future__ import annotations

import collections
import hashlib
import zipfile
from pathlib import Path

from db_change_analyzer.oracle.scanner import scan


ROOT = Path(__file__).resolve().parents[1]


def test_supplied_metadata_full_lexical_inventory_is_hash_bound() -> None:
    path = ROOT / "docs" / "gpu-fusion-metadata.zip"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "2f81ffe06e261c3d2870b17074ac03f891c77fb42237fd97f95109aeef1a7322"
    types: collections.Counter[str] = collections.Counter()
    keys: collections.Counter[str] = collections.Counter()
    multi = 0
    trigger_states: collections.Counter[str] = collections.Counter()
    with zipfile.ZipFile(path) as archive:
        sql = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".sql")]
        assert len(sql) == 2340
        for info in sql:
            result = scan(archive.read(info), default_schema="GPU_USER")
            assert not result.diagnostics, (info.filename, result.diagnostics)
            multi += len(result.occurrences) > 1
            for occurrence in result.occurrences:
                types[occurrence.object_type] += 1
                keys[occurrence.object_key] += 1
                if occurrence.trailing_trigger_state:
                    trigger_states[occurrence.trailing_trigger_state] += 1
    assert sum(types.values()) == 2382
    assert len(keys) == 2380
    assert sum(value > 1 for value in keys.values()) == 2
    assert multi == 42
    assert types == {
        "TABLE": 981,
        "INDEX": 576,
        "SEQUENCE": 556,
        "VIEW": 130,
        "TRIGGER": 41,
        "PACKAGE_SPEC": 40,
        "PACKAGE_BODY": 40,
        "TYPE": 16,
        "PROCEDURE": 2,
    }
    assert trigger_states == {"ENABLE": 40, "DISABLE": 1}


def test_x18_k4_package_without_slash_keeps_both_headers_without_history_pairing() -> None:
    path = ROOT / "docs" / "gpu-fusion-release-5.1.0@20effa77e29.zip"
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("gpu-backend/src/main/resources/sql-scripts/PCK_BATCH_JOB.sql")
    result = scan(raw, default_schema="GPU_USER")
    assert [item.object_type for item in result.occurrences[:2]] == ["PACKAGE_SPEC", "PACKAGE_BODY"]
    assert result.occurrences[0].end_byte_exclusive == result.occurrences[1].start_byte
