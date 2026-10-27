"""POST-HOC EXPLORATORY descriptives of USDA's August corn yield vs the final (not pre-registered, not scored).
Writes only out/explore/{seasons.csv,reliability.csv,summary.md} so market-note numbers can pass the number trace.
Reads data/store.csv only; touches no PREREG artefact, forecast or score.

Selection rules (season y = int(reference_period[:4]), seasons 1995-2025; revision_flag v2_repost rows are skipped):
- A month without any print (no Oct 2013 / Oct 2025 report, government shutdowns) leaves that yield and its oct/nov error blank;
  means skip blanks.
- Month-m value of season y = the release in calendar month m of year y. Several releases in a month: earliest release_ts
  (first print); v1 / v1_corrected_only / v1_reissue are all eligible.
- Yield, production, harvested area come from Crop Production (CP: crop_production.us_corn.{yield,production_kbu,
  area_harvested_kac}). If CP has no value, fall back to the WASDE of the same month (wasde.us_corn.yield; production x1000
  Mbu->kbu; area_harvested x1000 Mac->kac); the source used is recorded in yield_source.
- Final = the first crop_production_summary.us_corn.{yield,production_kbu} release dated in year y+1 (Jan or Feb; the 2014 and
  2018 summaries came out in February).
- Ending stocks / total use: the August WASDE of year y, and the first WASDE released on or after the Annual Summary's release
  date (final_wasde_date).
- sep_side_agree = sign(sep-aug)==sign(final-aug). Zero-momentum baseline for it: 0.5+asin(rho)/pi with
  rho = sqrt(E[sep_move^2]/(E[sep_move^2]+E[post_sep_move^2])) (a driftless walk: P(same side of Aug at Sep and at final)).
- reliability.csv: rel_rmse_pct / rel_ci90_pct as printed in each August CP report (production, USDA's prior 20-year window)."""
import csv
import math
import statistics as st
from datetime import datetime

from .fetch import DATA, ROOT
from .models import write_csv

CP, CS, W = "crop_production.us_corn.", "crop_production_summary.us_corn.", "wasde.us_corn."
YEARS = range(1995, 2026)
COLS = ["season", "aug_yield", "sep_yield", "oct_yield", "nov_yield", "final_yield", "final_release_date", "aug_prod_mbu",
        "final_prod_mbu", "aug_area_mac", "err_pct", "abs_err_pct", "prod_rev_mbu", "yield_part_mbu", "area_part_mbu",
        "aug_es_mbu", "final_es_mbu", "final_wasde_date", "d_es_mbu", "d_es_pct_of_aug_es", "usefixed_pct", "oct_err_pct",
        "nov_err_pct", "sep_move", "post_sep_move", "sep_side_agree", "yield_source"]
REL_COLS = ["season", "release_date", "rel_rmse_pct", "rel_ci90_pct"]


def index(rows: list[dict]) -> dict:
    """(series, season) -> releases sorted by release_ts: [(release_date, ts, value)]. v2_repost skipped."""
    d: dict = {}
    for r in rows:
        if r["revision_flag"] == "v2_repost":
            continue
        d.setdefault((r["series"], int(r["reference_period"][:4])), []).append(
            (r["release_date"], datetime.fromisoformat(r["release_ts"]), float(r["value"])))
    for v in d.values():
        v.sort(key=lambda x: x[1])
    return d


def month_val(d: dict, series: str, y: int, m: int):
    for date, _, v in d.get((series, y), []):
        if date[:7] == f"{y}-{m:02d}":
            return v


def cp_val(d: dict, name: str, y: int, m: int):
    """(value, source) for yield / production_kbu / area_harvested_kac: CP, else same-month WASDE (converted)."""
    v = month_val(d, CP + name, y, m)
    if v is not None:
        return v, "CP"
    w, k = {"yield": ("yield", 1), "production_kbu": ("production", 1000), "area_harvested_kac": ("area_harvested", 1000)}[name]
    v = month_val(d, W + w, y, m)
    return (v * k, "WASDE") if v is not None else (None, "")


def first_after(d: dict, series: str, y: int, date: str):
    """First WASDE value of season y released on or after date: (release_date, value)."""
    for rd, _, v in d.get((series, y), []):
        if rd >= date:
            return rd, v
    return None, None


def final_print(d: dict, y: int):
    """(release_date, yield, production_kbu) of the first Annual Summary dated in year y+1."""
    for rd, _, v in d.get((CS + "yield", y), []):
        if rd[:4] == str(y + 1):
            p = [pv for pd, _, pv in d.get((CS + "production_kbu", y), []) if pd == rd]
            return rd, v, (p[0] if p else None)
    return None, None, None


