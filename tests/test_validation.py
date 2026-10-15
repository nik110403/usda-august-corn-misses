"""Store validation: WASDE identity (all eras) and TXT-era header/date/coverage assertions.

Needs data/store.csv and data/raw/: run `make store` first. No scoring of any kind.
Identity tolerance: <= 1 unit of the printed precision (1 million bushels; WASDE tables print integer million
bushels and note 'Totals may not add due to rounding').
"""
import collections
import csv
import hashlib
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from cfstore.fetch import DATA, ROOT
from cfstore.parse import parse_corn
from cfstore.parse_cp import parse_progress
from cfstore.parse_prod import parse_corn_table, parse_reliability
from cfstore.parse_txt import MONTHS, parse_corn_txt, parse_header
from cfstore.store import HEADER_DATE_EXCEPTIONS, TXT_NUMBERS, XML_FROM, read_store

TOL = 1.0


@pytest.fixture(scope="module")
def store():
    return read_store()


@pytest.fixture(scope="module")
def releases():
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def vintages(store):
    g = collections.defaultdict(dict)
    for r in store:
        if not r["series"].startswith("wasde.us_corn."):
            continue  # NASS series (Crop Progress / Crop Production) are not balance-sheet vintages
        g[(r["release_date"], r["revision_flag"], r["source_file"], r["reference_period"])][r["series"].rsplit(".", 1)[1]] = float(r["value"])
    return g


def identity_failures(g, tol=TOL):
    bad = []
    for k, v in sorted(g.items()):
        need = {"beginning_stocks", "production", "imports", "supply_total", "total_use", "ending_stocks"}
        if need - v.keys():
            bad.append((k, "missing", sorted(need - v.keys())))
            continue
        supply = v["beginning_stocks"] + v["production"] + v["imports"] - v["supply_total"]
        balance = v["supply_total"] - v["total_use"] - v["ending_stocks"]  # Use, total already includes exports
        if abs(supply) > tol or abs(balance) > tol:
            bad.append((k, supply, balance))
    return bad


def test_identity_all_eras(store):
    g = vintages(store)
    # 380 release rows incl. the Oct 2008 reissue (2 market years) + v2 re-posts (2008-12-12, 2013-07-11, 2018-12-14)
    assert len(g) == 3 * 379 + 2 + 3 * 3, len(g)
    bad = identity_failures(g)
    print(f"identity: {len(g)} vintage x year checks, {len(bad)} failures")
    assert not bad, bad[:10]


def test_identity_check_is_armed(store):
    g = vintages(store)
    k = next(k for k in g if k[0] == "2005-08-12")
    broken = {**g, k: {**g[k], "ending_stocks": g[k]["ending_stocks"] + 5}}
    assert [b[0] for b in identity_failures(broken)] == [k]


def test_exports_included_in_total_use(store):
    """Use, total = domestic use + exports in the table (so no separate exports term in the balance)."""
    g = vintages(store)
    v = g[next(k for k in g if k[0] == "2005-08-12")]
    assert v["total_use"] > v["exports"] > 0 and v["total_use"] > 1.5 * v["exports"]


# ---- TXT era -----------------------------------------------------------------------------------------------

def txt_releases(releases):
    return [r for r in releases if r["release_date"] < XML_FROM and r["file"].endswith(".txt")]


def primary(releases):
    return [r for r in releases if r["revision_flag"] != "v1_reissue"]


def test_txt_coverage_one_primary_per_month(releases):
    t = primary(txt_releases(releases))
    assert len(t) == len(TXT_NUMBERS) == 186
    months = collections.Counter(r["release_date"][:7] for r in t)
    expect = [f"{y}-{m:02d}" for y in range(1995, 2011) for m in range(1, 13) if f"{y}-{m:02d}" <= "2010-06"]
    assert sorted(months) == expect and set(months.values()) == {1}
    assert not [r for r in releases if r["release_date"] < XML_FROM and not r["file"].endswith(".txt")]
    assert all(int(r["n_values"]) == 27 for r in primary(releases))  # 9 lines x 3 years
    assert [r["n_values"] for r in releases if r["revision_flag"] == "v1_reissue"] == ["18"]  # 9 lines x 2 years


