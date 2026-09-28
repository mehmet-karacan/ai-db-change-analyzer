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
    for name in ("unit-response.schema.json", "report.schema.json", "result.schema.json", "delivery.schema.json", "corpus-manifest.schema.json"):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    normative = json.loads((ROOT / "schemas" / "unit-response.schema.json").read_text(encoding="utf-8"))
    generated = UnitResponse.model_json_schema()
    if set(normative["required"]) != set(generated["required"]):
        raise SystemExit("UnitResponse required-field contract drift")
    if set(normative["properties"]) != set(generated["properties"]):
        raise SystemExit("UnitResponse property contract drift")
    load_config(ROOT / "config" / "gpu.example.toml")
    print(json.dumps({"ok": True, "schemas": 5, "config_example": True}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
