#!/usr/bin/env python3
"""Reproducibly generate the vendored PL/SQL Python parser."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "antlr-plsql"
OUTPUT = ROOT / "src" / "db_change_analyzer" / "oracle" / "generated"
GRAMMAR_COMMIT = "b434a051c56dcc2a5de0a0f2d86575ed192b59da"
GENERATOR_SHA256 = "eae2dfa119a64327444672aff63e9ec35a20180dc5b8090b7a6ab85125df4d76"
SOURCE_HASHES = {
    "PlSqlLexer.g4": "f751a794d250d53ebca7c215265ebba8970ca0ebdddf1e78064cb8d572ecbe79",
    "PlSqlParser.g4": "0adcbaa477c010ec4e6ae16f66bee23f2d6c6ebde7a0e63a218a4541f4dd350b",
    "Python3/PlSqlLexerBase.py": "da147fcf86a3921ef4d3017357e99f09b60e308dab8db564bfeba39084234e84",
    "Python3/PlSqlParserBase.py": "d412b4da331fc4b64ee874e8da9ed9490a471213beab8a343dbb45b42286a74c",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_grammar(text: str) -> str:
    count = text.count("{this.")
    if count == 0:
        return text
    patched = text.replace("{this.", "{self.")
    if patched.count("{this.") != 0 or patched.count("{self.") < count:
        raise RuntimeError("targeted grammar action patch failed")
    return patched


def _patch_generated_imports(text: str) -> str:
    replacements = {
        "from PlSqlLexerBase import PlSqlLexerBase": "from .PlSqlLexerBase import PlSqlLexerBase",
        "from PlSqlParserBase import PlSqlParserBase": "from .PlSqlParserBase import PlSqlParserBase",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _patch_parser_base(text: str) -> str:
    marker = "from PlSqlLexer import PlSqlLexer as _Lexer"
    if text.count(marker) != 2:
        raise RuntimeError("unexpected parser base import layout")
    return text.replace(marker, "from .PlSqlLexer import PlSqlLexer as _Lexer")


def generate(jar: Path) -> dict[str, object]:
    jar = jar.resolve(strict=True)
    if digest(jar) != GENERATOR_SHA256:
        raise RuntimeError("ANTLR generator SHA-256 mismatch")
    for relative, expected in SOURCE_HASHES.items():
        if digest(VENDOR / relative) != expected:
            raise RuntimeError(f"vendored source digest mismatch: {relative}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dbca-antlr-") as directory:
        work = Path(directory)
        patches: list[str] = []
        for name in ("PlSqlLexer.g4", "PlSqlParser.g4"):
            original = (VENDOR / name).read_text(encoding="utf-8")
            patched = _patch_grammar(original)
            (work / name).write_text(patched, encoding="utf-8", newline="\n")
            patches.extend(difflib.unified_diff(original.splitlines(True), patched.splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}"))
        completed = subprocess.run(
            ["java", "-jar", str(jar), "-Dlanguage=Python3", "-visitor", "-no-listener", "PlSqlLexer.g4", "PlSqlParser.g4"],
            cwd=work,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError("ANTLR generation failed")
        for name in ("PlSqlLexer.py", "PlSqlParser.py", "PlSqlParserVisitor.py"):
            text = _patch_generated_imports((work / name).read_text(encoding="utf-8"))
            (OUTPUT / name).write_text(text, encoding="utf-8", newline="\n")
        lexer_base = (VENDOR / "Python3" / "PlSqlLexerBase.py").read_text(encoding="utf-8")
        parser_base = (VENDOR / "Python3" / "PlSqlParserBase.py").read_text(encoding="utf-8")
        (OUTPUT / "PlSqlLexerBase.py").write_text(lexer_base, encoding="utf-8", newline="\n")
        (OUTPUT / "PlSqlParserBase.py").write_text(_patch_parser_base(parser_base), encoding="utf-8", newline="\n")
        (VENDOR / "python-target.patch").write_text("".join(patches), encoding="utf-8", newline="\n")
    generated = {
        path.name: digest(path)
        for path in sorted(OUTPUT.glob("PlSql*.py"))
    }
    manifest = {
        "schema_version": "1.0",
        "grammar_repository": "https://github.com/antlr/grammars-v4",
        "grammar_commit": GRAMMAR_COMMIT,
        "generator_version": "4.13.2",
        "generator_sha256": GENERATOR_SHA256,
        "sources": SOURCE_HASHES,
        "generated": generated,
        "target_patch_sha256": digest(VENDOR / "python-target.patch"),
    }
    (VENDOR / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate(args.jar)
    print(json.dumps({"ok": True, "generated": manifest["generated"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
