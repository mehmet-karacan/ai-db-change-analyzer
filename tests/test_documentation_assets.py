from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_JSON = ROOT / "docs" / "architecture.json"
ARCHITECTURE_EXCALIDRAW = ROOT / "docs" / "architecture.excalidraw"
ARCHITECTURE_PNG = ROOT / "docs" / "architecture.png"


def test_architecture_assets_are_present_and_unicode_safe() -> None:
    assert ARCHITECTURE_JSON.is_file()
    assert ARCHITECTURE_EXCALIDRAW.is_file()
    assert ARCHITECTURE_PNG.is_file()
    assert ARCHITECTURE_PNG.stat().st_size > 0

    json_text = ARCHITECTURE_JSON.read_text(encoding="utf-8")
    excalidraw_text = ARCHITECTURE_EXCALIDRAW.read_text(encoding="utf-8")
    assert "�" not in json_text
    assert "�" not in excalidraw_text
    assert json.loads(json_text) == json.loads(excalidraw_text)

    scene = json.loads(json_text)
    text_elements = [element for element in scene["elements"] if element.get("type") == "text"]
    assert text_elements
    assert any(any(character in element.get("text", "") for character in "İĞÜŞÖÇığüşöç") for element in text_elements)
    assert all(element.get("fontFamily") != 1 for element in text_elements)
    assert all(element.get("originalText") == element.get("text") for element in text_elements)
