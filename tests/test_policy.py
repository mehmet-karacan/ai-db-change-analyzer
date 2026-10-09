from __future__ import annotations

from pathlib import Path

import pytest

from db_change_analyzer.policy import PolicyLoadError, load_policy_bundle


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_policy_bundle_has_stable_metadata() -> None:
    bundle = load_policy_bundle(ROOT / "src" / "db_change_analyzer")
    assert bundle.analysis.version == "source-review-tr/1.0"
    assert bundle.language.version == "report-language-tr/1.0"
    assert len(bundle.fingerprint) == 64
    assert bundle.analysis.sha256 != bundle.language.sha256


def test_missing_policy_fails_closed(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "analysis_policy.tr.md").write_text(
        "<!-- policy-version: source-review-tr/1.0 -->\ntext", encoding="utf-8"
    )
    with pytest.raises(PolicyLoadError, match="POLICY_MISSING"):
        load_policy_bundle(tmp_path)


def test_policy_bytes_change_fingerprint(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name, version in (("analysis_policy.tr.md", "a"), ("report_language.tr.md", "b")):
        (prompts / name).write_text(f"<!-- policy-version: {version}/1.0 -->\ntext", encoding="utf-8")
    first = load_policy_bundle(tmp_path).fingerprint
    (prompts / "report_language.tr.md").write_text("<!-- policy-version: b/1.0 -->\nchanged", encoding="utf-8")
    assert load_policy_bundle(tmp_path).fingerprint != first

