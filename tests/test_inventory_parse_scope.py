from __future__ import annotations

from pathlib import Path

from db_change_analyzer.git_client import TreeEntry
from db_change_analyzer.inventory import _parse_in_worker, inventory_bytes, inventory_revision


def test_parser_worker_ignores_a_shadow_package_in_current_directory(tmp_path: Path, monkeypatch) -> None:
    shadow = tmp_path / "db_change_analyzer"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('untrusted cwd executed')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ok, diagnostics = _parse_in_worker(b"CREATE TABLE T (ID NUMBER);", 20)
    assert ok and diagnostics == ()


def test_revision_only_structurally_parses_selected_paths(monkeypatch) -> None:
    raw = b"CREATE TABLE T (ID NUMBER);"
    entry = TreeEntry(mode="100644", kind="blob", oid="1" * 40, path=b"gpu_user/a.sql")

    class FakeGit:
        def list_tree(self, _revision):
            return [entry]

        def read_blob(self, _oid, _maximum):
            return raw

    calls = []

    def fake_parse(_raw, _timeout):
        calls.append(True)
        return True, ()

    monkeypatch.setattr("db_change_analyzer.inventory._parse_in_worker", fake_parse)
    skipped = inventory_revision(FakeGit(), "2" * 40, {"gpu_user": "GPU_USER"}, max_file_bytes=1024, timeout_seconds=20, parse_paths=set())
    assert skipped[0].parse_status == "text_fallback"
    assert skipped[0].diagnostics == ("PARSER_NOT_RUN",)
    assert skipped[0].occurrences
    assert calls == []

    selected = inventory_revision(FakeGit(), "2" * 40, {"gpu_user": "GPU_USER"}, max_file_bytes=1024, timeout_seconds=20, parse_paths={entry.path})
    assert selected[0].parse_status == "parsed"
    assert calls == [True]


def test_inventory_retries_scanned_objects_when_combined_file_parse_fails(monkeypatch) -> None:
    raw = b"CREATE TABLE S.A (ID NUMBER);\nCREATE TABLE S.B (ID NUMBER);\n"
    calls: list[tuple[str, ...]] = []

    def fake_parse(_raw, _timeout):
        return False, ("PARSER_TIMEOUT",)

    def fake_fragments(fragments, _timeout):
        calls.append(fragments)
        return tuple((True, ()) for _ in fragments)

    monkeypatch.setattr("db_change_analyzer.inventory._parse_in_worker", fake_parse)
    monkeypatch.setattr("db_change_analyzer.inventory._parse_fragments_in_worker", fake_fragments)

    scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="S", parse_timeout_seconds=20)

    assert len(scanned.occurrences) == 2
    assert status == "parsed"
    assert diagnostics == ()
    assert all(projection.support == "structural" for projection in projections)
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_inventory_keeps_text_fallback_when_any_scanned_object_is_unverified(monkeypatch) -> None:
    raw = b"CREATE TABLE S.A (ID NUMBER);\nCREATE TABLE S.B (ID NUMBER);\n"

    monkeypatch.setattr("db_change_analyzer.inventory._parse_in_worker", lambda _raw, _timeout: (False, ("PARSER_TIMEOUT",)))
    monkeypatch.setattr(
        "db_change_analyzer.inventory._parse_fragments_in_worker",
        lambda _fragments, _timeout: ((True, ()), (False, ("SYNTAX_ERROR",))),
    )

    _scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="S", parse_timeout_seconds=20)

    assert status == "text_fallback"
    assert diagnostics == ("PARSER_TIMEOUT",)
    assert all(projection.support == "text_fallback" for projection in projections)


def test_parser_cache_reuses_same_blob_and_timeout(monkeypatch) -> None:
    import db_change_analyzer.inventory as inventory
    from types import SimpleNamespace

    inventory._parse_cache.clear()
    calls = 0

    def fake_worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout=b'{"ok": true, "lexer_errors": [], "parser_errors": []}')

    monkeypatch.setattr(inventory.subprocess, "run", fake_worker)

    assert _parse_in_worker(b"CREATE TABLE CACHE_T (ID NUMBER);", 20) == (True, ())
    assert _parse_in_worker(b"CREATE TABLE CACHE_T (ID NUMBER);", 20) == (True, ())
    assert calls == 1


def test_parser_cache_can_reuse_persisted_result_after_process_cache_clear(monkeypatch) -> None:
    import db_change_analyzer.inventory as inventory
    from types import SimpleNamespace

    inventory._parse_cache.clear()
    persisted: dict[tuple[str, str], bytes] = {}
    calls = 0

    class Cache:
        def get_cache_entry(self, key, *, kind, version_fingerprint):
            return persisted.get((kind, key))

        def put_cache_entry(self, key, content, *, kind, version_fingerprint):
            persisted[(kind, key)] = content

    def fake_worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout=b'{"ok": true, "lexer_errors": [], "parser_errors": []}')

    monkeypatch.setattr(inventory.subprocess, "run", fake_worker)
    raw = b"CREATE TABLE PERSISTED_T (ID NUMBER);"
    assert _parse_in_worker(raw, 20, Cache()) == (True, ())
    inventory._parse_cache.clear()
    assert _parse_in_worker(raw, 20, Cache()) == (True, ())
    assert calls == 1


def test_large_combined_export_skips_unbounded_whole_file_retry(monkeypatch) -> None:
    raw = b"\n".join(f"CREATE TABLE S.T{index} (ID NUMBER);".encode() for index in range(33))
    calls = []
    monkeypatch.setattr("db_change_analyzer.inventory._parse_in_worker", lambda *_args: calls.append("whole") or (True, ()))
    monkeypatch.setattr("db_change_analyzer.inventory._parse_fragments_in_worker", lambda *_args: calls.append("fragments") or ())

    _scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="S", parse_timeout_seconds=20)

    assert status == "text_fallback"
    assert diagnostics == ("PARSER_NOT_RUN_LARGE_EXPORT",)
    assert calls == []
    assert all(projection.support == "text_fallback" for projection in projections)
