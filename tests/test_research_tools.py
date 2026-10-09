from __future__ import annotations

from pathlib import Path

import pytest

from db_change_analyzer.git_client import GitClient
from db_change_analyzer.research_tools import ResearchToolDispatcher, ResearchToolError
from tests.helpers.git_fixture import GitFixture


def dispatcher(tmp_path: Path, *, page_size: int = 100) -> tuple[GitFixture, ResearchToolDispatcher, str]:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/a.sql", "-- comment\nCREATE TABLE GPU_USER.A (ID NUMBER);\n")
    target = fixture.commit("base")
    client = GitClient(tmp_path / "cache.git", allow_file_protocol=True)
    fetched = client.fetch(str(fixture.root), "main")
    return fixture, ResearchToolDispatcher(client, {"gpu_user": "GPU_USER"}, scope_hash="s" * 64, page_size=page_size), fetched


def test_read_source_is_revision_and_scope_bound(tmp_path: Path) -> None:
    _, tools, target = dispatcher(tmp_path)
    result = tools.read_source(tool_call_id="c1", revision=target, path="gpu_user/a.sql")
    assert result.status == "ok"
    assert result.items[0]["start_line"] == 1
    with pytest.raises(ResearchToolError, match="PATH_OUT_OF_SCOPE"):
        tools.read_source(tool_call_id="c2", revision=target, path="README.md")


def test_search_cursor_cannot_cross_revision(tmp_path: Path) -> None:
    _, tools, target = dispatcher(tmp_path)
    result = tools.search_sources(tool_call_id="c1", revision=target, query="GPU_USER.A")
    assert result.items and result.evidence_ids
    if result.continuation:
        with pytest.raises(ResearchToolError, match="CURSOR_SCOPE_MISMATCH"):
            tools.search_sources(tool_call_id="c2", revision=target, query="other", cursor=result.continuation)


def test_search_cursor_paginates_until_scope_is_complete(tmp_path: Path) -> None:
    fixture, tools, _ = dispatcher(tmp_path, page_size=2)
    for index in range(3):
        fixture.write(f"gpu_user/match_{index}.sql", f"SELECT PAGINATED_SENTINEL FROM GPU_USER.T_{index};\n")
    fixture.commit("add paginated matches")
    target = tools.git.fetch(str(fixture.root), "main")

    first = tools.search_sources(tool_call_id="page-1", revision=target, query="PAGINATED_SENTINEL")
    assert len(first.items) == 2
    assert first.coverage["match_count"] == 3
    assert first.coverage["complete"] is False
    assert first.continuation

    second = tools.search_sources(
        tool_call_id="page-2", revision=target, query="PAGINATED_SENTINEL", cursor=first.continuation,
    )
    assert len(second.items) == 1
    assert second.continuation is None
    assert second.coverage["complete"] is True
    assert len({item["evidence_id"] for item in (*first.items, *second.items)}) == 3
    with pytest.raises(ResearchToolError, match="CURSOR_SCOPE_MISMATCH"):
        tools.search_sources(tool_call_id="page-3", revision=target, query="GPU_USER.T_0", cursor=first.continuation)


def test_get_diff_keeps_unnamed_out_of_scope_deltas_out(tmp_path: Path) -> None:
    fixture, tools, base = dispatcher(tmp_path)
    fixture.write("README.md", "outside\n")
    fixture.write("gpu_user/a.sql", "CREATE TABLE GPU_USER.A (ID NUMBER, NAME VARCHAR2(20));\n")
    fixture.commit("change")
    target = tools.git.fetch(str(fixture.root), "main")
    result = tools.get_diff(tool_call_id="c1", base_revision=base, target_revision=target)
    assert [item["path_display"] for item in result.items] == ["gpu_user/a.sql"]


def test_symbol_reference_and_history_tools_are_evidence_bound(tmp_path: Path) -> None:
    fixture, tools, base = dispatcher(tmp_path)
    fixture.write("gpu_user/a.sql", "CREATE TABLE GPU_USER.A (ID NUMBER);\n")
    fixture.write("gpu_user/use_a.sql", "SELECT ID FROM GPU_USER.A;\n")
    fixture.commit("add source")
    target = tools.git.fetch(str(fixture.root), "main")

    definition = tools.execute("lookup_symbol", {"revision": target, "symbol": "A"}, tool_call_id="c1")
    references = tools.execute("find_references", {"revision": target, "symbol": "A"}, tool_call_id="c2")
    history = tools.execute("get_history", {"revision": target, "path": "gpu_user/a.sql"}, tool_call_id="c3")

    assert definition["status"] == "ok" and definition["items"][0]["classification"] == "definition"
    assert references["status"] == "ok" and references["items"]
    assert history["status"] == "ok" and history["items"][0]["commit"] == target