def test_one_primary_per_month_except_shutdowns(releases):
    """Every month Jan 1995 - last release has exactly one primary release, except the shutdown months; reposts
    (same WASDE number, later date, e.g. 2018-12-14) are v2_repost and not in the release table; the Oct 2008
    reissue is its own release (v1_reissue) and is not a primary."""
    cnt = collections.Counter(r["release_date"][:7] for r in primary(releases))
    last = max(cnt)
    months = [f"{y}-{m:02d}" for y in range(1995, int(last[:4]) + 1) for m in range(1, 13) if f"{y}-{m:02d}" <= last]
    zero = [m for m in months if cnt[m] == 0]
    assert zero == ["2013-10", "2019-01", "2025-10"]
    assert [m for m in months if cnt[m] > 1] == []
    assert not [r for r in releases if r["release_date"] == "2018-12-14" or r["revision_flag"] == "v2_repost"]


def test_reissue_2008_10_28_is_a_release_never_an_origin(store, releases):
    from cfstore.asof import build_features, origins
    reissue = [r for r in releases if r["revision_flag"] == "v1_reissue"]
    assert [r["release_date"] for r in reissue] == ["2008-10-28"]
    assert reissue[0]["release_ts"].startswith("2008-10-28T08:30:00")
    oct_origin = [o for o in origins(releases) if o["release_date"].startswith("2008-10")]
    assert [o["release_date"] for o in oct_origin] == ["2008-10-10"]
    rows = {(r["series"].rsplit(".", 1)[1], r["reference_period"]): float(r["value"]) for r in store
            if r["release_date"] == "2008-10-28"}
    assert rows[("production", "2008/09")] == 12033.0 and rows[("yield", "2008/09")] == 153.9
    assert rows[("area_harvested", "2008/09")] == 78.2 and rows[("ending_stocks", "2008/09")] == 1088.0
    old = {(r["series"].rsplit(".", 1)[1], r["reference_period"]): float(r["value"]) for r in store
           if r["release_date"] == "2008-10-10" and r["revision_flag"] == "v1"}
    assert (old[("production", "2008/09")], old[("yield", "2008/09")], old[("area_harvested", "2008/09")]) == (12200.0, 154.0, 79.2)
    nov = next(o for o in origins(releases) if o["release_date"] == "2008-11-10")
    feats = build_features(store, nov)  # guard accepts the reissue as a normal earlier release
    assert any(r["release_date"] == "2008-10-28" for r in feats)


def test_txt_headers_match_files(releases):
    """Re-read each stored TXT's cover: contiguous WASDE numbers; cover date == release_date except the
    documented exceptions (original reports take the cover date, corrected-only files the ESMIS date)."""
    t = sorted(primary(txt_releases(releases)), key=lambda r: r["release_date"])
    nos = []
    for r in t:
        text = (ROOT / r["file"]).read_bytes().decode("latin-1")
        no, hd = parse_header(text)
        nos.append(no)
        listing_date = r["file"].split("/")[3]  # data/raw/wasde/<ESMIS date>/<id>/<file>
        if hd.isoformat() == r["release_date"]:
            assert listing_date == r["release_date"] or listing_date in HEADER_DATE_EXCEPTIONS, r
        else:
            assert HEADER_DATE_EXCEPTIONS[listing_date][0] == hd.isoformat(), r
            if r["revision_flag"] == "v1":  # original report: release_date is the cover date
                assert r["release_date"] == hd.isoformat() and listing_date != hd.isoformat()
            else:  # corrected-only: the ESMIS (visibility) date
                assert r["release_date"] == listing_date
        assert hd.weekday() < 5, (r, hd)
    assert nos == list(TXT_NUMBERS)


def test_txt_flags(releases):
    flags = {r["release_date"]: r["revision_flag"] for r in txt_releases(releases) if r["revision_flag"] != "v1"}
    # corrected copies: marker in the file head (general detector) or file name _revision; originals not in ESMIS
    assert flags == {d: "v1_corrected_only" for d in
                     ("1995-09-12", "1996-03-14", "1998-05-12", "1998-11-10", "2006-03-13", "2006-06-09")} | {"2008-10-28": "v1_reissue"}


