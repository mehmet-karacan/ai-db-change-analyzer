from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_corpus", ROOT / "tools" / "prepare_corpus.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("name", ["../escape.sql", "/absolute.sql", "C:/drive.sql"])
def test_i05_rejects_unsafe_member_paths(tmp_path: Path, name: str) -> None:
    archive = _zip(tmp_path / "bad.zip", {"manifest.txt": b"Schema: X\n", name: b"select 1;"})
    with pytest.raises(MODULE.CorpusError):
        MODULE.prepare(archive, tmp_path / "out")


def test_x16_application_archive_cannot_be_metadata(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "application.zip", {"README.md": b"app", "x.sql": b"select 1;"})
    with pytest.raises(MODULE.CorpusError, match="manifest"):
        MODULE.prepare(archive, tmp_path / "out")


def test_x20_provenance_is_not_inferred_from_name_or_mtime(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "metadata@deadbeef.zip", {"manifest.txt": b"Schema: GPU_USER\n", "tables/T.sql": b"CREATE TABLE GPU_USER.T (ID NUMBER);\r\n"})
    result = MODULE.prepare(archive, tmp_path / "out")
    provenance = result["sources"][0]["provenance"]
    assert provenance == {"git_history_present": False, "verified_source_commit": None, "snapshot_captured_at": None, "history_kind": "none"}


def test_supplied_metadata_is_measured_and_identity_mismatch_is_visible(tmp_path: Path) -> None:
    archive = ROOT / "docs" / "gpu-fusion-metadata.zip"
    assert archive.is_file(), "the user supplied this corpus; it must not be silently skipped"
    result = MODULE.prepare(archive, tmp_path / "out")
    source = result["sources"][0]
    assert source["archive_sha256"] == "2f81ffe06e261c3d2870b17074ac03f891c77fb42237fd97f95109aeef1a7322"
    assert source["observed"]["file_count"] == 2341
    assert source["observed"]["sql_count"] == 2340
    assert any(item.startswith("CORPUS_IDENTITY_MISMATCH") for item in source["warnings"])
