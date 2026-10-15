"""Split-conformal central-band ranks in exact rational arithmetic (PREREG section 2)."""
from fractions import Fraction
from math import ceil, floor


def band_ranks(n: int, level: Fraction | str) -> tuple[int, int]:
    """(k_lo, k_hi) for a central band at coverage `level` (e.g. Fraction(4, 5)) from n past errors.

    k_lo = floor((n+1)*alpha/2), k_hi = ceil((n+1)*(1-alpha/2)), alpha = 1 - level.
    The band is scored only if 1 <= k_lo and k_hi <= n.
    """
    alpha = 1 - Fraction(level)
    return floor((n + 1) * alpha / 2), ceil((n + 1) * (1 - alpha / 2))


def band_scored(n: int, level: Fraction | str) -> bool:
    k_lo, k_hi = band_ranks(n, level)
    return 1 <= k_lo and k_hi <= n
