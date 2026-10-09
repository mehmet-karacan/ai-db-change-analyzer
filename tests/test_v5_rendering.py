from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db_change_analyzer.v5_rendering import V5RenderError, _value, build_render_manifest, render_v5_view


_FIXTURES = Path(__file__).parent / "fixtures" / "v5"


def _view(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _render(view: dict, **limits):
    return render_v5_view(
        view, sender="analyzer@example.invalid", recipients=["review@example.invalid"],
        message_id="<v5-test@example.invalid>", date=datetime(2026, 9, 29, tzinfo=UTC), **limits,
    )


@pytest.mark.parametrize("fixture", ["01-v5-showcase.view.json", "02-no-change.view.json", "03-sequence-only.view.json"])
def test_approved_fixtures_render_as_multipart_mail(fixture: str) -> None:
    rendered = _render(_view(fixture))
    assert rendered.profile == "full"
    assert len(rendered.html) <= 90_000
    assert len(rendered.mime) <= 150_000
    assert b"text/plain" in rendered.mime and b"text/html" in rendered.mime
    assert b"lang=\"tr\"" in rendered.html
    assert b"<script" not in rendered.html


def test_start_with_display_preserves_sign_and_arbitrarily_long_literal() -> None:
    digits = "1234567890" * 500
    fact = {"taxonomy_id": "sequence.sequence_property.start_with",
            "before": {"state": "present", "value": "+000123"},
            "after": {"state": "present", "value": "-" + digits}}
    assert _value(fact, "before") == "+000.123"
    shown = _value(fact, "after")
    assert shown.startswith("-12.345.678.901")
    assert shown.replace(".", "") == "-" + digits


def test_budget_rerenders_whole_profiles_and_fails_closed() -> None:
    view = _view("01-v5-showcase.view.json")
    full = _render(view)
    compact = _render(view, html_limit=len(full.html) - 1)
    assert compact.profile != "full"
    assert len(compact.html) < len(full.html)
    assert b"</html>" in compact.html
    mime_bounded = _render(view, mime_limit=len(full.mime) - 1)
    assert mime_bounded.profile != "full"
    assert len(mime_bounded.mime) < len(full.mime)
    assert b"</html>" in mime_bounded.html
    with pytest.raises(V5RenderError, match="MAIL_SIZE_LIMIT"):
        _render(view, html_limit=100, mime_limit=100)


def test_untrusted_source_is_escaped_and_url_is_allowlisted() -> None:
    view = deepcopy(_view("03-sequence-only.view.json"))
    view["objects"][0]["deterministic_summary_tr"] = "<script>alert(1)</script>"
    rendered = _render(view)
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.html
    assert b"<script>alert(1)</script>" not in rendered.html
    with pytest.raises(V5RenderError, match="REPORT_URL_REJECTED"):
        _render(view, report_url="https://allowed.example/reports/x?token=secret", allowed_link_hosts={"allowed.example"})
    with pytest.raises(V5RenderError, match="REPORT_URL_REJECTED"):
        _render(view, report_url="https://allowed.example.evil/reports/x", allowed_link_hosts={"allowed.example"})
    for unsafe in ("https://@allowed.example/reports/x", "https://allowed.example/reports/x\x7f"):
        with pytest.raises(V5RenderError, match="REPORT_URL_REJECTED"):
            _render(view, report_url=unsafe, allowed_link_hosts={"allowed.example"})


def test_fact_evidence_side_cannot_be_swapped() -> None:
    view = deepcopy(_view("03-sequence-only.view.json"))
    fact = view["objects"][0]["facts"][0]
    fact["before"]["evidence_ids"] = fact["after"]["evidence_ids"]
    with pytest.raises(V5RenderError, match="MAIL_VIEW_FACT_SIDE_MISMATCH"):
        _render(view)


def test_combined_unknown_and_history_only_objects_render_with_explicit_limits() -> None:
    view = deepcopy(_view("03-sequence-only.view.json"))
    original_object = view["objects"][0]
    original_evidence = view["evidence_registry"]

    def unique(value: object, old: str, new: str) -> object:
        return json.loads(json.dumps(value, ensure_ascii=False).replace(old, new))

    unknown = unique(original_object, "obj-004", "obj-unknown")
    unknown = unique(unknown, "ev-004", "ev-unknown")
    unknown = unique(unknown, "fact-004", "fact-unknown")
    unknown["identity"]["name"] = "UNKNOWN_OBJECT"
    unknown["operation"] = "unknown"
    unknown["pattern"] = "unknown"
    unknown["verification"] = "unresolved"
    unknown["facts"][0]["taxonomy_id"] = "common.source.text"
    unknown["facts"][0]["subject_name"] = "Kaynak metni"
    unknown["facts"][0]["subject_kind"] = "SOURCE"
    unknown["facts"][0]["change_action"] = "modified"
    for side in ("before", "after"):
        unknown["facts"][0][side] = {"state": "unknown", "value": None, "evidence_ids": ["ev-unknown-scope"]}
    unknown["evidence_ids"] = ["ev-unknown-scope"]
    unknown["deterministic_summary_tr"] = "Nesne durumu doğrulanamadı."
    unknown["source_checks"] = {"identity_match": "uncertain", "presence_comparison": "uncertain", "start_with_only_proved": False, "remainder_equal": False, "conflict_detected": False}

    history = unique(original_object, "obj-004", "obj-history")
    history = unique(history, "ev-004", "ev-history")
    history = unique(history, "fact-004", "fact-history")
    history["identity"]["name"] = "HISTORY_ONLY"
    history["operation"] = "added"
    history["verification"] = "limited"
    history["facts"][0]["change_action"] = "added"
    history["facts"][0]["context_only"] = True
    history["deterministic_summary_tr"] = "Yalnız tarihsel bağlam kaydıdır."

    scope_evidence = unique(original_evidence[2], "ev-004-scope", "ev-unknown-scope")
    scope_evidence["object_id"] = "obj-unknown"
    view["objects"] = [unknown, history]
    view["evidence_registry"] = [scope_evidence, *unique(original_evidence[:2], "ev-004", "ev-history")]
    view["evidence_registry"].extend(unique(item, "ev-004", "ev-history") for item in original_evidence)
    view["evidence_registry"] = [item for index, item in enumerate(view["evidence_registry"]) if item["evidence_id"] not in {previous["evidence_id"] for previous in view["evidence_registry"][:index]}]
    for item in view["evidence_registry"]:
        if item["evidence_id"].startswith("ev-history"):
            item["object_id"] = "obj-history"
    view["changed_files"] = 2

    rendered = _render(view)
    html = rendered.html.decode("utf-8")
    assert "UNKNOWN_OBJECT" in html and "HISTORY_ONLY" in html
    assert "Doğrulanamadı" in html
    assert "tarihsel" in html.lower()


@pytest.mark.parametrize("count", [0, 1, 40, 41, 100, 120, 1000])
def test_scale_profiles_keep_totals_and_only_live_detail_links(count: int) -> None:
    original = _view("03-sequence-only.view.json")
    view = deepcopy(original)
    view["objects"] = []
    view["evidence_registry"] = []
    view["changed_files"] = count
    for index in range(count):
        tag = f"{index:04d}"

        def unique(item: dict) -> dict:
            return json.loads(json.dumps(item).replace("obj-004", f"obj-{tag}").replace("ev-004", f"ev-{tag}").replace("fact-004", f"fact-{tag}"))

        obj = unique(original["objects"][0])
        obj["identity"]["name"] = f"SEQ_{tag}"
        view["objects"].append(obj)
        view["evidence_registry"].extend(unique(item) for item in original["evidence_registry"])
    rendered = _render(view)
    manifest = build_render_manifest(view, rendered, source_report_sha256=None)
    html = rendered.html.decode("utf-8")
    assert len(rendered.html) <= 90_000 and len(rendered.mime) <= 150_000
    assert f"{count} nesne kaynak farkı" in html
    assert manifest["omitted_index_objects"] == count - len(rendered.index_object_ids)
    assert manifest["omitted_detail_objects"] == count - len(rendered.detail_object_ids)
    assert b"href=" not in rendered.html
    if count >= 120:
        assert rendered.profile != "full"
        assert "kısaltıldı" in html
    if count == 1000:
        minimal = _render(view, html_limit=18_000)
        assert minimal.profile == "minimal"
        assert len(minimal.html) <= 18_000
        assert b"1000 nesne kaynak fark" in minimal.html
        assert not minimal.index_object_ids and not minimal.detail_object_ids
        assert b"href=" not in minimal.html
