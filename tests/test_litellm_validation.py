from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from db_change_analyzer.analysis import analyze_with_single_repair
from db_change_analyzer.config import load_config
from db_change_analyzer.litellm_http import LiteLLMClient, ModelTransportError
from db_change_analyzer.models import AnalysisUnit, FindingKind, SourcePair
from db_change_analyzer.validation import ResponseValidationError, parse_json_object, validate_unit_response


ROOT = Path(__file__).resolve().parents[1]


def unit() -> AnalysisUnit:
    return AnalysisUnit(
        unit_id="u1",
        object_identity=None,
        artifact_paths=["gpu_user/a.sql"],
        related_object_keys=[],
        view_tags=["net"],
        source_pair=SourcePair(old_revision="1" * 40, new_revision="2" * 40),
        deterministic_facts=[],
        evidence_registry=[{"kind": "source", "evidence_id": "ev1", "snippet": "CREATE TABLE t(id NUMBER)"}],
        dependency_edges=[],
        coverage_manifest={"context_coverage": "full"},
        allowed_claim_kinds=[FindingKind.INTERPRETATION, FindingKind.RECOMMENDED_CHECK],
    )


def valid_content() -> str:
    return json.dumps({
        "schema_version": "1.0",
        "unit_id": "u1",
        "summary_tr": "Kaynakta tablo tanimi goruldu.",
        "summary_evidence_ids": ["ev1"],
        "findings": [],
        "limitations": [],
    })


def verified_model_config(tmp_path: Path, *, output_mode: str = "json_schema"):
    raw = (ROOT / "config" / "gpu.example.toml").read_text(encoding="utf-8")
    raw = raw.replace("route_verified = false", "route_verified = true")
    raw = raw.replace("capabilities_verified = false", "capabilities_verified = true")
    raw = raw.replace('capability_record = ""', 'capability_record = "reviewed/test.json"')
    raw = raw.replace("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768")
    raw = raw.replace('output_mode = "json_schema"', f'output_mode = "{output_mode}"')
    path = tmp_path / "config.toml"
    path.write_text(raw, encoding="utf-8")
    return load_config(path).model


def test_strict_json_rejects_duplicates_nonfinite_and_trailing_text() -> None:
    with pytest.raises(ResponseValidationError, match="DUPLICATE_JSON_KEY"):
        parse_json_object('{"a":1,"a":2}')
    with pytest.raises(ResponseValidationError, match="NONFINITE_NUMBER"):
        parse_json_object('{"a":NaN}')
    with pytest.raises(ResponseValidationError, match="INVALID_JSON"):
        parse_json_object('{"a":1} trailing')


def test_validation_rejects_unknown_evidence_and_secret_without_echoing_content() -> None:
    forged = json.loads(valid_content())
    forged["summary_evidence_ids"] = ["invented"]
    with pytest.raises(ResponseValidationError, match="UNKNOWN_EVIDENCE_ID"):
        validate_unit_response(json.dumps(forged), unit())
    leaked_payload = json.loads(valid_content())
    leaked_payload["summary_tr"] = "api_key=super-secret-value"
    with pytest.raises(ResponseValidationError, match="SECRET_IN_RESPONSE") as caught:
        validate_unit_response(json.dumps(leaked_payload), unit())
    assert "super-secret-value" not in str(caught.value)


def test_prompt_json_allows_exactly_one_fenced_object() -> None:
    assert validate_unit_response(f"```json\n{valid_content()}\n```", unit(), prompt_json=True).response.unit_id == "u1"
    with pytest.raises(ResponseValidationError):
        validate_unit_response(f"text\n```json\n{valid_content()}\n```", unit(), prompt_json=True)


def test_strict_json_accepts_valid_finding_enums() -> None:
    response = json.loads(valid_content())
    response["findings"] = [{
        "kind": "interpretation",
        "claim": "Tablo tanimi kaynakta goruldu.",
        "risk": "unknown",
        "evidence_level": "source_backed_inference",
        "evidence_ids": ["ev1"],
    }]
    validated = validate_unit_response(json.dumps(response), unit())
    assert validated.response.findings[0].kind is FindingKind.INTERPRETATION


def test_single_repair_is_bounded_and_evidence_validated() -> None:
    calls: list[tuple[str, ...]] = []
    outcome = analyze_with_single_repair(unit(), lambda: "not json", lambda codes: calls.append(codes) or valid_content())
    assert outcome.attempts == 2 and outcome.repaired
    assert calls == [("INVALID_JSON",)]
    with pytest.raises(ResponseValidationError):
        analyze_with_single_repair(unit(), lambda: "not json", lambda _: "still bad")


def test_http_request_shape_and_envelope_are_strict(tmp_path: Path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer local-test-key"
        return httpx.Response(200, json={"model": "deployment-label", "choices": [{"message": {"content": valid_content()}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3}})

    config = verified_model_config(tmp_path)
    with LiteLLMClient(config, transport=httpx.MockTransport(handler)) as client:
        reply = client.complete(api_key="local-test-key", system_message="safe", user_payload=unit().model_dump(mode="json"), response_schema={"type": "object"}, output_tokens=64)
    assert reply.finish_reason == "stop"
    assert captured["model"] == "Kimi-K2.7-Code"
    assert "tools" not in captured and "temperature" not in captured
    assert captured["response_format"]["type"] == "json_schema"


def test_json_object_mode_includes_schema_in_prompt(tmp_path: Path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": valid_content()}, "finish_reason": "stop"}]})

    config = verified_model_config(tmp_path, output_mode="json_object")
    schema = {"type": "object", "required": ["unit_id"]}
    with LiteLLMClient(config, transport=httpx.MockTransport(handler)) as client:
        client.complete(api_key="local-test-key", system_message="safe", user_payload=unit().model_dump(mode="json"), response_schema=schema, output_tokens=64)
    assert captured["response_format"] == {"type": "json_object"}
    assert '"required":["unit_id"]' in captured["messages"][0]["content"]


def test_http_never_retries_or_downgrades_generic_400(tmp_path: Path) -> None:
    config = verified_model_config(tmp_path)
    called = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(400, json={"error": "unsupported parameter"})

    with LiteLLMClient(config, transport=httpx.MockTransport(handler)) as client, pytest.raises(ModelTransportError) as caught:
        client.complete(api_key="x" * 8, system_message="safe", user_payload={}, response_schema={}, output_tokens=1)
    assert caught.value.code == "MODEL_REQUEST_REJECTED"
    assert called == 1


def test_unverified_route_blocks_before_network(tmp_path: Path) -> None:
    config = load_config(ROOT / "config" / "gpu.example.toml").model
    with LiteLLMClient(config, transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client, pytest.raises(ModelTransportError) as caught:
        client.complete(api_key="x" * 8, system_message="safe", user_payload={}, response_schema={}, output_tokens=1)
    assert caught.value.code == "MODEL_CAPABILITY_UNVERIFIED"


def test_unverified_synthetic_probe_uses_fixed_safe_payload() -> None:
    config = load_config(ROOT / "config" / "gpu.example.toml").model
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": valid_content()}, "finish_reason": "stop"}]})

    with LiteLLMClient(config, transport=httpx.MockTransport(handler)) as client:
        client.probe_synthetic(api_key="local-test-key", response_schema={"type": "object"}, output_tokens=4096)
    sent = json.loads(captured["messages"][1]["content"])
    assert sent["coverage_manifest"] == {"synthetic": True}
    assert sent["artifact_paths"] == ["synthetic/smoke.sql"]
    assert captured["max_tokens"] == 4096
