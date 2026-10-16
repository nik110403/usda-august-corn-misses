"""Scoring primitives for the evaluation harness (PREREG 4, 5, 10). Pure functions; tested on synthetic data only
(tests/test_evaluate.py). Nothing here reads a forecast or a truth."""
from fractions import Fraction
from math import comb, sqrt

from scipy import stats as sps

# PREREG 4.2 wordings, in the order of the ordered rule; {nom} is the nominal level in percent.
WORD_OK = "consistent with nominal {nom}% within ±15 points"
WORD_NARROW = "too narrow"
WORD_WIDE = "too wide"
WORD_NONE = "not distinguishable from {nom}% at this sample size, and not shown equivalent"
WORD_M3_REJECT = "M3 improves on M1 at August (one-sided HLN-DM, p = {p}, {n} seasons)"
WORD_M3_NOREJ = "M3 and M1 are not distinguishable at August with {n} seasons."
WORD_M3_CUT = "the pre-registered comparison was not run"


def clopper_pearson(x: int, n: int, conf: float = 0.90) -> tuple[float, float]:
    """Exact two-sided Clopper-Pearson interval (beta quantiles) for x successes of n at confidence `conf`."""
    a = 1 - conf
    lo = 0.0 if x == 0 else float(sps.beta.ppf(a / 2, x, n - x + 1))
    hi = 1.0 if x == n else float(sps.beta.ppf(1 - a / 2, x + 1, n - x))
    return lo, hi


def wording(x: int, n: int, nominal: Fraction, margin: Fraction = Fraction(15, 100)) -> tuple[str, tuple[float, float]]:
    """PREREG 4.2 ordered rule on the two-sided 90% Clopper-Pearson interval; returns (wording, interval).
    Order: inside [nominal-margin, nominal+margin] ; entirely below nominal ; entirely above nominal ; otherwise."""
    lo, hi = clopper_pearson(x, n)
    nom_pct = f"{float(nominal) * 100:g}"
    if float(nominal - margin) <= lo and hi <= float(nominal + margin):
        w = WORD_OK
    elif hi < float(nominal):
        w = WORD_NARROW
    elif lo > float(nominal):
        w = WORD_WIDE
    else:
        w = WORD_NONE
    return w.format(nom=nom_pct), (lo, hi)


def binom_p_two_sided(x: int, n: int, p: Fraction) -> float:
    """PREREG 5: p = min(1, 2 * min(P(X <= x), P(X >= x))) under Binomial(n, p), exact rationals."""
    p = Fraction(p)
    pmf = [comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]
    return float(min(Fraction(1), 2 * min(sum(pmf[: x + 1]), sum(pmf[x:]))))


def hln_dm(d: list[float], alternative: str = "greater") -> dict:
    """Diebold-Mariano with the Harvey-Leybourne-Newbold correction for horizon 1 (PREREG 4.4): gamma0 = (1/n) sum (d - dbar)^2,
    statistic DM * sqrt((n-1)/n), which equals the paired t statistic dbar / (s_d / sqrt(n)); Student t on n-1 df.
    alternative 'greater' = one-sided H1: E[d] > 0; 'two-sided'. Degenerate d (no variance) gives p = 1 when dbar = 0."""
    n = len(d)
    if n < 2:
        raise ValueError("need at least 2 loss differentials")
    dbar = sum(d) / n
    g0 = sum((v - dbar) ** 2 for v in d) / n
    if g0 == 0:
        t = 0.0 if dbar == 0 else float("inf") * (1 if dbar > 0 else -1)
    else:
        t = (dbar / sqrt(g0 / n)) * sqrt((n - 1) / n)
    if alternative == "greater":
        p = float(sps.t.sf(t, n - 1)) if dbar != 0 or g0 != 0 else 1.0
    elif alternative == "two-sided":
        p = float(min(1.0, 2 * sps.t.sf(abs(t), n - 1))) if dbar != 0 or g0 != 0 else 1.0
    else:
        raise ValueError(alternative)
    return {"n": n, "dbar": dbar, "t": t, "df": n - 1, "p": p}


def holm(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in the input order. A cut test enters with p = 1."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: (pvals[i], i))
    adj, run = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj


def pinball(u: Fraction | float, tau: Fraction | float):
    """Standard rho_tau(u) = u * (tau - 1{u < 0}); u = percent error minus quantile (both percent space)."""
    return u * (tau - (1 if u < 0 else 0))


def season_loss(us: list, taus: list):
    """Per-season loss: mean pinball over the tau set ('truncated quantile score', not CRPS)."""
    return sum(pinball(u, t) for u, t in zip(us, taus)) / len(taus)


def mincer_zarnowitz(fc: list[float], fin: list[float]) -> dict:
    """OLS fin = a + b * fc; joint F test of (a, b) = (0, 1) on (2, n-2) df."""
    n = len(fc)
    mx, my = sum(fc) / n, sum(fin) / n
    sxx = sum((x - mx) ** 2 for x in fc)
    if sxx == 0 or n < 3:  # constant forecasts: the regression is undefined
        return {"n": n, "a": float("nan"), "b": float("nan"), "F": float("nan"), "p": float("nan")}
    b = sum((x - mx) * (y - my) for x, y in zip(fc, fin)) / sxx
    a = my - b * mx
    ssr = sum((y - a - b * x) ** 2 for x, y in zip(fc, fin))
    ssr0 = sum((y - x) ** 2 for x, y in zip(fc, fin))  # restricted: a = 0, b = 1
    f = ((ssr0 - ssr) / 2) / (ssr / (n - 2)) if ssr > 0 else float("inf")
    return {"n": n, "a": a, "b": b, "F": f, "p": float(sps.f.sf(f, 2, n - 2)) if ssr > 0 else 0.0}
