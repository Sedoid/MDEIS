"""Exact arithmetic for the answer key. Everything is a Fraction, so a change of exactly 1.0 is
exactly 1.0 and never 0.9999999. Only the final text is rounded."""

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction


def weighted_average(pairs):
    """pairs of (weight, value). Returns a Fraction."""
    total_w = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / total_w


def two_places(x):
    """A Fraction as text with 2 decimals, halves rounded up (13.5 prints as 13.50)."""
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def grade_for(score, bands):
    for grade, minimum in bands:
        if score >= minimum:
            return grade
    return bands[-1][0]


def distance_to_range(value, low, high):
    """0 inside the range (edges included), otherwise the distance to the nearest edge."""
    if value < low:
        return low - value
    if value > high:
        return value - high
    return Fraction(0)


def level_name(value, low, high):
    if value < low:
        return "below_expected_range"
    if value > high:
        return "above_expected_range"
    return "within_expected_range"


def to_fraction(text):
    return Fraction(text)
