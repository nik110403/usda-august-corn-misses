from fractions import Fraction
from math import floor

from cfstore.conformal import band_ranks, band_scored


def test_ranks_exact():
    assert band_ranks(9, Fraction(4, 5)) == (1, 9)
    assert band_ranks(22, Fraction(4, 5)) == (2, 21)
    assert band_ranks(15, Fraction(4, 5)) == (1, 15)
    assert band_ranks(9, "0.8") == (1, 9)  # string levels are parsed exactly


def test_float_arithmetic_gets_n9_wrong():
    alpha = 1 - 0.8  # 0.19999999999999996
    assert floor((9 + 1) * alpha / 2) == 0  # float drops k_lo to 0 ...
    assert band_ranks(9, Fraction(4, 5))[0] == 1  # ... exact arithmetic gives 1
    assert not (1 <= floor(10 * alpha / 2))  # float would wrongly unscore n = 9


def test_scored_thresholds():
    assert band_scored(9, Fraction(4, 5)) and band_scored(9, Fraction(1, 2))
    assert not band_scored(8, Fraction(4, 5))
    assert not band_scored(18, Fraction(9, 10)) and band_scored(19, Fraction(9, 10))
