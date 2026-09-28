#!/usr/bin/env python3
"""Verify a prepared corpus without executing any member content."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def verify(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    manifest = json.loads((root / "corpus-manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for source in manifest.get("sources", []):
        prefix = (root / source["output_prefix"]).resolve()
        for item in source.get("files", []):
            candidate = (prefix / Path(*item["path"].split("/"))).resolve()
            if prefix not in candidate.parents:
                failures.append(f"escape:{item['path']}")
                continue
            if not candidate.is_file():
                failures.append(f"missing:{item['path']}")
                continue
            data = candidate.read_bytes()
            if len(data) != item["bytes"]:
                failures.append(f"size:{item['path']}")
            if hashlib.sha256(data).hexdigest() != item["raw_sha256"]:
                failures.append(f"hash:{item['path']}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        failures = verify(args.root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": not failures, "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
