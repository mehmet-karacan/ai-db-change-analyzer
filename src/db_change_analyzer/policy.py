from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class PolicyLoadError(RuntimeError):
    """Raised when a versioned runtime policy cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    name: str
    path: str
    version: str
    sha256: str
    text: str


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    analysis: PolicyDocument
    language: PolicyDocument

    @property
    def fingerprint(self) -> str:
        payload = "\n".join(
            f"{item.name}:{item.version}:{item.sha256}"
            for item in (self.analysis, self.language)
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def metadata(self) -> dict[str, dict[str, str]]:
        return {
            item.name: {"version": item.version, "sha256": item.sha256}
            for item in (self.analysis, self.language)
        }


def _version(text: str, path: Path) -> str:
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if not first.startswith("<!-- policy-version:") or not first.endswith(" -->"):
        raise PolicyLoadError(f"POLICY_VERSION_MISSING:{path.name}")
    value = first[len("<!-- policy-version:"):-len(" -->")].strip()
    if not value or any(ord(char) < 32 for char in value):
        raise PolicyLoadError(f"POLICY_VERSION_INVALID:{path.name}")
    return value


def _load_document(root: Path, name: str) -> PolicyDocument:
    path = root / "prompts" / name
    try:
        if path.is_symlink() or not path.is_file():
            raise PolicyLoadError(f"POLICY_MISSING:{name}")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except PolicyLoadError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PolicyLoadError(f"POLICY_UNREADABLE:{name}") from exc
    if "\x00" in text:
        raise PolicyLoadError(f"POLICY_NUL:{name}")
    return PolicyDocument(name, str(path), _version(text, path), hashlib.sha256(raw).hexdigest(), text)


def load_policy_bundle(root: str | Path | None = None) -> PolicyBundle:
    package_root = Path(root) if root is not None else Path(__file__).parent
    analysis = _load_document(package_root, "analysis_policy.tr.md")
    language = _load_document(package_root, "report_language.tr.md")
    return PolicyBundle(analysis, language)