def pct(final, x):
    """100*(final-x)/x, None when the month's print is missing (no Oct 2013 / Oct 2025 report: shutdowns)."""
    return None if x is None else 100 * (final - x) / x


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def season_row(d: dict, y: int):
    fdate, fy, fp = final_print(d, y)
    if fy is None:
        return None
    (ay, s1), (sy, s2), (oy, s3), (ny, s4) = (cp_val(d, "yield", y, m) for m in (8, 9, 10, 11))
    ap, _ = cp_val(d, "production_kbu", y, 8)
    aa, _ = cp_val(d, "area_harvested_kac", y, 8)
    prod_rev = (fp - ap) / 1000
    yield_part = (fy - ay) * aa / 1000
    aug_es = month_val(d, W + "ending_stocks", y, 8)
    wdate, final_es = first_after(d, W + "ending_stocks", y, fdate)
    r = dict(season=y, aug_yield=ay, sep_yield=sy, oct_yield=oy, nov_yield=ny, final_yield=fy, final_release_date=fdate,
             aug_prod_mbu=ap / 1000, final_prod_mbu=fp / 1000, aug_area_mac=aa / 1000, err_pct=100 * (fy - ay) / ay,
             prod_rev_mbu=prod_rev, yield_part_mbu=yield_part, area_part_mbu=prod_rev - yield_part, aug_es_mbu=aug_es,
             final_es_mbu=final_es, final_wasde_date=wdate, d_es_mbu=final_es - aug_es,
             d_es_pct_of_aug_es=100 * (final_es - aug_es) / aug_es, usefixed_pct=100 * prod_rev / aug_es,
             oct_err_pct=pct(fy, oy), nov_err_pct=pct(fy, ny), sep_move=sy - ay, post_sep_move=fy - sy,
             sep_side_agree=sign(sy - ay) == sign(fy - ay), yield_source="+".join(sorted({s1, s2, s3, s4} - {""})))
    r["abs_err_pct"] = abs(r["err_pct"])
    return r


def reliability_rows(d: dict) -> list[dict]:
    out = []
    for y in range(1995, 2027):
        a = [(rd, v) for rd, _, v in d.get((CP + "rel_rmse_pct", y), []) if rd[:7] == f"{y}-08"]
        c = month_val(d, CP + "rel_ci90_pct", y, 8)
        if a:
            out.append(dict(season=y, release_date=a[0][0], rel_rmse_pct=a[0][1], rel_ci90_pct=c))
    return out