def test_txt_selection_log():
    with (DATA / "txt_selection.csv").open(newline="") as f:
        log = list(csv.DictReader(f))
    prim = [r for r in log if r["role"] == "primary"]
    assert len(prim) == 186 and len({r["wasde_no"] for r in prim}) == 186
    assert {r["fname"]: r["role"] for r in log if r["listing_date"] in ("2008-10-28", "2008-12-12")} == {
        "wasde-10-28-2008.txt": "reissue", "wasde-12-12-2008.txt": "repost"}
    ex = {(r["listing_date"], r["fname"]) for r in log if r["role"] == "excluded"}
    # repeated / replacement files are logged, never emitted
    assert ("2006-06-09", "wasde-06-09-2006_revision.txt") in ex and ("2009-09-14", "wasde-09-14-2009.txt") in ex
    # 1995: one full report per month; the small cotton/narrative entries are excluded
    p95 = [r for r in prim if r["listing_date"].startswith("1995")]
    assert len(p95) == 12 and all(r["wasde_no"] == str(298 + i) for i, r in enumerate(p95))


def test_txt_spot_values_match_printed_tables(store):
    """Values read by hand from the printed tables (checks/items-A.md 1)."""
    def v(series, d, ref):
        rows = [r for r in store if r["series"] == f"wasde.us_corn.{series}" and r["release_date"] == d
                and r["reference_period"] == ref and r["revision_flag"] != "v2_repost"]
        assert len(rows) == 1
        return float(rows[0]["value"])
    assert (v("yield", "1996-08-12", "1996/97"), v("production", "1996-08-12", "1996/97")) == (118.7, 8695.0)
    assert (v("yield", "2005-08-12", "2005/06"), v("production", "2005-08-12", "2005/06")) == (139.2, 10350.0)
    assert (v("yield", "2009-01-12", "2008/09"), v("production", "2009-01-12", "2008/09")) == (153.9, 12101.0)
    assert v("ending_stocks", "1995-04-11", "1992/93") == 2113.0


# ---- parser fails loudly on unrecognised layouts --------------------------------------------------------------

def _text(name="1996-08-12"):
    p = next(Path(ROOT / "data/raw/wasde").glob(f"{name}/*/wasde-*.txt"))
    return p.read_bytes().decode("latin-1")


@pytest.mark.parametrize("mut", [
    lambda t: t.replace("Ending stocks, total ", "Ending stocks, tot.  "),
    lambda t: t.replace("Yield per harvested", "Yield per harv.     "),
    lambda t: t.replace("Item", "Itm "),
    lambda t: re.sub(r"(Production +:)( +[\d,]+)", r"\1", t),
])
def test_parser_raises_on_changed_layout(mut):
    t = _text()
    parse_corn_txt(t, 8)  # control
    with pytest.raises(ValueError):
        parse_corn_txt(mut(t), 8)


def test_parser_raises_on_wrong_month():
    with pytest.raises(ValueError, match="column order"):
        parse_corn_txt(_text(), 9)


# ---- cross-vintage column check + production = yield x area --------------------------------------------------

def _prev_cols(rel):
    """previous-month column (the figures a report repeats from the last one) of the projection year, from the raw file."""
    path = ROOT / rel["file"]
    if path.suffix == ".xls":
        return None  # 2013-07-11 ORIG xls: no previous-month parser
    if path.suffix == ".txt":
        text = path.read_bytes().decode("latin-1")
        return parse_corn_txt(text, parse_header(text)[1].month, prev=True)
    return parse_corn(path.read_bytes(), prev=True)


# Documented exceptions: (this release, ref, series) where the repeated column legitimately differs from the last
# vintage's current column. Filled from the findings printed by test_cross_vintage_columns (see F4b report).
CROSS_VINTAGE_EXCEPTIONS: dict[tuple[str, str, str], str] = {}


def cross_vintage_diffs(store, releases):
    cur = collections.defaultdict(dict)
    for r in store:
        if r["revision_flag"] != "v2_repost":
            cur[r["release_date"]][(r["series"], r["reference_period"])] = float(r["value"])
    rels = sorted(releases, key=lambda r: r["release_date"])
    diffs, n = [], 0
    for a, b in zip(rels, rels[1:]):
        pv = _prev_cols(b)
        for row in pv or []:
            key = (row["series"], row["reference_period"])
            if key in cur[a["release_date"]]:
                n += 1
                if cur[a["release_date"]][key] != row["value"]:
                    diffs.append((b["release_date"], row["reference_period"], row["series"].rsplit(".", 1)[1],
                                  cur[a["release_date"]][key], row["value"], a["release_date"]))
    return n, diffs


