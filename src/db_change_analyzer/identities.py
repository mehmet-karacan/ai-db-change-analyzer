from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


ASCII_UNQUOTED = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")


@dataclass(frozen=True, slots=True)
class CanonicalIdentifier:
    raw: str
    canonical: str
    quoted: bool
    confidence: str


def canonical_identifier(raw: str, *, quoted: bool) -> CanonicalIdentifier:
    if quoted:
        return CanonicalIdentifier(raw=raw, canonical=raw, quoted=True, confidence="known")
    if ASCII_UNQUOTED.fullmatch(raw):
        return CanonicalIdentifier(raw=raw, canonical=raw.upper(), quoted=False, confidence="known")
    return CanonicalIdentifier(raw=raw, canonical=raw, quoted=False, confidence="uncertain")


def object_key(namespace: str, schema: CanonicalIdentifier | None, object_type: str, name: CanonicalIdentifier, subkey: str | None = None) -> str:
    parts = [namespace, schema.canonical if schema else "", object_type.upper(), name.canonical]
    if subkey:
        parts.append(subkey)
    return "|".join(parts)


def stable_id(prefix: str, payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
