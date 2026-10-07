from __future__ import annotations

from db_change_analyzer.history import CommitRecord, CommitDelta, RangePlan
from db_change_analyzer.git_client import RawDelta
from db_change_analyzer.oracle.changes import Change, ChangeSet, Value
from db_change_analyzer.workflow import _commit_rows, _deployment_preparation, _risk_assessment


def _change(taxonomy_id: str, *, after: str = "new", path: tuple[str, ...] = ()) -> Change:
    return Change(
        fact_id="fact-1", taxonomy_id=taxonomy_id, component_path=path,
        action="modified", category="contract",
        before=Value("present", "old", ("ev-old",)),
        after=Value("present", after, ("ev-new",)),
        context_only=False, verification="verified",
    )


def test_commit_rows_keep_every_commit_file_transition() -> None:
    first = "a" * 40
    second = "b" * 40
    commit = CommitRecord(second, (first,), "2026-10-07T10:00:00+00:00", "2026-10-07T10:01:00+00:00", 0)
    delta = RawDelta("100644", "100644", "0" * 40, "c" * 40, "M", b"gpu_user/t.sql")
    rows = _commit_rows(RangePlan("ANALYZE", first, second, (commit,), (CommitDelta(commit, first, (delta,)),), (delta,), (delta,)))

    assert rows[0]["sha"] == second
    assert rows[0]["files"] == [{
        "path_display": "gpu_user/t.sql", "path_b64": "Z3B1X3VzZXIvdC5zcWw=", "operation": "modified",
        "old_mode": "100644", "new_mode": "100644", "old_blob": None, "new_blob": "c" * 40,
    }]


def test_risk_and_deployment_outputs_are_conservative_and_bound_to_evidence() -> None:
    key = "SCHEMA|GPU_USER|TABLE|T"
    objects = [{
        "identity": {"object_key": key, "object_type": "TABLE", "raw_schema": "GPU_USER", "raw_name": "T", "schema_name": "GPU_USER", "name": "T"},
        "net_operation": "modified", "status": "analyzed",
    }]
    changes = {key: ChangeSet(key, "TABLE", "modified", (_change("table.column.nullable", after="false", path=("ID",)),), (), ())}

    risk = _risk_assessment(objects, changes, ())
    deployment = _deployment_preparation(objects, changes)

    assert risk["level"] == "high"
    assert risk["confidence"] == "limited"
    assert deployment["status"] == "prepared_not_executed"
    assert deployment["checks"][0]["code"] == "NULL_DATA_CHECK"
    assert deployment["checks"][0]["execution"] == "not_run"
    assert deployment["checks"][0]["evidence_ids"] == ["ev-new", "ev-old"]
