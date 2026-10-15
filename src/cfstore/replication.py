"""NASS replication gate (STRATEGY 8, SPEC F5, checks/RESULTS item 3) and the Good-Irwin qualitative check.

A data-validation gate, not a PREREG score: nothing here touches B0/B1/M1.

NASS's own definition (CropProd-08-12-2015.txt lines 2647-2660, same text in 2025): the deviation between the August 1
production forecast and the final estimate is expressed as a percentage of the final estimate; the root of the mean of the
squared percentage deviations over the latest 20-year period is the "Root Mean Square Error"; "below" / "above" count the
years in which the forecast was below / above the final. The window is read as the 20 crop years before the issue year
(1995-2014 for the August 2015 issue, 2005-2024 for 2025). NASS does not define "final"; two readings are tried, no others:
  (a) the January Annual Summary production of the crop year (the primary-truth source; store series
      crop_production_summary.us_corn.production_kbu);
  (b) the latest estimate of that crop year in the store before the issue: the national row of the newest Annual Summary
      released before the August origin (a Summary prints three crop years, so a year is restated in up to two later
      January issues).
"""
import math
from fractions import Fraction

from .asof import info_set, origins
from .baselines import load, pct_error, season_of
from .nass import read_text
from .fetch import ROOT
from .parse_prod import SUMMARY_TITLE, parse_corn_table

PRINTED = {2015: {"rmse": Fraction("4.3"), "below": 10, "above": 10, "mean_mbu": 344, "min_mbu": 16, "max_mbu": 940},
           2025: {"rmse": Fraction("3.8"), "below": 7, "above": 13}}  # RESULTS item 3; re-read from the store rows below
WINDOW = 20
MISS_CAUSE = ("Cause: NASS's printed 'final' is its latest revised estimate, including Census-benchmark revisions made years after "
              "the January Annual Summary (an Annual Summary restates only the two previous crop years). The store holds no vintage "
              "after (b), so the older years' finals differ; printed mean and largest differences (370 / 1,192 Mbu "
              "in 2025, 361 / 1,167 here) are consistent with it. Not tuned further.")