def display(rows: list[dict]) -> list[dict]:
    nd = {"aug_yield": 1, "sep_yield": 1, "oct_yield": 1, "nov_yield": 1, "final_yield": 1, "sep_move": 1, "post_sep_move": 1,
          "aug_prod_mbu": 1, "final_prod_mbu": 1, "aug_area_mac": 3, "prod_rev_mbu": 1, "yield_part_mbu": 1, "area_part_mbu": 1,
          "d_es_mbu": 0, "aug_es_mbu": 0, "final_es_mbu": 0}
    return [{k: (round(v, nd.get(k, 2)) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]


def ols(x: list[float], y: list[float]):
    n, mx, my = len(x), st.mean(x), st.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    res = sum((c - my - b * (a - mx)) ** 2 for a, c in zip(x, y))
    return b, b / math.sqrt(res / (n - 2) / sxx), n


def summary(rows: list[dict], rel: list[dict]) -> str:
    sub = lambda a, b: [r for r in rows if a <= r["season"] <= b]
    mabs = lambda rs, k: st.mean(abs(r[k]) for r in rs if r[k] is not None)
    f = lambda x, nd=2: f"{x:.{nd}f}"
    n = len(rows)
    L = ["POST-HOC EXPLORATORY — not pre-registered, not scored; descriptive only.", "",
         f"n seasons: {n} ({rows[0]['season']}-{rows[-1]['season']}), yield from first August Crop Production print vs January Annual Summary.", "",
         "## Mean absolute yield miss, |err_pct| (%)", ""]
    for a, b in ((1995, 2009), (2010, 2025), (2015, 2025), (2004, 2014)):
        L.append(f"- {a}-{b}: {f(mabs(sub(a, b), 'err_pct'))} (n={len(sub(a, b))})")
    slope, t, k = ols([r["season"] for r in rows], [r["abs_err_pct"] for r in rows])
    L += [f"- 2004-2014 vs 2015-2025: {f(mabs(sub(2004, 2014), 'err_pct'))} vs {f(mabs(sub(2015, 2025), 'err_pct'))}",
          f"- OLS of abs_err_pct on season: slope {f(slope, 3)} pp/yr, t = {f(t)}, n = {k}", ""]
    L.append("## Ending-stocks revision and production revision relative to August ending stocks")
    L.append("")
    for k_, lab in (("d_es_pct_of_aug_es", "|d_es_pct_of_aug_es| >= 25"), ("usefixed_pct", "|usefixed_pct| >= 25")):
        for a in (1995, 2015):
            rs = sub(a, 2025)
            hit = [r["season"] for r in rs if abs(r[k_]) >= 25]
            L.append(f"- {lab}, {a}-2025: {len(hit)}/{len(rs)}; seasons {', '.join(map(str, hit)) or 'none'}")
        L.append(f"- median |{k_}| (1995-2025): {f(st.median(abs(r[k_]) for r in rows))}")
    L += ["", "## Mean absolute production revision and its parts (Mbu)", "",
          "| period | |yield_part| | |area_part| | |prod_rev| |", "|---|---|---|---|"]
    for a, b in ((1995, 2009), (2010, 2025), (2015, 2025)):
        rs = sub(a, b)
        L.append(f"| {a}-{b} | {f(mabs(rs, 'yield_part_mbu'), 0)} | {f(mabs(rs, 'area_part_mbu'), 0)} | {f(mabs(rs, 'prod_rev_mbu'), 0)} |")
    ag = sum(r["sep_side_agree"] for r in rows)
    m1, m2 = (st.mean(r[k_] ** 2 for r in rows) for k_ in ("sep_move", "post_sep_move"))
    rho = math.sqrt(m1 / (m1 + m2))
    base = 0.5 + math.asin(rho) / math.pi
    cont = sum(sign(r["post_sep_move"]) == sign(r["sep_move"]) for r in rows)
    L += ["", "## September momentum", "",
          f"- sep_side_agree (sign(sep-aug)==sign(final-aug)): {ag}/{n}",
          f"- zero-momentum baseline 0.5+asin(rho)/pi, rho = sqrt(E[sep_move^2]/(E[sep_move^2]+E[post_sep_move^2])): rho = {f(rho, 3)}, "
          f"baseline {f(base, 3)} (expected {f(base * n, 1)} of {n})",
          f"- continuation (sign(post_sep_move)==sign(sep_move)): {cont}/{n}", "",
          "## Later-month yield misses, mean |err| (%)", "", "| period | |oct_err_pct| | |nov_err_pct| |", "|---|---|---|"]
    for a, b in ((2004, 2014), (2015, 2025)):
        L.append(f"| {a}-{b} | {f(mabs(sub(a, b), 'oct_err_pct'))} | {f(mabs(sub(a, b), 'nov_err_pct'))} |")
    L += ["", "## Stress seasons", ""]
    for y in (2020, 2025, 2011):
        r = next(r for r in rows if r["season"] == y)
        L.append(f"- {y}: yield {f(r['aug_yield'], 1)} -> {f(r['final_yield'], 1)}; prod_rev {f(r['prod_rev_mbu'], 1)} Mbu "
                 f"(yield_part {f(r['yield_part_mbu'], 1)}, area_part {f(r['area_part_mbu'], 1)}); ending stocks {f(r['aug_es_mbu'], 0)} -> "
                 f"{f(r['final_es_mbu'], 0)} (d_es {f(r['d_es_mbu'], 0)} Mbu, {f(r['d_es_pct_of_aug_es'], 1)}%; final WASDE {r['final_wasde_date']})")
    L += ["", "## August reliability (as printed; refers to production over USDA's prior 20-year window)", ""]
    for r in rel:
        if r["season"] in (1995, 2004, 2015, 2025, 2026):
            L.append(f"- {r['season']}: rel_rmse_pct {f(r['rel_rmse_pct'], 1)}, rel_ci90_pct {f(r['rel_ci90_pct'], 1)}")
    return "\n".join(L) + "\n"


def run(root=None) -> int:
    root = root or ROOT
    with (DATA / "store.csv").open(newline="") as fh:
        d = index(list(csv.DictReader(fh)))
    rows = [r for r in (season_row(d, y) for y in YEARS) if r]
    rel = reliability_rows(d)
    out = root / "out" / "explore"
    write_csv(out / "seasons.csv", display(rows), COLS)
    write_csv(out / "reliability.csv", rel, REL_COLS)
    (out / "summary.md").write_text(summary(rows, rel))
    return 0
