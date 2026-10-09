from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare_runtime_config.py SOURCE TARGET")
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    replacements = (
        ("route_verified = false", "route_verified = true"),
        ("capabilities_verified = false", "capabilities_verified = true"),
        ('capability_record = ""', 'capability_record = ".analyzer-state/capability-record.json"'),
        ("verified_context_window_tokens = 0", "verified_context_window_tokens = 32768"),
    )
    for old, new in replacements:
        source = source.replace(old, new)
    Path(sys.argv[2]).write_text(source, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
