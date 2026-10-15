"""DIAGNOSTIC: August M1 band for season 2025 from 2010+ seasons only. No scoring of any kind."""
import csv
from fractions import Fraction

from .asof import info_set, origins, ts
from .conformal import band_ranks
from .fetch import DATA
from .store import read_store


def truth_rows(store: list[dict], season: int) -> dict | None:
    """First-final: yield for crop `season` in the first WASDE of calendar year season+1 that carries it."""
    ref = f"{season}/{str(season + 1)[2:]}"
    c = [r for r in store if r["series"] == "wasde.us_corn.yield" and r["reference_period"] == ref
         and r["release_date"].startswith(str(season + 1)) and r["revision_flag"] != "v2_repost"]
    return min(c, key=lambda r: r["release_date"]) if c else None


def usda_yield(rows: list[dict], origin: dict, season: int) -> Fraction:
    ref = f"{season}/{str(season + 1)[2:]}"
    c = [r for r in rows if r["series"] == "wasde.us_corn.yield" and r["reference_period"] == ref
         and r["release_date"] == origin["release_date"] and r["revision_flag"] != "v2_repost"]
    assert len(c) == 1, (season, origin["release_date"], len(c))
    return Fraction(c[0]["value"])


FIRST_SEASON = 2010  # the diagnostic stays 2010+ only now that the store reaches back to 1995 (F4b); no scoring


def august_errors(store: list[dict], releases: list[dict], season_to: int, first_season: int = FIRST_SEASON) -> list[Fraction]:
    """Percent errors e = 100*(final - USDA_Aug)/USDA_Aug of 2010+ seasons whose first-final precedes the season_to August origin_ts."""
    aug = {int(o["release_date"][:4]): o for o in origins(releases) if o["release_date"][5:7] == "08"}
    o_now = aug[season_to]
    visible = info_set(store, o_now)
    errs = []
    for s in sorted(y for y in aug if y >= first_season):
        t = truth_rows(visible, s)
        if t is None:
            continue
        usda = usda_yield(store, aug[s], s)
        errs.append(100 * (Fraction(t["value"]) - usda) / usda)
    return errs


def run(season: int = 2025) -> None:
    store = read_store()
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        releases = list(csv.DictReader(f))
    aug = next(o for o in origins(releases) if o["release_date"].startswith(f"{season}-08"))
    point = usda_yield(store, aug, season)
    errs = sorted(august_errors(store, releases, season))
    n = len(errs)
    print("DIAGNOSTIC — 2010+ only, not a PREREG score")
    print(f"Season {season}, August M1 origin {aug['release_date']} (origin_ts {ts(aug).isoformat()})")
    print(f"n = {n} past percent errors (seasons 2010+ with first-final released before origin_ts)")
    print(f"USDA August point: {float(point):.1f} bu/ac")
    for label, level in (("80%", Fraction(4, 5)), ("50%", Fraction(1, 2))):
        k_lo, k_hi = band_ranks(n, level)
        if not (1 <= k_lo and k_hi <= n):
            print(f"{label} band: not scorable at n={n} (k_lo={k_lo}, k_hi={k_hi})")
            continue
        lo = point * (1 + errs[k_lo - 1] / 100)
        hi = point * (1 + errs[k_hi - 1] / 100)
        print(f"{label} band: k_lo={k_lo}, k_hi={k_hi}, e(k_lo)={float(errs[k_lo - 1]):+.2f}%, "
              f"e(k_hi)={float(errs[k_hi - 1]):+.2f}%  ->  [{float(lo):.1f}, {float(hi):.1f}] bu/ac")
