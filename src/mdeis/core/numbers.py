"""Exact arithmetic for results. Everything is a Fraction, so a change of exactly 1.0 is exactly 1.0
and never 0.9999999. Only the final text is rounded, to two decimals, halves up ("13.5" is "13.50").

This module holds no school value. It is arithmetic only.
"""

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction


def exact(value) -> Fraction:
    """A number from the rulebook or a card as an exact fraction (2.5 is 5/2, not a float)."""
    return Fraction(str(value))


def two_places(x: Fraction) -> str:
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def weighted_average(pairs) -> Fraction:
    """`pairs` of (weight, value). Returns a Fraction."""
    total = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / total


def distance_to_range(value: Fraction, low: Fraction, high: Fraction) -> Fraction:
    """Zero inside the range (the edges included), otherwise the distance to the nearest edge."""
    if value < low:
        return low - value
    if value > high:
        return value - high
    return Fraction(0)


def level_name(value: Fraction, low: Fraction, high: Fraction) -> str:
    if value < low:
        return "below_expected_range"
    if value > high:
        return "above_expected_range"
    return "within_expected_range"


def grade_for(score: Fraction, bands) -> str:
    """`bands` is [(grade, lowest score)] from the highest band down."""
    for grade, lowest in bands:
        if score >= lowest:
            return grade
    return bands[-1][0]
