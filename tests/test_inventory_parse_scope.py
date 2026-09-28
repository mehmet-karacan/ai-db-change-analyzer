from __future__ import annotations

from db_change_analyzer.git_client import TreeEntry
from db_change_analyzer.inventory import inventory_revision


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
