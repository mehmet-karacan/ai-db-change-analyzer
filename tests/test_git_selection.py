from __future__ import annotations

from pathlib import Path

import pytest

from db_change_analyzer.git_client import GitClient
from db_change_analyzer.history import HistoryError, HistoryPlanner
from db_change_analyzer.workflow import _commit_rows, _history_events
from tests.helpers.git_fixture import GitFixture


def fetched(tmp_path: Path, fixture: GitFixture) -> tuple[GitClient, str]:
    client = GitClient(tmp_path / "cache.git", allow_file_protocol=True)
    target = client.fetch(str(fixture.root), "main")
    return client, target


def test_g01_no_change_and_g02_out_of_scope(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    base = fixture.commit("base")
    client, target = fetched(tmp_path, fixture)
    planner = HistoryPlanner(client, ["gpu_user"])
    assert planner.automatic(base, target).outcome == "NO_CHANGE"
    fixture.write("README.md", "outside\n")
    expected = fixture.commit("outside")
    target = client.fetch(str(fixture.root), "main")
    plan = planner.automatic(base, target)
    assert target == expected
    assert plan.outcome == "OUT_OF_SCOPE_ONLY"
    assert _history_events(plan) == []


def test_g05_revert_has_empty_net_but_two_history_transitions(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    base = fixture.commit("base")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER NOT NULL);\n")
    fixture.commit("change")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    target_expected = fixture.commit("revert")
    client, target = fetched(tmp_path, fixture)
    plan = HistoryPlanner(client, ["gpu_user"]).automatic(base, target)
    assert target == target_expected
    assert len(plan.commits) == 2
    assert len(plan.event_deltas) == 2
    assert len(plan.net_deltas) == 0
    assert plan.outcome == "ANALYZE"
    events = _history_events(plan)
    assert len(events) == 2
    assert [item["commit_sha"] for item in events] == [item.sha for item in plan.commits]
    assert [item["parent_sha"] for item in events] == [base, plan.commits[0].sha]
    assert all(item["operation"] == "modified" for item in events)
    assert len({item["event_id"] for item in events}) == 2


def test_g09_manual_root_uses_empty_tree_and_rejects_non_root(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    root = fixture.commit("root")
    client, _ = fetched(tmp_path, fixture)
    plan = HistoryPlanner(client, ["gpu_user"]).manual_root(root)
    assert len(plan.net_deltas) == 1
    assert _commit_rows(plan)[0]["delta_kind"] == "root"
    fixture.write("gpu_user/u.sql", "CREATE TABLE GPU_USER.U (ID NUMBER);\n")
    child = fixture.commit("child")
    client.fetch(str(fixture.root), "main")
    with pytest.raises(HistoryError, match="parent"):
        HistoryPlanner(client, ["gpu_user"]).manual_root(child)


def test_g10_side_commit_and_merge_are_in_reachable_ledger(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    base = fixture.commit("base")
    fixture.branch("side", base)
    fixture.write("gpu_user/s.sql", "CREATE SEQUENCE GPU_USER.S;\n")
    side = fixture.commit("side")
    fixture.switch("main")
    fixture.write("README.md", "main\n")
    fixture.commit("main")
    fixture.run("merge", "--no-ff", "side", "-m", "merge")
    client, target = fetched(tmp_path, fixture)
    plan = HistoryPlanner(client, ["gpu_user"]).automatic(base, target)
    assert side in [item.sha for item in plan.commits]
    assert len(plan.commits) == 3
    assert plan.outcome == "ANALYZE"
    roles = {item["sha"]: item["integration_role"] for item in _commit_rows(plan)}
    assert roles[side] == "other_reachable"
    assert roles[target] == "target_first_parent_chain"


def test_g16_divergence_fails_closed(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    base = fixture.commit("base")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER NOT NULL);\n")
    old_target = fixture.commit("old")
    client, _ = fetched(tmp_path, fixture)
    fixture.run("reset", "--hard", base)
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID VARCHAR2(2));\n")
    fixture.commit("rewritten")
    target = client.fetch(str(fixture.root), "main")
    with pytest.raises(HistoryError, match="ancestor"):
        HistoryPlanner(client, ["gpu_user"]).automatic(old_target, target)


def test_g18_backlog_limit_does_not_silently_truncate(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path / "source")
    fixture.write("gpu_user/t.sql", "CREATE TABLE GPU_USER.T (ID NUMBER);\n")
    base = fixture.commit("base")
    for index in range(3):
        fixture.write("gpu_user/t.sql", f"CREATE TABLE GPU_USER.T (ID NUMBER); -- {index}\n")
        fixture.commit(str(index))
    client, target = fetched(tmp_path, fixture)
    with pytest.raises(HistoryError) as error:
        HistoryPlanner(client, ["gpu_user"], maximum_commits=2).automatic(base, target)
    assert error.value.code == "BACKLOG_LIMIT"


def test_g20_tab_and_newline_path_roundtrips_as_raw_bytes(tmp_path: Path) -> None:
    weird = b"gpu_user/tab\tline\nname.sql"
    raw = b":100644 100644 " + (b"a" * 40) + b" " + (b"b" * 40) + b" M\x00" + weird + b"\x00"
    delta = GitClient._parse_raw_delta(raw)
    assert delta[0].path == weird
