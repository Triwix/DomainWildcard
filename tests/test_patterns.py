import pytest

from app.patterns import (
    PatternValidationError,
    estimate_total_candidates,
    expand_pattern,
    iter_expanded_pattern,
    validate_pattern,
)


def test_validate_pattern_requires_between_one_and_four_wildcards():
    assert validate_pattern("*example.com") == "*example.com"
    assert validate_pattern("*-*.example.com") == "*-*.example.com"
    assert validate_pattern("*-*-*.example.com") == "*-*-*.example.com"
    assert validate_pattern("*-*-*-*.example.com") == "*-*-*-*.example.com"

    with pytest.raises(PatternValidationError):
        validate_pattern("example.com")

    with pytest.raises(PatternValidationError):
        validate_pattern("*-*-*-*-*.example.com")


def test_expand_pattern_replaces_wildcard():
    pattern = "*example.com"
    words = ["alpha", "beta"]
    assert expand_pattern(pattern, words) == ["alphaexample.com", "betaexample.com"]


def test_expand_pattern_two_wildcards_with_secondary_words():
    pattern = "*-*.example.com"
    first = ["alpha", "beta"]
    second = ["one", "two"]
    assert expand_pattern(pattern, first, secondary_words=second) == [
        "alpha-one.example.com",
        "alpha-two.example.com",
        "beta-one.example.com",
        "beta-two.example.com",
    ]


def test_expand_pattern_two_wildcards_without_secondary_uses_primary_twice():
    pattern = "*-*.example.com"
    words = ["x", "y"]
    assert expand_pattern(pattern, words) == [
        "x-x.example.com",
        "x-y.example.com",
        "y-x.example.com",
        "y-y.example.com",
    ]


def test_expand_pattern_three_wildcards_uses_secondary_for_positions_two_and_three():
    pattern = "*-*-*.example.com"
    primary = ["a", "b"]
    secondary = ["x", "y"]
    assert expand_pattern(pattern, primary, secondary_words=secondary) == [
        "a-x-x.example.com",
        "a-x-y.example.com",
        "a-y-x.example.com",
        "a-y-y.example.com",
        "b-x-x.example.com",
        "b-x-y.example.com",
        "b-y-x.example.com",
        "b-y-y.example.com",
    ]


def test_expand_pattern_four_wildcards_without_secondary_reuses_primary_for_all_positions():
    pattern = "*-*-*-*.example.com"
    words = ["p", "q"]
    expanded = expand_pattern(pattern, words)
    assert len(expanded) == 16
    assert expanded[0] == "p-p-p-p.example.com"
    assert expanded[-1] == "q-q-q-q.example.com"


def test_estimate_total_candidates_handles_one_to_four_wildcards():
    assert estimate_total_candidates("*example.com", 5) == 5
    assert estimate_total_candidates("*-*.example.com", 5) == 25
    assert estimate_total_candidates("*-*.example.com", 5, 3) == 15
    assert estimate_total_candidates("*-*-*.example.com", 5, 3) == 45
    assert estimate_total_candidates("*-*-*-*.example.com", 2, 4) == 128


def test_iter_expanded_pattern_matches_expand_pattern():
    pattern = "*-*.example.com"
    first = ["a", "b"]
    second = ["1", "2"]
    assert list(iter_expanded_pattern(pattern, first, secondary_words=second)) == expand_pattern(
        pattern,
        first,
        secondary_words=second,
    )
