from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise SystemExit("unsupported release manifest")
    for relative, expected in manifest["files"].items():
        path = (ROOT / relative).resolve(strict=True)
        if ROOT.resolve() not in path.parents:
            raise SystemExit("manifest path escapes repository")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"release digest mismatch: {relative}")
    print(json.dumps({"ok": True, "files": len(manifest["files"])}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
