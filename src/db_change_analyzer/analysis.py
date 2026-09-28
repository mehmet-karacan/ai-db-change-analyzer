from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import AnalysisUnit, UnitResponse
from .validation import ResponseValidationError, validate_unit_response


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    response: UnitResponse
    attempts: int
    repaired: bool


def analyze_with_single_repair(
    unit: AnalysisUnit,
    primary: Callable[[], str],
    repair: Callable[[tuple[str, ...]], str],
    *,
    prompt_json: bool = False,
    repair_already_used: bool = False,
    extra_secret_patterns: list[str] | None = None,
) -> AnalysisOutcome:
    content = primary()
    try:
        result = validate_unit_response(content, unit, prompt_json=prompt_json, extra_secret_patterns=extra_secret_patterns)
        return AnalysisOutcome(result.response, 1, False)
    except ResponseValidationError as first_error:
        if repair_already_used:
            raise
        repaired = repair(first_error.codes)
        result = validate_unit_response(repaired, unit, prompt_json=prompt_json, extra_secret_patterns=extra_secret_patterns)
        return AnalysisOutcome(result.response, 2, True)
