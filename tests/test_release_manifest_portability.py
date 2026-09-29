"""Release hashes must survive a Windows checkout with core.autocrlf=true."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_text_files_have_pinned_line_endings() -> None:
    paths = set(json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))["files"])
    pinned = {}
    for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        if line.startswith("/") and " text eol=" in line:
            path, ending = line.removeprefix("/").split(" text eol=", 1)
            pinned[path] = ending
    assert paths == set(pinned)
    assert set(pinned.values()) <= {"lf", "crlf"}
    for path, ending in pinned.items():
        raw = (ROOT / path).read_bytes()
        if ending == "lf":
            assert b"\r\n" not in raw, path
        else:
            assert b"\n" not in raw.replace(b"\r\n", b""), path
