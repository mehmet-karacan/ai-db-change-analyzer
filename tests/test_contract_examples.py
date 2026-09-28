from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from db_change_analyzer.config import AppConfig, load_config
from db_change_analyzer.identities import canonical_identifier
from db_change_analyzer.models import UnitResponse


ROOT = Path(__file__).resolve().parents[1]


def test_v01_all_json_schemas_are_valid() -> None:
    for path in sorted((ROOT / "schemas").glob("*.schema.json")):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_config_example_is_strict_and_has_safe_model_defaults() -> None:
    config = load_config(ROOT / "config" / "gpu.example.toml")
    assert isinstance(config, AppConfig)
    assert not config.model.route_verified
    assert not config.model.capabilities_verified
    assert config.model.verified_context_window_tokens == 0
    assert config.repository.branch == "master"


def test_unknown_config_field_is_rejected() -> None:
    config = load_config(ROOT / "config" / "gpu.example.toml")
    data = config.model_dump()
    data["execute_sql"] = True
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data, strict=True)


def test_v08_unit_response_rejects_executable_extra_field() -> None:
    value = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "summary_tr": "Kaynakta değişiklik görüldü.",
        "summary_evidence_ids": ["src-1"],
        "findings": [],
        "limitations": [],
        "execute_sql": "drop table t",
    }
    with pytest.raises(ValidationError):
        UnitResponse.model_validate(value, strict=True)


def test_o07_ascii_and_quoted_identity_rules() -> None:
    assert canonical_identifier("foo", quoted=False).canonical == "FOO"
    assert canonical_identifier("FOO", quoted=False).canonical == "FOO"
    assert canonical_identifier("FOO", quoted=True).canonical == "FOO"
    assert canonical_identifier("Foo", quoted=True).canonical == "Foo"


def test_o08_non_ascii_unquoted_identity_is_uncertain() -> None:
    value = canonical_identifier("ß", quoted=False)
    assert value.canonical == "ß"
    assert value.confidence == "uncertain"
