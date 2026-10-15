"""Baselines B0, B1, B2 (PREREG 3) as functions of (season, origin) over the guarded information set.

No score of any kind is computed here (PREREG: every scope cut happens before the first score; checkpoint 2 runs in F6).
Everything is exact rationals. Rows come only from `asof.build_features` (select + leakage assertion) or from
`trend.trend_at` (finals strictly before the origin, asserted).

PREREG 3 B1 is "scored at 90% against both truths". Reading adopted (PREREG-AMENDMENTS.md, 2026-10-16): "both truths" are the
primary truth (January first-final) and the sensitivity truth (latest Quick Stats value, a pseudo-vintage) of PREREG 1. The
PRODUCTION band is the object PREREG 3 defines and is scored against production truth. The YIELD band (production band divided
by USDA's harvested area at the origin) is a derived/added object NOT defined by PREREG 3; both are returned as separate fields
and nothing is scored in F5. B1 is unavailable at the Oct 2008 origin (see b1_band).
"""
import csv
from dataclasses import dataclass
from fractions import Fraction

from .asof import build_features, origins, ts
from .conformal import band_ranks
from .fetch import DATA
from .skeleton import truth_rows
from .store import read_store
from .trend import MIN_FINALS, trend_at

B1_MONTHS = (8, 9, 10, 11)


def season_of(origin: dict) -> int:
    return int(origin["release_date"][:4])


def pct_error(final: Fraction, point: Fraction) -> Fraction:
    """PREREG 2/3: e = 100 * (final - point) / point."""
    return 100 * (Fraction(final) - Fraction(point)) / Fraction(point)


def load() -> tuple[list[dict], list[dict]]:
    """(store rows, WASDE release rows)."""
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        return read_store(), list(csv.DictReader(f))


# ---- conformal band (reuses conformal.band_ranks) -------------------------------------------------------------------

@dataclass(frozen=True)
class Band:
    level: Fraction
    n: int
    k_lo: int
    k_hi: int
    e_lo: Fraction  # percent errors at the two ranks
    e_hi: Fraction
    lo: Fraction  # point * (1 + e/100), exact
    hi: Fraction


def percent_band(point: Fraction, errors: list[Fraction], level: Fraction | str) -> Band | None:
    """PREREG 2 split-conformal central band around `point` from past percent errors; None where it is not scored
    (1 <= k_lo and k_hi <= n fails)."""
    n = len(errors)
    k_lo, k_hi = band_ranks(n, level)
    if not (1 <= k_lo and k_hi <= n):
        return None
    srt = sorted(Fraction(e) for e in errors)
    e_lo, e_hi = srt[k_lo - 1], srt[k_hi - 1]
    p = Fraction(point)
    return Band(Fraction(level), n, k_lo, k_hi, e_lo, e_hi, p * (1 + e_lo / 100), p * (1 + e_hi / 100))


# ---- B0 --------------------------------------------------------------------------------------------------------------

def b0_point(store: list[dict], origin: dict, season: int | None = None) -> Fraction:
    """B0 yield for `season` (default: the origin's season): OLS trend on first-finals of crops >= 1995 released before
    the origin (trend.trend_at; ValueError below 5 finals)."""
    return trend_at(store, origin).predict(season_of(origin) if season is None else season)


def b0_errors(store: list[dict], releases: list[dict], origin: dict) -> list[Fraction]:
    """B0's own out-of-sample percent errors for the band at `origin`: every earlier season s' whose first-final was
    released before origin_ts, at the same-month origin of s', with B0 fitted as of that origin (needs >= MIN_FINALS finals
    there; earlier seasons without enough finals are skipped). Percent error e = 100*(final - B0)/B0."""
    o_ts, month = ts(origin), origin["release_date"][5:7]
    by_year = {season_of(o): o for o in origins(releases) if o["release_date"][5:7] == month}
    out = []
    for s in sorted(y for y in by_year if y < season_of(origin)):
        t = truth_rows(store, s)
        if t is None or ts(t) >= o_ts:
            continue
        try:
            p = b0_point(store, by_year[s], s)
        except ValueError:
            continue
        out.append(pct_error(Fraction(t["value"]), p))
    return out


def b0_band(store: list[dict], releases: list[dict], origin: dict, level: Fraction | str) -> Band | None:
    return percent_band(b0_point(store, origin), b0_errors(store, releases, origin), level)


# ---- B1 --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class B1:
    origin_date: str
    production_kbu: Fraction  # USDA production at the origin, thousand bushels
    area_kac: Fraction  # USDA harvested area at the origin, thousand acres
    yield_point: Fraction  # USDA yield at the origin = production / area (bu/ac)
    r_pct: Fraction  # printed 90% reliability, percent of final
    prod_lo: Fraction  # P / (1 + r/100)
    prod_hi: Fraction  # P / (1 - r/100)
    yield_lo: Fraction  # prod band / area
    yield_hi: Fraction


def _frac(r: dict) -> Fraction:
    return Fraction(r["value"])  # store values are repr(float); Fraction of the decimal string is exact


def b1_band(store: list[dict], origin: dict) -> B1:
    """USDA's printed 90% production band at an August-November origin. The Crop Production issued the same day is a joint
    release admitted by the guard's own-release exemption; it is taken from `build_features` (select + leakage assertion),
    never read from the raw store directly. Raises ValueError where the same-day Crop Production is not in the guarded
    information set: Oct 2008 only (its 10-10 file survives as the 28-Oct reissue, PREREG-AMENDMENTS.md item 1)."""
    if int(origin["release_date"][5:7]) not in B1_MONTHS:
        raise ValueError(f"B1 exists for August-November origins only, got {origin['release_date']}")
    feats = build_features(store, origin)
    ref = f"{season_of(origin)}/{str(season_of(origin) + 1)[2:]}"

    def get(name: str) -> Fraction:
        c = [r for r in feats if r["series"] == f"crop_production.us_corn.{name}" and r["release_date"] == origin["release_date"]
             and r["reference_period"] == ref]
        if len(c) != 1:
            raise ValueError(f"expected one same-day crop_production {name} row at {origin['release_date']}, got {len(c)}")
        assert ts(c[0]) == ts(origin)  # the joint release carries the origin's own timestamp
        return _frac(c[0])

    p, area, r = get("production_kbu"), get("area_harvested_kac"), get("rel_ci90_pct")
    lo, hi = p / (1 + r / 100), p / (1 - r / 100)
    return B1(origin["release_date"], p, area, get("yield"), r, lo, hi, lo / area, hi / area)


# ---- B2 --------------------------------------------------------------------------------------------------------------

def b2_point(store: list[dict], releases: list[dict], origin: dict) -> Fraction | None:
    """Carry-forward: USDA yield at the previous existing origin of the same season (None at the first origin of the
    season, i.e. May). The previous origin's WASDE is released before this one, so it is in the information set; checked."""
    prev = [o for o in origins(releases) if season_of(o) == season_of(origin) and o["release_date"] < origin["release_date"]]
    if not prev:
        return None
    po = prev[-1]
    feats = build_features(store, origin)
    ref = f"{season_of(origin)}/{str(season_of(origin) + 1)[2:]}"
    c = [r for r in feats if r["series"] == "wasde.us_corn.yield" and r["reference_period"] == ref
         and r["release_date"] == po["release_date"]]
    if len(c) != 1:
        raise ValueError(f"expected one previous-origin yield row at {po['release_date']}, got {len(c)}")
    return _frac(c[0])