def test_cross_vintage_columns(store, releases):
    n, diffs = cross_vintage_diffs(store, releases)
    print(f"cross-vintage: {n} line comparisons, {len(diffs)} differences")
    for d in diffs:
        print("   ", d)
    unexplained = [d for d in diffs if d[:3] not in {(k[0], k[1], k[2]) for k in CROSS_VINTAGE_EXCEPTIONS}]
    assert n > 3000 and not unexplained, unexplained[:10]


def test_production_equals_yield_times_area(store):
    """production ~= yield x harvested area within the rounding of the printed inputs (yield 0.1 bu/ac, area 0.1 M ac,
    production 1 M bu)."""
    g = vintages(store)
    n, bad = 0, []
    for k, v in sorted(g.items()):
        if {"yield", "area_harvested", "production"} <= v.keys():
            n += 1
            tol = 0.05 * v["yield"] + 0.05 * v["area_harvested"] + 0.5 + 1e-9
            if abs(v["yield"] * v["area_harvested"] - v["production"]) > tol:
                bad.append((k, v["yield"], v["area_harvested"], v["production"]))
    print(f"yield x area: {n} checks, {len(bad)} failures")
    assert n > 1000 and not bad, bad[:10]


def test_cross_vintage_check_is_armed(store, releases):
    bumped = [{**r, "value": repr(float(r["value"]) + 1)} if (r["release_date"] == "2005-07-12" and r["series"].endswith("production")
              and r["reference_period"] == "2005/06" and r["revision_flag"] == "v1") else r for r in store]
    _, diffs = cross_vintage_diffs(bumped, releases)
    assert [(d[0], d[2]) for d in diffs] == [("2005-08-12", "production")]


def test_cross_vintage_covers_both_eras(store, releases):
    txt = [r for r in releases if r["release_date"] < XML_FROM]
    n, _ = cross_vintage_diffs(store, txt)
    assert n > 1000
    n_all, _ = cross_vintage_diffs(store, releases)
    assert n_all - n > 1000


# ==== F4c: NASS series (Crop Progress, Crop Production, Annual Summary) ==============================================

