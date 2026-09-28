from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|token)\b\s*[=:]\s*['\"]?[^\s,'\"]{6,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bCREATE\s+(?:USER|DATABASE\s+LINK)\b.*?\bIDENTIFIED\s+BY\b", re.DOTALL),
)


@dataclass(frozen=True, slots=True)
class SecretScan:
    blocked: bool
    pattern_index: int | None


def scan_secret(text: str, extra_patterns: list[str] | None = None) -> SecretScan:
    patterns = [*DEFAULT_SECRET_PATTERNS, *(re.compile(item) for item in (extra_patterns or []))]
    for index, pattern in enumerate(patterns):
        if pattern.search(text):
            return SecretScan(True, index)
    return SecretScan(False, None)


def contained_path(root: Path, candidate: Path) -> Path:
    root = root.resolve(strict=True)
    candidate = candidate.resolve(strict=False)
    if candidate == root or root in candidate.parents:
        return candidate
    raise ValueError("path escapes owned root")


def approved_https_url(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in allowed_hosts and not parsed.username and not parsed.password
