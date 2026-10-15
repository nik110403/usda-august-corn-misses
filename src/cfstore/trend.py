"""B0 trend pieces (STRATEGY 5.2, leakage test 3): linear OLS of first-final yield on season, fitted only on finals
released strictly before the origin. No score, coverage or interval is computed here (PREREG: nothing scored before F6).

First-final of season s = the yield for crop s in the first WASDE of calendar year s+1 that carries it (the January
WASDE; 8 Feb 2019 after the shutdown), exactly as the store's truth is defined (skeleton.truth_rows). Exact rationals.
"""
from dataclasses import dataclass
from fractions import Fraction

from .asof import LeakageError, ts
from .skeleton import truth_rows

YIELD = "wasde.us_corn.yield"


@dataclass(frozen=True)
class Trend:
    seasons: tuple[int, ...]
    intercept: Fraction
    slope: Fraction

    @property
    def n(self) -> int:
        return len(self.seasons)

    def predict(self, season: int) -> Fraction:
        return self.intercept + self.slope * season


def fit_trend(finals: list[tuple[int, Fraction, dict]]) -> Trend:
    """OLS yield = a + b * season on (season, final, row) triples; needs >= 2 distinct seasons."""
    xs = [Fraction(s) for s, _, _ in finals]
    ys = [y for _, y, _ in finals]
    n = len(xs)
    if len(set(xs)) < 2:
        raise ValueError(f"trend needs at least 2 distinct seasons, got {n}")
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return Trend(tuple(sorted(int(x) for x in xs)), my - b * mx, b)


def first_finals(store: list[dict]) -> list[tuple[int, Fraction, dict]]:
    """(season, first-final yield, source row) for every season whose first-final is in `store`."""
    seasons = sorted({int(r["reference_period"][:4]) for r in store if r["series"] == YIELD})
    out = []
    for s in seasons:
        t = truth_rows(store, s)
        if t is not None:
            out.append((s, Fraction(t["value"]), t))
    return out


def finals_before(store: list[dict], origin: dict) -> list[tuple[int, Fraction, dict]]:
    """The guarded selector: first-finals whose release_ts is strictly earlier than the origin's release_ts."""
    o_ts = ts(origin)
    return [f for f in first_finals(store) if ts(f[2]) < o_ts]


def assert_finals_before(finals: list[tuple[int, Fraction, dict]], origin: dict) -> None:
    """Leakage test 3 guard: fails if any final used in a fit was released at or after the origin."""
    for s, _, row in finals:
        if ts(row) >= ts(origin) or row["release_date"] >= origin["release_date"]:
            raise LeakageError(f"final of season {s} released {row['release_ts']} is not before origin {origin['release_ts']}")


def trend_at(store: list[dict], origin: dict) -> Trend:
    """Trend fitted for `origin` on the finals released before it (expanding window), guard asserted."""
    finals = finals_before(store, origin)
    assert_finals_before(finals, origin)
    return fit_trend(finals)
