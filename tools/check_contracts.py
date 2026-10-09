from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from db_change_analyzer.config import load_config  # noqa: E402
from db_change_analyzer.models import UnitResponse  # noqa: E402


def main() -> int:
    schema_names = ("unit-response.schema.json", "report.schema.json", "result.schema.json", "delivery.schema.json", "corpus-manifest.schema.json", "source-review.schema.json", "source-review-input.schema.json", "review-eval-comparison.schema.json")
    for name in schema_names:
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        package_schema = ROOT / "src" / "db_change_analyzer" / "schemas" / name
        if package_schema.is_file() and json.loads(package_schema.read_text(encoding="utf-8")) != schema:
            raise SystemExit(f"schema mirror drift: {name}")
    normative = json.loads((ROOT / "schemas" / "unit-response.schema.json").read_text(encoding="utf-8"))
    generated = UnitResponse.model_json_schema()
    if set(normative["required"]) != set(generated["required"]):
        raise SystemExit("UnitResponse required-field contract drift")
    if set(normative["properties"]) != set(generated["properties"]):
        raise SystemExit("UnitResponse property contract drift")
    load_config(ROOT / "config" / "gpu.example.toml")
    print(json.dumps({"ok": True, "schemas": len(schema_names), "config_example": True}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
