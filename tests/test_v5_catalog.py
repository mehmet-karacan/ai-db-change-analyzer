from __future__ import annotations

import json
from pathlib import Path

from db_change_analyzer.taxonomy.catalog import change_catalog, supported_types
import db_change_analyzer


def test_approved_catalog_matches_the_mail_contract_and_supported_types() -> None:
    catalog = change_catalog()
    schema = json.loads((Path(__file__).parents[1] / "schemas/v5/mail-view-model.schema.json").read_text(encoding="utf-8"))
    fact_ids = set(schema["$defs"]["fact"]["properties"]["taxonomy_id"]["enum"])
    assert set(catalog) == fact_ids
    assert len(catalog) == 143
    assert supported_types() == {"TABLE", "INDEX", "SEQUENCE", "VIEW", "PACKAGE_SPEC", "PACKAGE_BODY", "TRIGGER", "PROCEDURE", "FUNCTION", "TYPE", "TYPE_BODY"}
    assert all(family.object_types <= supported_types() for family in catalog.values())


def test_packaged_contracts_match_repository_contracts() -> None:
    root = Path(__file__).parents[1] / "schemas" / "v5"
    installed = Path(db_change_analyzer.__file__).parent / "schemas" / "v5"
    assert {path.name for path in root.glob("*.json")} == {path.name for path in installed.glob("*.json")}
    assert all(path.read_bytes() == (installed / path.name).read_bytes() for path in root.glob("*.json"))
    assert (Path(__file__).parents[1] / "schemas" / "report.schema.json").read_bytes() == (
        Path(db_change_analyzer.__file__).parent / "schemas" / "report.schema.json"
    ).read_bytes()