MONTHS = {8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov'}
TOL_PP = Fraction(1, 10)


def _year(ref: str) -> int:
    return int(ref[:4])


def forecasts(store: list[dict], month: int, series: str = "production_kbu") -> dict[int, Fraction]:
    """{crop year: value} of the national corn series in the Crop Production issued in `month` of that year."""
    return {_year(r["reference_period"]): Fraction(r["value"]) for r in store
            if r["series"] == f"crop_production.us_corn.{series}" and int(r["release_date"][5:7]) == month
            and r["release_date"].startswith(r["reference_period"][:4])}


def finals_a(store: list[dict], series: str = "production_kbu") -> dict[int, Fraction]:
    """(a) January Annual Summary value of crop year Y (released in Y+1; reference_period 'Y/..')."""
    return {_year(r["reference_period"]): Fraction(r["value"]) for r in store
            if r["series"] == f"crop_production_summary.us_corn.{series}"}


def finals_b(store: list[dict], issue_origin: dict, series: str = "production_kbu") -> dict[int, Fraction]:
    """(b) newest estimate of each crop year in an Annual Summary visible (info_set) at the August origin of the issue.
    Re-reads each visible Summary's national row with all of its columns (the store keeps only the last one)."""
    name = "production_kbu" if series == "production_kbu" else "yield"
    summ = sorted((r for r in info_set(store, issue_origin) if r["series"] == f"crop_production_summary.us_corn.{name}"),
                  key=lambda r: r["release_date"])
    out: dict[int, Fraction] = {}
    for r in summ:  # ascending: later Summaries overwrite earlier ones
        cols = parse_corn_table(read_text(ROOT / r["source_file"]), SUMMARY_TITLE, columns=True)[name]
        for y, v in cols:
            out[y] = Fraction(str(v))
    return out


def replicate(fc: dict[int, Fraction], fin: dict[int, Fraction], issue_year: int) -> dict:
    ys = range(issue_year - WINDOW, issue_year)
    missing = [y for y in ys if y not in fc or y not in fin]
    if missing:
        raise ValueError(f"window {issue_year - WINDOW}-{issue_year - 1}: missing forecast or final for {missing}")
    dev = [pct_error(fc[y], fin[y]) for y in ys]  # pct_error(x, base) = 100*(x - base)/base: here forecast vs final as base
    absd = [abs(fc[y] - fin[y]) / 1000 for y in ys]  # million bushels
    return {"rmse": math.sqrt(float(sum(d * d for d in dev) / len(dev))),
            "below": sum(fc[y] < fin[y] for y in ys), "above": sum(fc[y] > fin[y] for y in ys),
            "mean_mbu": float(sum(absd) / len(absd)), "min_mbu": float(min(absd)), "max_mbu": float(max(absd)), "n": len(dev)}


def printed(store: list[dict], year: int) -> dict:
    g = {r["series"].rsplit(".", 1)[1]: Fraction(r["value"]) for r in store
         if r["series"].startswith("crop_production.us_corn.rel_") and r["release_date"][:7] == f"{year}-08"}
    return {"rmse": g["rel_rmse_pct"], "below": int(g["rel_n_below"]), "above": int(g["rel_n_above"]),
            "mean_mbu": int(g["rel_mean_abs_diff_mbu"]), "min_mbu": int(g["rel_min_abs_diff_mbu"]), "max_mbu": int(g["rel_max_abs_diff_mbu"])}


def run() -> int:
    store, releases = load()
    fc = forecasts(store, 8)
    rows = []
    print("NASS August corn PRODUCTION reliability, replicated from the store (deviation in % of final; latest 20 crop years)")
    print(f"{'issue':<9}{'final':<6}{'window':<11}{'RMSE%':>7}{'printed':>8}{'diff':>7}{'below/above':>13}{'printed':>9}"
          f"{'mean/min/max Mbu':>20}{'printed':>15}  verdict")
    for yr in (2015, 2025):
        pr = printed(store, yr)
        assert pr["rmse"] == PRINTED[yr]["rmse"] and (pr["below"], pr["above"]) == (PRINTED[yr]["below"], PRINTED[yr]["above"]), pr
        aug = next(o for o in origins(releases) if o["release_date"].startswith(f"{yr}-08"))
        for label, fin in (("a", finals_a(store)), ("b", finals_b(store, aug))):
            r = replicate(fc, fin, yr)
            diff = r["rmse"] - float(pr["rmse"])
            cnt_off = max(abs(r["below"] - pr["below"]), abs(r["above"] - pr["above"]))
            verdict = ("RMSE ok" if abs(diff) <= float(TOL_PP) + 1e-9 else "RMSE MISS") + (", counts exact" if cnt_off == 0 else
                      ", counts off by one" if cnt_off == 1 else ", counts MISS")
            rows.append((yr, label, abs(diff) <= float(TOL_PP) + 1e-9 and cnt_off <= 1))
            print(f"Aug {yr:<5}{label:<6}{yr - WINDOW}-{yr - 1:<5}{r['rmse']:>7.2f}{float(pr['rmse']):>8.1f}{diff:>+7.2f}"
                  f"{str(r['below']) + '/' + str(r['above']):>13}{str(pr['below']) + '/' + str(pr['above']):>9}"
                  f"{'%.0f/%.0f/%.0f' % (r['mean_mbu'], r['min_mbu'], r['max_mbu']):>20}"
                  f"{'%d/%d/%d' % (pr['mean_mbu'], pr['min_mbu'], pr['max_mbu']):>15}  {verdict}")
    # Gate (SPEC F5): a miss is allowed only with a documented cause, so a miss prints it and the command still exits 0.
    for yr in (2015, 2025):
        good = [lab for y, lab, g in rows if y == yr and g]
        if good:
            print(f"Aug {yr}: GATE MET with final {'/'.join(good)} (RMSE within 0.1 pp, counts off by at most one)")
        else:
            print(f"Aug {yr}: GATE MISSED with both finals. {MISS_CAUSE}")
    good_irwin(store, releases)
    return 0


def good_irwin(store: list[dict], releases: list[dict]) -> None:
    """Is the August forecast error RMSE the largest of Aug/Sep/Oct/Nov? NASS-data check on crop years with all four
    issues in the store (no October 2013), against final (a) and (b as of the Aug 2025 issue). Production and yield."""
    aug25 = next(o for o in origins(releases) if o["release_date"].startswith("2025-08"))
    print("\nGood-Irwin qualitative check: RMSE (% of final) of the Crop Production forecast by issue month (qualitative, not a PREREG score)")
    for series, fa, fb in (("production_kbu", finals_a(store), finals_b(store, aug25)),
                           ("yield", finals_a(store, "yield"), finals_b(store, aug25, "yield"))):
        for label, fin in (("a", fa), ("b", fb)):
            for lo, hi in ((1995, 2024), (2005, 2024), (1995, 2014)):
                fcs = {m: forecasts(store, m, series) for m in (8, 9, 10, 11)}
                ys = [y for y in range(lo, hi + 1) if y in fin and all(y in fcs[m] for m in fcs)]
                rm = {m: math.sqrt(float(sum(pct_error(fcs[m][y], fin[y]) ** 2 for y in ys) / len(ys))) for m in fcs}
                top = max(rm, key=rm.get)
                print(f"{series:<15} final {label} {lo}-{hi} n={len(ys):>2}  Aug {rm[8]:.2f}  Sep {rm[9]:.2f}  Oct {rm[10]:.2f}  Nov {rm[11]:.2f}"
                      f"  largest: {MONTHS[top]}")


if __name__ == "__main__":
    raise SystemExit(run())
