from __future__ import annotations

from itertools import product
from typing import Iterable, Iterator, List, Optional


class PatternValidationError(ValueError):
    pass


MAX_PATTERN_WILDCARDS = 4


def validate_pattern(pattern: str) -> str:
    normalized = pattern.strip().lower()
    if not normalized:
        raise PatternValidationError("Pattern is required.")
    wildcard_count = normalized.count("*")
    if wildcard_count < 1 or wildcard_count > MAX_PATTERN_WILDCARDS:
        raise PatternValidationError(
            f"Pattern must contain between 1 and {MAX_PATTERN_WILDCARDS} '*' wildcards."
        )
    if " " in normalized:
        raise PatternValidationError("Pattern cannot contain spaces.")
    if "/" in normalized:
        raise PatternValidationError("Pattern must be a domain format without slashes.")
    return normalized


def estimate_total_candidates(pattern: str, primary_count: int, secondary_count: Optional[int] = None) -> int:
    wildcard_count = pattern.count("*")
    first = max(0, int(primary_count))
    if wildcard_count < 1 or wildcard_count > MAX_PATTERN_WILDCARDS:
        return 0
    if wildcard_count == 1:
        return first
    other = max(0, int(secondary_count if secondary_count is not None else primary_count))
    return first * (other ** (wildcard_count - 1))


def iter_expanded_pattern(pattern: str, words: Iterable[str], secondary_words: Iterable[str] | None = None) -> Iterator[str]:
    first_words = list(words)
    wildcard_count = pattern.count("*")
    if wildcard_count < 1 or wildcard_count > MAX_PATTERN_WILDCARDS:
        return

    segments = pattern.split("*")
    if wildcard_count == 1:
        prefix, suffix = segments[0], segments[1]
        for first in first_words:
            yield f"{prefix}{first}{suffix}"
        return

    other_words = list(secondary_words) if secondary_words is not None else first_words
    for first in first_words:
        for rest in product(other_words, repeat=wildcard_count - 1):
            pieces: List[str] = [segments[0], first]
            for idx, token in enumerate(rest, start=1):
                pieces.append(segments[idx])
                pieces.append(token)
            pieces.append(segments[wildcard_count])
            yield "".join(pieces)


def expand_pattern(pattern: str, words: Iterable[str], secondary_words: Iterable[str] | None = None) -> List[str]:
    return list(iter_expanded_pattern(pattern, words, secondary_words=secondary_words))
