from pathlib import Path

import pytest

from db_change_analyzer.config import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]


def test_artifact_example_does_not_require_smtp_configuration() -> None:
    config = load_config(ROOT / "config" / "gpu.artifact.example.toml")

    assert config.schema_version == 2
    assert config.delivery.mode == "jenkins_artifact"
    assert config.smtp is None


def test_legacy_delivery_requires_smtp_configuration(tmp_path: Path) -> None:
    source = (ROOT / "config" / "gpu.artifact.example.toml").read_text(encoding="utf-8")
    source = source.replace('schema_version = 2', 'schema_version = 1', 1)
    source = source.replace('[delivery]\nmode = "jenkins_artifact"\nresult_path = "./out/result.json"\n\n', "")
    config_path = tmp_path / "config.toml"
    config_path.write_text(source, encoding="utf-8")

    with pytest.raises(ConfigError, match="configuration validation failed"):
        load_config(config_path)
