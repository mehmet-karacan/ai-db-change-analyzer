from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class ChangeFamily:
    taxonomy_id: str
    object_types: frozenset[str]
    component: str
    property_key: str
    label_tr: str
    category: str
    allowed_actions: frozenset[str]


@lru_cache(maxsize=1)
def change_catalog() -> dict[str, ChangeFamily]:
    raw = json.loads(files("db_change_analyzer.taxonomy").joinpath("change-catalog.json").read_text(encoding="utf-8"))
    if raw["schema_version"] != "oracle-change-catalog/1.0":
        raise ValueError("Unsupported Oracle taxonomy version")
    families: dict[str, ChangeFamily] = {}
    for item in raw["entries"]:
        key = item["id"]
        if key in families:
            raise ValueError(f"Duplicate Oracle taxonomy id: {key}")
        families[key] = ChangeFamily(
            taxonomy_id=key,
            object_types=frozenset(item["object_types"]),
            component=item["component"],
            property_key=item["property_key"],
            label_tr=item["label_tr"],
            category=item["category"],
            allowed_actions=frozenset(item["allowed_change_actions"]),
        )
    return families


@lru_cache(maxsize=1)
def supported_types() -> frozenset[str]:
    raw = json.loads(files("db_change_analyzer.taxonomy").joinpath("object-catalog.json").read_text(encoding="utf-8"))
    if raw["schema_version"] != "oracle-taxonomy/1.0":
        raise ValueError("Unsupported Oracle taxonomy version")
    return frozenset(item["id"] for item in raw["types"])