@pytest.fixture(scope="module")
def nass_rel():
    with (DATA / "nass_releases.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def _val(store, series, ref, d):
    rows = [r for r in store if r["series"] == series and r["reference_period"] == ref and r["release_date"] == d
            and r["revision_flag"] != "v2_repost"]
    assert len(rows) == 1, (series, ref, d, len(rows))
    return float(rows[0]["value"]), rows[0]["revision_flag"]


# Documented exceptions to WASDE == Crop Production on a joint date: (date, line) -> cause.
WASDE_CP_EXCEPTIONS: dict = {}  # none since the corrected-only 2008-10-10 file is dated at its documented reissue, 2008-10-28 (PREREG-AMENDMENTS.md)


def wasde_cp_same_day(store):
    """Every date (Aug-Nov, 1995-2026) with both a WASDE and a Crop Production corn release: WASDE US corn yield ==
    Crop Production national yield; production within rounding (WASDE prints integer million bu, CP 1,000 bu)."""
    n, bad, dates = 0, [], sorted({r["release_date"] for r in store if r["series"] == "crop_production.us_corn.yield"})
    for d in dates:
        ref = f"{d[:4]}/{str(int(d[:4]) + 1)[2:]}"
        wy, _ = _val(store, "wasde.us_corn.yield", ref, d)
        wp, _ = _val(store, "wasde.us_corn.production", ref, d)
        cy, _ = _val(store, "crop_production.us_corn.yield", ref, d)
        cp, _ = _val(store, "crop_production.us_corn.production_kbu", ref, d)
        n += 2
        if wy != cy:
            bad.append((d, "yield", wy, cy))
        if abs(wp - cp / 1000) > 0.5 + 1e-9:
            bad.append((d, "production", wp, cp / 1000))
    return n, dates, bad


def test_wasde_equals_crop_production_same_day(store):
    n, dates, bad = wasde_cp_same_day(store)
    print(f"WASDE vs Crop Production: {len(dates)} joint dates, {n} line comparisons, {len(bad)} exceptions")
    for b in bad:
        print("   exception:", b, "->", WASDE_CP_EXCEPTIONS.get(b[:2]))
    assert len(dates) == 125  # (2008-10-28 replaces 2008-10-10) Aug 32 + Sep 32 + Oct 30 + Nov 31 (no Oct 2013, Oct 2025 reports)
    assert {d[5:7] for d in dates} == {"08", "09", "10", "11"}
    assert {b[:2] for b in bad} == set(WASDE_CP_EXCEPTIONS), bad


def test_wasde_cp_check_is_armed(store):
    bumped = [{**r, "value": repr(float(r["value"]) + 0.1)} if (r["series"] == "crop_production.us_corn.yield" and r["release_date"] == "2015-08-12") else r
              for r in store]
    _, _, bad = wasde_cp_same_day(bumped)
    assert ("2015-08-12", "yield") in {b[:2] for b in bad}


def test_crop_production_2008_10_10_is_the_oct_28_vintage(store, releases):
    """The 2008-10-10 Crop Production exists only as `_revision` (corrected Oct 28 numbers): flagged corrected-only, stamped at
    its documented reissue date 2008-10-28, and equal to the WASDE reissue of that day, not to the Oct 10 WASDE."""
    cy, fl = _val(store, "crop_production.us_corn.yield", "2008/09", "2008-10-28")
    cp, _ = _val(store, "crop_production.us_corn.production_kbu", "2008/09", "2008-10-28")
    assert fl == "v1_corrected_only"
    assert not [r for r in store if r["series"].startswith("crop_production.") and r["release_date"] == "2008-10-10"]
    assert _val(store, "wasde.us_corn.yield", "2008/09", "2008-10-28")[0] == cy == 153.9
    assert round(cp / 1000) == _val(store, "wasde.us_corn.production", "2008/09", "2008-10-28")[0] == 12033
    assert _val(store, "wasde.us_corn.yield", "2008/09", "2008-10-10")[0] == 154.0


def test_annual_summary_equals_january_wasde(store):
    """Annual Summary (each January; 8 Feb 2019) national corn yield/production == the same day's WASDE first-final line,
    and the Summary's release date is a WASDE release date (the first-final truth of STRATEGY 5.1)."""
    summ = sorted({r["release_date"] for r in store if r["series"] == "crop_production_summary.us_corn.yield"})
    assert len(summ) == 32 and summ[0] == "1995-01-12" and summ[-1] == "2026-01-12" and "2015-02-05" in summ and "2015-01-12" not in summ
    wasde_dates = {r["release_date"] for r in store if r["series"] == "wasde.us_corn.yield"}
    bad = []
    for d in summ:
        # the 2015 Summary exists in ESMIS only as `_revision`, stamped at its printed reissue (2015-02-05); the January WASDE
        # it must equal is the one of the archived date 2015-01-12 (forage corrections; corn untouched)
        w = "2015-01-12" if d == "2015-02-05" else d
        assert w in wasde_dates, f"Annual Summary date {d} is not a WASDE date"
        ref = f"{int(d[:4]) - 1}/{d[2:4]}"
        sy, _ = _val(store, "crop_production_summary.us_corn.yield", ref, d)
        sp, _ = _val(store, "crop_production_summary.us_corn.production_kbu", ref, d)
        wy, _ = _val(store, "wasde.us_corn.yield", ref, w)
        wp, _ = _val(store, "wasde.us_corn.production", ref, w)
        if sy != wy or abs(sp / 1000 - wp) > 0.5 + 1e-9:
            bad.append((d, sy, wy, sp / 1000, wp))
    print(f"Annual Summary vs January WASDE: {len(summ)} years, {len(bad)} mismatches {bad}")
    assert not bad, bad


def test_crop_production_reliability_rows(store):
    """The values F5 needs: printed RMSE (percent of production), 90% interval and the below/above counts (RESULTS item 3)."""
    def rel(d, k):
        return _val(store, f"crop_production.us_corn.rel_{k}", f"{d[:4]}/{str(int(d[:4]) + 1)[2:]}", d)[0]
    assert (rel("2015-08-12", "rmse_pct"), rel("2015-08-12", "ci90_pct"), rel("2015-08-12", "n_below"), rel("2015-08-12", "n_above")) == (4.3, 7.5, 10, 10)
    assert (rel("2025-08-12", "rmse_pct"), rel("2025-08-12", "ci90_pct"), rel("2025-08-12", "n_below"), rel("2025-08-12", "n_above")) == (3.8, 6.5, 7, 13)
    dates = sorted({r["release_date"] for r in store if r["series"] == "crop_production.us_corn.rel_rmse_pct"})
    assert len(dates) == 125 and {d[5:7] for d in dates} == {"08", "09", "10", "11"}
    for d in dates:  # window = 20 years (10 in the 1995-98 reports) minus the years without a forecast that month
        n = rel(d, "n_below") + rel(d, "n_above")
        assert 9 <= n <= 20, d
        if d[5:7] == "10" and "2014" <= d[:4] <= "2024":
            assert n == 19, d  # no October 2013 forecast (shutdown) inside the window
    assert rel("2026-10-09", "n_below") + rel("2026-10-09", "n_above") == 18  # no Oct 2013 and no Oct 2025 forecast


# ---- Crop Progress -------------------------------------------------------------------------------------------------

EXPECTED_NASS_COUNTS = {("crop_production", "v1"): 375, ("crop_production", "v1_corrected_only"): 4,
                        ("crop_production_summary", "v1"): 31, ("crop_production_summary", "v1_corrected_only"): 1,
                        ("crop_progress", "v1"): 1103, ("crop_progress", "v1_corrected_only"): 4}
# printed 'Released' dates on a Wednesday: 2012-10-31 (the Oct 29 report delayed by the Sandy closures), 2017-07-05 (July 4 week)
EXPECTED_CP_OFF_DAYS = [("2012-10-31", "Wed"), ("2017-07-05", "Wed")]


def _cp_files(nass_rel):
    return [r for r in nass_rel if r["series_root"] == "crop_progress"]


def test_crop_progress_week_ending_and_prev_week(store, nass_rel):
    """reference_period = the printed week-ending Sunday; this week's 'previous week' line equals last week's published line."""
    cp = [r for r in store if r["series"] == "crop_progress.us_corn.good_excellent"]
    byweek = {r["reference_period"]: float(r["value"]) for r in cp}
    assert len(byweek) == len(cp)
    n, diffs = 0, []
    for r in cp:
        text = (ROOT / r["source_file"]).read_bytes().decode("latin-1")
        p = parse_progress(text)
        assert p["good_excellent"] == float(r["value"])
        we = date.fromisoformat(r["reference_period"])
        assert we.weekday() == 6 and 0 < (date.fromisoformat(r["release_date"]) - we).days <= 3, r
        if p["week_ending"] is not None:
            assert p["week_ending"] == we
        pw = (we - timedelta(days=7)).isoformat()
        if p["prev_good_excellent"] is not None and pw in byweek:
            n += 1
            if byweek[pw] != p["prev_good_excellent"]:
                diffs.append((r["release_date"], pw, byweek[pw], p["prev_good_excellent"]))
    print(f"Crop Progress: {len(cp)} corn G/E rows, {n} previous-week comparisons, {len(diffs)} differences {diffs}")
    assert n > 600 and not diffs


def test_crop_progress_seasons(store):
    cp = [r for r in store if r["series"] == "crop_progress.us_corn.good_excellent"]
    per_year = collections.Counter(r["reference_period"][:4] for r in cp)
    assert set(per_year) == {str(y) for y in range(1995, 2027)}
    assert all(14 <= v <= 30 for v in per_year.values()), per_year
    assert all(0 < float(r["value"]) <= 100 for r in cp)
    assert all(r["release_ts"].endswith(("-04:00", "-05:00")) and "T16:00:00" in r["release_ts"] for r in cp)


def test_crop_progress_corrected_only_and_exceptions(nass_rel):
    flagged = {r["release_date"] for r in nass_rel if r["series_root"] == "crop_progress" and r["revision_flag"] == "v1_corrected_only"}
    assert flagged == {"1997-06-24", "2008-07-28", "2015-08-04", "2015-11-24"}
    flagged = {r["release_date"] for r in nass_rel if r["series_root"] == "crop_production" and r["revision_flag"] == "v1_corrected_only"}
    assert flagged == {"2003-08-12", "2008-08-12", "2008-10-28", "2010-01-12"}
    flagged = {r["release_date"] for r in nass_rel if r["series_root"] == "crop_production_summary" and r["revision_flag"] == "v1_corrected_only"}
    assert flagged == {"2015-02-05"}


def test_nass_release_counts(nass_rel):
    c = collections.Counter((r["series_root"], r["revision_flag"]) for r in nass_rel)
    print("NASS releases per series and flag:", dict(c))
    assert dict(c) == EXPECTED_NASS_COUNTS
    sel = list(csv.DictReader((DATA / "nass_selection.csv").open(newline="")))
    prim = collections.Counter(r["ident"] for r in sel if r["role"] == "primary")
    assert prim == {"CropProg": 1107, "CropProd": 379, "CropProdSu": 32}
    for r in sel:
        assert r["role"] in ("primary", "excluded")


def test_crop_progress_off_days_are_logged(nass_rel):
    """Crop Progress is released Monday (Tuesday after a Monday holiday); every other weekday is a logged exception."""
    off = sorted((r["release_date"], date.fromisoformat(r["release_date"]).strftime("%a")) for r in _cp_files(nass_rel)
                 if date.fromisoformat(r["release_date"]).weekday() not in (0, 1))
    print("Crop Progress releases not on Mon/Tue:", off)
    assert off == EXPECTED_CP_OFF_DAYS


def test_crop_progress_time_statement_every_era():
    """SEV3 (d): the 16:00 ET release time is stated in the report text of every year 1995-2026 (all primary files scanned);
    the only file naming another weekly time for Crop Progress is the 1996-07-29 pre-announcement of 3:00 p.m.; the
    12:00 p.m. statements of 2002-2010 belong to the Weekly Weather and Crop Bulletin."""
    with (DATA / "nass_selection.csv").open(newline="") as f:
        prim = [r for r in csv.DictReader(f) if r["ident"] == "CropProg" and r["role"] == "primary"]
    rx = re.compile(r"(?:(?:are|will be|be)\s+released|released)\s+(?:at|after)\s+(\d{1,2}(?::\d\d)?\s*(?:p\.?m\.?|a\.?m\.?)|noon)", re.I)
    years, other = collections.defaultdict(int), []
    for r in prim:
        path = next((ROOT / "data/raw/crop_progress").glob(f"{r['listing_date']}/*/{r['fname']}"))
        text = " ".join(path.read_bytes().decode("latin-1").split())
        for m in rx.finditer(text):
            t = re.sub(r"[ .]", "", m.group(1).lower())
            ctx = text[max(0, m.start() - 90):m.end()]
            if t in ("4pm", "4:00pm"):
                years[r["listing_date"][:4]] += 1
            elif "Bulletin" not in ctx:
                other.append((r["listing_date"], t))
    assert set(years) == {str(y) for y in range(1995, 2027)}, sorted(years)
    assert sorted(other) == [("1996-07-29", "3:00pm"), ("1997-06-24", "9:00am")]  # pre-announcement; the 9:00 a.m. update of a corrected file


# ---- the release-time table and the reissue note ---------------------------------------------------------------

def test_release_time_table_notes():
    with (DATA / "release_times.csv").open(newline="") as f:
        tab = list(csv.DictReader(f))
    cp = [r for r in tab if r["series"] == "crop_progress"]
    assert len(cp) == 1 and cp[0]["local_time"] == "16:00" and cp[0]["valid_to"] == "9999-12-31"
    assert "4 p.m." in cp[0]["source"] and "2026" in cp[0]["source"] and "2005" in cp[0]["source"] and "UNVERIFIED" not in cp[0]["source"]
    assert {r["series"] for r in tab} >= {"crop_production_summary"}
    wasde = next(r for r in tab if r["series"] == "wasde" and r["valid_from"] == "1995-01-01")
    assert "2008-10-28" in wasde["source"] and "UNVERIFIED" in wasde["source"]


def test_reissue_time_is_flagged_unverified_in_the_log():
    with (DATA / "txt_selection.csv").open(newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["role"] == "reissue")
    assert row["listing_date"] == "2008-10-28" and "UNVERIFIED" in row["reason"] and "08:30" in row["reason"]


# ---- 20 hand-read PDF spot checks ----------------------------------------------------------------------------------

def spot_rows():
    with (DATA / "spot_checks.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def test_spot_checks_20_of_20(store):
    """Each row: a value read by eye from the ESMIS PDF page (see data/spot_checks.csv, pdf sha256 + page) must equal the store."""
    rows = spot_rows()
    assert len(rows) == 20
    roots = collections.Counter(r["series"].split(".")[0] for r in rows)
    assert roots["wasde"] >= 10 and roots["crop_production"] >= 2 and roots["crop_progress"] >= 1
    assert sum(r["release_date"] < "2010-07-09" and r["series"].startswith("wasde") for r in rows) >= 5  # TXT era
    assert sum(r["release_date"] >= "2010-07-09" and r["series"].startswith("wasde") for r in rows) >= 5  # XML era
    assert any(r["release_date"].startswith("1995") for r in rows) and any(r["release_date"].startswith("2025") for r in rows)
    bad = []
    for r in rows:
        ref = r["marketing_year"]
        d = r["release_date"]
        found = [x for x in store if x["series"] == r["series"] and x["reference_period"] == ref and x["release_date"] == d
                 and x["revision_flag"] != "v2_repost"]
        assert len(found) == 1, r
        if float(found[0]["value"]) != float(r["value_read"]):
            bad.append((d, r["series"], ref, found[0]["value"], r["value_read"]))
        assert re.fullmatch(r"[0-9a-f]{64}", r["pdf_sha256"]) and r["pdf_url"].endswith(".pdf") and int(r["page"]) >= 1
    print(f"spot checks: {len(rows)} read by eye, {len(rows) - len(bad)} match the store")
    assert not bad, bad


def test_spot_check_pdfs_are_the_manifest_files():
    """The PDFs behind the spot checks are in the fetch manifest with the recorded hash and (when present) on disk unchanged."""
    from cfstore.fetch import read_manifest
    man = read_manifest()
    for r in spot_rows():
        m = man[r["pdf_url"]]
        assert m["sha256"] == r["pdf_sha256"]
        path = ROOT / m["local_path"]
        if path.exists():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == r["pdf_sha256"]


# ---- parsers fail loudly -------------------------------------------------------------------------------------------

def _raw(sub, d):
    return next((ROOT / "data/raw" / sub).glob(f"{d}/*/*.txt")).read_bytes().decode("latin-1")


def test_crop_progress_parser_raises_on_changed_layout():
    t = _raw("crop_progress", "2026-10-13")
    assert parse_progress(t)["good_excellent"] == 56.0
    with pytest.raises(ValueError):
        parse_progress(t.replace("18 States ......", "18 Stat  ......"))
    with pytest.raises(ValueError):  # a row that does not sum to ~100
        parse_progress(t.replace("18 States ......:      5          12          27          44          12",
                                 "18 States ......:      5          12          27          44          52"))


def test_mid_season_report_without_corn_condition_fails_the_build():
    """A renamed corn condition title would parse to None (April-May reports legitimately have none); mid-season reports
    (15 Jun - 30 Sep) must carry it, so the ingest raises."""
    from cfstore.nass import parse_release
    path = next((ROOT / "data/raw/crop_progress").glob("2025-08-11/*/*.txt"))
    text = path.read_bytes().decode("latin-1")
    p = {"release_date": "2025-08-11", "text": text, "fname": path.name}
    assert len(parse_release("CropProg", p)) == 1
    with pytest.raises(ValueError):
        parse_release("CropProg", {**p, "text": text.replace("Corn Condition - Selected States", "Corn Cond. - Selected States")})
    may = next((ROOT / "data/raw/crop_progress").glob("2025-05-12/*/*.txt"))
    assert parse_release("CropProg", {"release_date": "2025-05-12", "text": may.read_bytes().decode("latin-1"), "fname": may.name}) == []


def test_crop_production_parser_raises_on_changed_layout():
    t = _raw("crop_production", "2015-08-12")
    assert parse_corn_table(t)["yield"] == 168.8 and parse_reliability(t, 8)["rmse_pct"] == 4.3
    with pytest.raises(ValueError):
        parse_reliability(t.replace("Corn for grain ........bushels:", "Corn grain ............bushels:"), 8)
    lines = t.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("United States") and "168.8" in ln)
    lines[i] = lines[i].replace("168.8", "99.9")
    with pytest.raises(ValueError, match="yield x area"):  # a column mix-up is caught by yield x area == production
        parse_corn_table("\n".join(lines))
