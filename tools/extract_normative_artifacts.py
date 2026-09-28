#!/usr/bin/env python3
"""Mechanically extract the approved fenced artifacts from AKTIF_GOREV.md.

This is intentionally narrow: it never executes fenced content and refuses
missing/duplicate sections. It keeps the checked-in contracts byte-equivalent
to the approved task document while that local evidence file is available.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "AKTIF_GOREV.md"

ARTIFACTS = {
    "### A1. `schemas/unit-response.schema.json`": ("json", ROOT / "schemas" / "unit-response.schema.json"),
    "### A2. `schemas/report.schema.json`": ("json", ROOT / "schemas" / "report.schema.json"),
    "## Ek B — Secretsız normatif yapılandırma": ("toml", ROOT / "config" / "gpu.example.toml"),
    "## Ek C — Yalnız yeni job için Jenkinsfile sözleşmesi": ("groovy", ROOT / "Jenkinsfile"),
}


def extract(text: str, heading: str, language: str) -> str:
    start = text.find(heading)
    if start < 0:
        raise RuntimeError(f"missing heading: {heading}")
    next_heading = re.search(r"^##(?:#)? ", text[start + len(heading) :], flags=re.MULTILINE)
    end = len(text) if next_heading is None else start + len(heading) + next_heading.start()
    section = text[start:end]
    matches = re.findall(rf"```{re.escape(language)}\r?\n(.*?)\r?\n```", section, flags=re.DOTALL)
    if len(matches) != 1:
        raise RuntimeError(f"expected one {language} block under {heading}, got {len(matches)}")
    return matches[0].replace("\r\n", "\n") + "\n"


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8")
    for heading, (language, destination) in ARTIFACTS.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(extract(text, heading, language), encoding="utf-8", newline="\n")
        print(destination.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
