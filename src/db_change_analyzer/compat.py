"""Small standard-library compatibility shims for the supported Python range."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum


try:
    from datetime import UTC
except ImportError:  # pragma: no cover - exercised on Python 3.10
    UTC = timezone.utc


try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised on Python 3.10
    class StrEnum(str, Enum):
        """Python 3.11-compatible StrEnum fallback."""

        def __str__(self) -> str:
            return self.value


__all__ = ["UTC", "StrEnum", "datetime"]
