from __future__ import annotations

import json
from pathlib import Path

import pytest

from db_change_analyzer.research_journal import ResearchJournal, ResearchJournalError


def _journal(tmp_path: Path) -> ResearchJournal:
    return ResearchJournal(tmp_path / "scope", scope_hash="a" * 64, run_id="run-1", generation=0)


def test_journal_is_hash_chained_deduplicated_and_redacted(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    receipt = {
        "tool_call_id": "call-1",
        "tool_name": "read_source",
        "arguments_digest": "b" * 64,
        "items": [{"snippet": "SECRET_VALUE_MUST_NOT_BE_WRITTEN"}],
        "evidence_ids": ["evidence-1"],
        "status": "ok",
        "coverage": {"complete": True},
        "diagnostics": [],
        "elapsed_ms": 1,
    }
    journal.append_receipt(receipt, unit_id="unit-1", attempt=1, turn=1)
    journal.append_unit_status(
        unit_id="unit-1", attempt=1, status="VALIDATED", request_digest="c" * 64,
        result_digest="d" * 64, diagnostics=[],
    )

    records = journal.verify()
    assert len(records) == 2
    assert journal.has_receipt(unit_id="unit-1", tool_call_id="call-1")
    assert "SECRET_VALUE_MUST_NOT_BE_WRITTEN" not in journal.directory.joinpath("00000001.json").read_text(encoding="utf-8")
    assert ResearchJournal(journal.scope_root, scope_hash="a" * 64, run_id="run-1", generation=0).verify() == records


def test_journal_rejects_tampering_and_missing_index(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append_unit_status(
        unit_id="unit-1", attempt=1, status="VALIDATED", request_digest="c" * 64,
        result_digest="d" * 64, diagnostics=[],
    )
    record_path = journal.directory / "00000001.json"
    value = json.loads(record_path.read_text(encoding="utf-8"))
    value["status"] = "INVALID"
    record_path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ResearchJournalError, match="hash mismatch"):
        journal.verify()

    journal = _journal(tmp_path / "second")
    journal.append_unit_status(
        unit_id="unit-1", attempt=1, status="VALIDATED", request_digest="c" * 64,
        result_digest="d" * 64, diagnostics=[],
    )
    journal.index_path.unlink()
    with pytest.raises(ResearchJournalError, match="index is missing"):
        journal.verify()


def test_journal_rejects_sequence_gaps(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    for attempt in (1, 2):
        journal.append_unit_status(
            unit_id="unit-1", attempt=attempt, status="VALIDATED", request_digest="c" * 64,
            result_digest="d" * 64, diagnostics=[],
        )
    (journal.directory / "00000001.json").unlink()
    with pytest.raises(ResearchJournalError, match="sequence has a gap"):
        journal.verify()
