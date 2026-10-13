"""Exact arithmetic for results. Expected values are worked out by hand."""

from fractions import Fraction

import pytest

from mdeis.core import numbers


@pytest.mark.parametrize(
    "value, text",
    [
        (Fraction(27, 2), "13.50"),
        (Fraction(1, 8), "0.13"),  # 0.125 rounds half up
        (Fraction(-1, 8), "-0.13"),  # and away from zero
        (Fraction(-2), "-2.00"),
        (Fraction(0), "0.00"),
        (Fraction(1, 3), "0.33"),
        (Fraction(2, 3), "0.67"),
    ],
)
def test_two_places_rounds_halves_up(value, text):
    assert numbers.two_places(value) == text


def test_exact_reads_a_decimal_without_float_error():
    assert numbers.exact(2.5) == Fraction(5, 2)
    assert numbers.exact("0.1") + numbers.exact("0.2") == numbers.exact("0.3")


def test_a_weighted_average_of_a_window():
    # weights 1, 2, 2 on scores 10, 13, 14: (10 + 26 + 28) / 5 = 64/5 = 12.8
    pairs = [(Fraction(1), Fraction(10)), (Fraction(2), Fraction(13)), (Fraction(2), Fraction(14))]
    assert numbers.two_places(numbers.weighted_average(pairs)) == "12.80"


@pytest.mark.parametrize(
    "value, low, high, gap",
    [(9, 12, 16, 3), (12, 12, 16, 0), (14, 12, 16, 0), (16, 12, 16, 0), (17, 12, 16, 1)],
)
def test_the_gap_is_the_distance_to_the_nearest_edge(value, low, high, gap):
    got = numbers.distance_to_range(Fraction(value), Fraction(low), Fraction(high))
    assert got == Fraction(gap)


def test_level_names_describe_the_result_against_the_range():
    low, high = Fraction(12), Fraction(16)
    assert numbers.level_name(Fraction(11), low, high) == "below_expected_range"
    assert numbers.level_name(Fraction(12), low, high) == "within_expected_range"
    assert numbers.level_name(Fraction(16), low, high) == "within_expected_range"
    assert numbers.level_name(Fraction(17), low, high) == "above_expected_range"


def test_a_grade_is_read_from_the_bands_highest_first():
    bands = [("A", Fraction(16)), ("B", Fraction(14)), ("E", Fraction(0))]
    assert numbers.grade_for(Fraction(16), bands) == "A"
    assert numbers.grade_for(Fraction(31, 2), bands) == "B"
    assert numbers.grade_for(Fraction(0), bands) == "E"
