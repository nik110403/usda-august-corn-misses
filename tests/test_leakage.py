"""Leakage tests 1-4 (STRATEGY section 7). Needs data/store.csv and out/: run `make store` first."""
import csv
import re
from fractions import Fraction
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from cfstore.asof import LeakageError, assert_no_leak, build_features, date_level_select, info_set, is_own_release, origins, ts
from cfstore.fetch import DATA, ROOT
from cfstore.store import read_store
from cfstore.overlap import OUT, crop_progress_dates, overlap_rows, summary
from cfstore.skeleton import truth_rows
from cfstore.times import release_ts
from cfstore.trend import assert_finals_before, finals_before, first_finals, fit_trend, trend_at


@pytest.fixture(scope="module")
def store():
    return read_store()


@pytest.fixture(scope="module")
def releases():
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def _cp_row(d: str, hh: int, mm: int) -> dict:
    t = datetime.combine(date.fromisoformat(d), time(hh, mm), tzinfo=ZoneInfo("America/New_York"))
    return {"series": "crop_progress.us_corn.good_excellent", "reference_period": "synthetic",
            "release_date": d, "release_ts": t.isoformat(), "time_source": "planted", "source_file": "planted",
            "file_hash": "", "value": "70", "revision_flag": "planted"}


def test_origins_are_regular_may_nov(releases):
    os_ = origins(releases)
    assert {date.fromisoformat(o["release_date"]).month for o in os_} <= set(range(5, 12))
    assert len({o["release_date"][:7] for o in os_}) == len(os_)  # one per month, no reissues
    assert "2025-10" not in {o["release_date"][:7] for o in os_}  # shutdown: no origin, none imputed


def test1_every_real_origin_passes_guard(store, releases):
    for o in origins(releases):
        feats = build_features(store, o)  # asserts internally
        assert feats and all(ts(r) < ts(o) or r["release_date"] == o["release_date"] for r in feats)


def test1_origin_sweep_covers_txt_era(store, releases):
    os_ = origins(releases)
    txt = [o for o in os_ if o["release_date"] < "2010-07-01"]
    assert len(txt) == 15 * 7 + 2  # May-Nov 1995-2009 plus May, Jun 2010
    assert {o["release_date"][:4] for o in txt} == {str(y) for y in range(1995, 2011)}
    for o in txt:  # every TXT-era origin passes the guard, sees only its own file and strictly earlier rows
        feats = build_features(store, o)
        own = [r for r in feats if r["release_date"] == o["release_date"] and r["series"].startswith("wasde.")]
        joint = [r for r in feats if r["release_date"] == o["release_date"] and not r["series"].startswith("wasde.")]
        assert own and all(r["source_file"] == o["file"] for r in own)
        assert all(r["series"].startswith("crop_production.") for r in joint)  # joint CP only
        assert all(r["release_date"] < o["release_date"] for r in feats if r not in own and r not in joint)


def test1_assertion_fires_on_later_row(store, releases):
    o = origins(releases)[0]
    later = [r for r in store if r["release_date"] > o["release_date"]][:1]
    with pytest.raises(LeakageError):
        assert_no_leak(later, o)


def _row(series: str, d: str, t: datetime) -> dict:
    return {**_cp_row(d, 0, 0), "series": series, "release_ts": t.isoformat()}


def _origin(releases, d="2025-05-12"):
    o = next(o for o in origins(releases) if o["release_date"] == d)
    assert date.fromisoformat(d).weekday() == 0 and ts(o).hour == 12  # real Monday WASDE origin, noon ET
    return o


def test2_guard_is_armed_careless_selector(store, releases):
    """Mutation test: a careless date-level join lets the 16:00 same-day row through; the guard must fail the build."""
    o = _origin(releases)
    leak = _cp_row(o["release_date"], 16, 0)
    assert leak["release_ts"] == release_ts("crop_progress", date.fromisoformat(o["release_date"]))[0].isoformat()
    with pytest.raises(LeakageError):
        build_features(store + [leak], o, select=date_level_select)


def test2_same_day_rows_excluded_by_strict_build(store, releases):
    o = _origin(releases)
    d = o["release_date"]
    leak16 = _cp_row(d, 16, 0)
    placeholder = _row("crop_progress.us_corn.good_excellent", d, datetime(2025, 5, 12, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert placeholder["release_ts"].startswith("2025-05-12T12:00:00+00:00")  # = 08:00 ET, before origin_ts
    gs = _row("grain_stocks.us_corn.stocks", d, datetime(2025, 5, 12, 11, 59, tzinfo=ZoneInfo("America/New_York")))
    base = build_features(store, o)
    for row in (leak16, placeholder, gs):
        assert row not in info_set(store + [row], o)  # excluded by the selector (same-day rule)
        assert build_features(store + [row], o) == base  # strict build: excluded, no failure
        with pytest.raises(LeakageError):  # forced into the feature set, the guard raises
            assert_no_leak(base + [row], o)
        with pytest.raises(LeakageError):
            build_features(store + [row], o, select=date_level_select)


def test2_previous_day_rows(store, releases):
    o = _origin(releases)
    ok159 = _cp_row("2025-05-11", 15, 59)
    ok1600 = _cp_row("2025-05-11", 16, 0)  # table-consistent previous-day row
    assert ok159 in info_set(store + [ok159], o)  # selector admits the 15:59 previous-day row
    with pytest.raises(LeakageError):  # ...but its stamp disagrees with the release-time table, so the guard rejects it
        assert_no_leak([ok159], o)
    feats = build_features(store + [ok1600], o)
    assert ok1600 in feats


def test_own_release_matches_first_release_file(store, releases):
    o = _origin(releases)
    own = [r for r in store if r["release_date"] == o["release_date"] and r["series"].startswith("wasde.")]
    assert own and all(r["source_file"] == o["file"] for r in own)
    repost = {**own[0], "source_file": "data/raw/other_v2.xml", "revision_flag": "v2_repost"}
    assert repost not in info_set(store + [repost], o)
    other = {**own[0], "source_file": "data/raw/other.xml"}
    assert other not in info_set(store + [other], o)
    with pytest.raises(LeakageError):
        assert_no_leak([repost], o)


def test_v2_only_dates_flagged_and_2013_07_11_primary_is_orig_xls(store, releases):
    flagged = {r["release_date"] for r in releases if r["revision_flag"] == "v1_corrected_only"}
    assert flagged == {"1995-09-12", "1996-03-14", "1998-05-12", "1998-11-10", "2006-03-13", "2006-06-09", "2024-12-10", "2025-04-10", "2025-05-12", "2026-05-12", "2026-06-11"}
    jul = [r for r in store if r["release_date"] == "2013-07-11"]
    prim = [r for r in jul if r["revision_flag"] == "v1"]
    rep = [r for r in jul if r["revision_flag"] == "v2_repost"]
    assert prim and rep and len(prim) == len(rep) == 27
    assert all(r["source_file"].endswith("ORIGwasde-07-11-2013.xls") for r in prim)
    o = next(o for o in origins(releases) if o["release_date"] == "2013-08-12")
    feats = build_features(store, o)
    assert not any(r["revision_flag"] == "v2_repost" for r in feats)
    assert any(r["release_date"] == "2013-07-11" for r in feats)


def test_guard_exemption_requires_same_ts_mutation(store, releases):
    """SEV3(a): a same-date row of the origin's own series is exempt only if its release_ts equals the origin's.
    On the old guard (is_own_release only) the row below slips through; the independent exemption rejects it."""
    o = _origin(releases)
    own = next(r for r in store if r["release_date"] == o["release_date"] and r["source_file"] == o["file"])
    assert ts(own) == ts(o)
    assert_no_leak([own], o)  # control: genuine own row passes
    o_early = {**o, "release_ts": datetime(2025, 5, 12, 11, 0, tzinfo=ZoneInfo("America/New_York")).isoformat()}
    assert is_own_release(own, o_early)  # the selector's helper still exempts it (unchanged)
    with pytest.raises(LeakageError):  # same date, own file, table-consistent ts, but ts != origin ts
        assert_no_leak([own], o_early)


# ---- N1: origins() chooses by revision_flag, never by date order -------------------------------------------------

def _rel(d: str, flag: str) -> dict:
    t = datetime.combine(date.fromisoformat(d), time(8, 30), tzinfo=ZoneInfo("America/New_York"))
    return {"release_date": d, "release_ts": t.isoformat(), "file": f"synthetic/{d}.txt", "revision_flag": flag, "n_values": "27"}


def test_origins_ignore_reissue_and_repost_by_flag():
    rows = [_rel("2020-05-12", "v1"),
            _rel("2020-06-19", "v1_reissue"),  # a reissue in a month with no primary: that month has no origin
            _rel("2020-07-10", "v1_corrected_only"), _rel("2020-07-09", "v1"),  # original dated AFTER... see below
            _rel("2020-08-05", "v1_reissue"), _rel("2020-08-12", "v1"),  # reissue dated before the primary
            _rel("2020-09-11", "v2_repost"), _rel("2020-09-14", "v1_corrected_only"),
            _rel("2020-10-09", "v1_corrected_only"), _rel("2020-10-13", "v1")]  # corrected copy dated before its original
    got = {o["release_date"][:7]: o for o in origins(rows)}
    assert "2020-06" not in got
    assert got["2020-05"]["release_date"] == "2020-05-12"
    assert got["2020-07"]["revision_flag"] == "v1" and got["2020-07"]["release_date"] == "2020-07-09"
    assert got["2020-08"]["release_date"] == "2020-08-12"  # not the earlier reissue
    assert got["2020-09"]["release_date"] == "2020-09-14"  # the repost is never an origin; corrected-only is
    assert got["2020-10"]["release_date"] == "2020-10-13"  # the original v1 wins over an earlier-dated corrected copy
    assert all(o["revision_flag"] in ("v1", "v1_corrected_only") for o in got.values())
    assert list(got) == sorted(got)
    assert origins(list(reversed(rows))) == origins(rows)  # independent of input order
    with pytest.raises(ValueError):
        origins([_rel("2020-05-12", "v3_mystery")])


def test_real_origins_all_primary(releases):
    os_ = origins(releases)
    assert all(o["revision_flag"] in ("v1", "v1_corrected_only") for o in os_)
    assert not [o for o in os_ if o["release_date"] == "2008-10-28"]


# ---- Crop Production corrected-only vintage is never the origin's joint release ---------------------------------

def test_crop_production_2008_10_10_is_corrected_only_never_an_oct10_vintage(store, releases):
    o = next(o for o in origins(releases) if o["release_date"] == "2008-10-10")
    cp = [r for r in store if r["series"].startswith("crop_production.") and r["release_date"] == "2008-10-28"]
    assert cp and {r["revision_flag"] for r in cp} == {"v1_corrected_only"}
    assert all("_revision" in r["source_file"] for r in cp)
    vals = {r["series"].split(".")[-1]: float(r["value"]) for r in cp}
    assert vals["yield"] == 153.9 and round(vals["production_kbu"] / 1000) == 12033  # the Oct 28 numbers ...
    assert vals["yield"] != 154.0 and round(vals["production_kbu"] / 1000) != 12200  # ... not the Oct 10 vintage (154.0, 12,200)
    assert not any(r in info_set(store, o) for r in cp)  # stamped 2008-10-28 (documented reissue), after the Oct-10 origin
    assert not any(r in build_features(store, o) for r in cp)
    assert {r["release_date"] for r in cp} == {"2008-10-28"}
    with pytest.raises(LeakageError):
        assert_no_leak(cp, o)
    feats = build_features(store, o)
    assert not [r for r in feats if r["series"].startswith("crop_production.") and r["release_date"] > "2008-10-10"]
    assert not any(r["release_date"] == "2008-10-10" and r["series"].startswith("crop_production.") for r in feats)  # no Oct-10 vintage at all
    # a regular joint Crop Production release IS the origin's own information
    o25 = next(o for o in origins(releases) if o["release_date"] == "2025-08-12")
    cp25 = [r for r in store if r["series"].startswith("crop_production.") and r["release_date"] == "2025-08-12"]
    assert cp25 and {r["revision_flag"] for r in cp25} == {"v1"}
    assert all(r in info_set(store, o25) for r in cp25)
    assert_no_leak(cp25, o25)
    # later origins see it (Nov 2008 sees the corrected vintage; it was visible since Oct 28 anyway)
    nov = next(o for o in origins(releases) if o["release_date"] == "2008-11-10")
    assert all(r in info_set(store, nov) for r in cp)


# ---- Leakage test 3: the trend is fitted only on finals released before the origin --------------------------------

def test3_trend_sweep_every_origin(store, releases):
    n_checked = 0
    for o in origins(releases):
        y = int(o["release_date"][:4])
        finals = finals_before(store, o)
        if len(finals) < 5:
            with pytest.raises(ValueError):
                trend_at(store, o)  # PREREG 3: at least 5 finals required (origins before 2000)
            continue
        tr = trend_at(store, o)  # asserts the guard internally
        assert all(ts(row) < ts(o) for _, _, row in finals)
        assert min(tr.seasons) == 1995 and max(tr.seasons) == y - 1 and tr.n == y - 1995  # crops 1995..y-1, nothing newer
        assert all(row["release_date"][:4] == str(s + 1) and row["release_date"][5:7] in ("01", "02") for s, _, row in finals)
        own = truth_rows(store, y)  # the origin's own season: its first-final is released the next January
        if own is None:
            continue  # 2026 origins: the season is not final yet
        assert ts(own) > ts(o)
        leaky = finals + [(y, Fraction(own["value"]), own)]
        with pytest.raises(LeakageError):
            assert_finals_before(leaky, o)  # the guard is active
        tr2 = fit_trend(leaky)
        assert (tr2.intercept, tr2.slope) != (tr.intercept, tr.slope) and tr2.predict(y) != tr.predict(y)
        n_checked += 1
    assert n_checked >= 180  # 2000-2025 x 7 origins less the shutdown months


def test3_careless_selector_mutation(store, releases):
    """Mutation: a selector that takes every final in the store (the careless join) makes the guard fail and moves the fit."""
    o = next(o for o in origins(releases) if o["release_date"] == "2010-08-12")
    careless = first_finals(store)
    assert len(careless) > len(finals_before(store, o)) >= 5
    with pytest.raises(LeakageError):
        assert_finals_before(careless, o)
    assert fit_trend(careless).predict(2010) != trend_at(store, o).predict(2010)


def test3_trend_is_exact_ols_and_needs_two_points():
    pts = [(2000, Fraction(100), {}), (2001, Fraction(104), {}), (2002, Fraction(105), {})]
    t = fit_trend(pts)
    assert t.slope == Fraction(5, 2)
    assert t.predict(2001) == 103 and t.predict(2003) == 108  # passes through the means (2001, 103)
    with pytest.raises(ValueError):
        fit_trend(pts[:1])


# ---- Leakage test 4: origins on a Crop Progress release day -------------------------------------------------------

def raw_crop_progress_dates() -> set[str]:
    """Release dates of every Crop Progress report file on disk: printed 'Released' date; files without one, and corrected /
    updated re-releases, take the directory (ESMIS) date, except the documented 2015-08-03 -> 08-04 reissue. Notices (no
    'Week Ending' table) and byte-duplicate `_dup` copies are skipped."""
    from cfstore.parse_cp import released_date
    out = set()
    for path in (ROOT / "data/raw/crop_progress").glob("*/*/*.txt"):
        if path.parent.name.endswith("_dup"):
            continue
        text = path.read_bytes().decode("latin-1")
        if not re.search(r"Week\s+Ending", text, re.I):
            continue
        d = path.parts[-3]
        if "_correction" in path.name or "_revision" in path.name or d == "1997-06-24":
            out.add("2015-08-04" if d == "2015-08-03" else d)
            continue
        try:
            rel = released_date(text).isoformat()
        except ValueError:
            rel = d
        out.add(rel)
    return out


def test4_overlap_count_matches_recomputation(releases):
    with (OUT / "crop_progress_overlap.csv").open(newline="") as f:
        written = list(csv.DictReader(f))
    # independent recomputation from the raw files' printed release dates (not from nass.select() or its CSVs)
    cp = raw_crop_progress_dates()
    assert cp == crop_progress_dates()
    mine = [(o["release_date"], o["release_date"] in cp) for o in origins(releases)]
    assert [(r["origin_date"], bool(int(r["on_crop_progress_day"]))) for r in written] == mine
    s = summary(overlap_rows())
    txt = (OUT / "crop_progress_overlap_count.txt").read_text()
    for label, v in s.items():
        assert f"{label}: {v['overlap']} of {v['origins']} May-Nov WASDE origins" in txt
    assert s["1995-2025"]["overlap"] == sum(hit for d, hit in mine if d < "2026")
    print("overlap:", s)


# 215 regular May-Nov WASDE origins 1995-2025 (7 x 31 = 217 less the two shutdown months, Oct 2013 and Oct 2025); 24 of them
# (20 Mon, 4 Tue) coincide with a Crop Progress release. FEASIBILITY 5b said 29 of 231. The ESMIS WASDE listing holds 229
# distinct May-Nov dates in 1995-2025 (listing.csv; 5b's 231 is not reproducible, 2 higher); one origin (1995-08-11) is not a listing date, so 229 - 214 = 15 listing dates are non-origin
# entries (cotton/narrative extracts, the 2008-10-28 reissue, ...). 5b's 29 included 5 dates that are not the real
# release date of a regular WASDE (1995-08-14 = Aug 11 report, 1995-09-11 and 1995-10-10 = extracts, 2008-05-05 = extra
# listing, the real May WASDE is 2008-05-09, 2009-09-14 = extract; the real one is 2009-09-11). 29 - 5 = 24.
EXPECTED_OVERLAP = (215, 24)


def test4_overlap_vs_feasibility_5b(releases):
    """RESULTS item 5b counted 29 of 231 (24 Mon, 5 Tue) from ESMIS listing dates; the store counts real origins."""
    s = summary(overlap_rows())["1995-2025"]
    assert (s["origins"], s["overlap"]) == EXPECTED_OVERLAP, s
    assert (s["Mon"], s["Tue"]) == (20, 4)
    five_b = set("""1995-06-12 1995-08-14 1995-09-11 1995-10-10 1996-08-12 1996-11-12 1997-05-12 1997-11-10 1999-07-12 2002-08-12 2002-11-12
        2003-05-12 2004-07-12 2004-10-12 2005-09-12 2007-06-11 2008-05-05 2008-11-10 2009-09-14 2011-09-12 2013-08-12 2014-11-10 2016-09-12
        2019-08-12 2021-07-12 2021-10-12 2022-09-12 2024-08-12 2025-05-12""".split())
    mine = {r["origin_date"] for r in overlap_rows() if r["on_crop_progress_day"] and r["origin_date"] < "2026"}
    assert mine < five_b and five_b - mine == {"1995-08-14", "1995-09-11", "1995-10-10", "2008-05-05", "2009-09-14"}
    with (DATA / "listing.csv").open(newline="") as f:
        listing = {r["release_date"] for r in csv.DictReader(f) if "1995" <= r["release_date"][:4] <= "2025" and 5 <= int(r["release_date"][5:7]) <= 11}
    origin_dates = {r["origin_date"] for r in overlap_rows() if r["origin_date"] < "2026"}
    assert (len(listing), len(listing - origin_dates), len(origin_dates), sorted(origin_dates - listing)) == (229, 15, 215, ["1995-08-11"])
    assert not (five_b - mine) & origin_dates  # none of the five is a regular origin in the store


def test_corrected_only_without_documented_reissue_is_admitted_as_archived(store, releases):
    """PREREG 1: corrected-only files are used as archived. CropProd 2008-08-12 (`_revision`, sugarbeet correction, corn untouched)
    keeps its archived date and is the Aug-12-2008 origin's joint Crop Production release; so does CropProd 2003-08-12 (narrative note)."""
    for d in ("2008-08-12", "2003-08-12"):
        o = next(o for o in origins(releases) if o["release_date"] == d)
        cp = [r for r in store if r["series"].startswith("crop_production.") and r["release_date"] == d]
        assert cp and {r["revision_flag"] for r in cp} == {"v1_corrected_only"}
        assert all(r in info_set(store, o) for r in cp)
        assert all(r in build_features(store, o) for r in cp)  # guard passes (same date, same table time)


def test_crop_progress_2015_08_03_correction_is_dated_08_04(store):
    rows = [r for r in store if r["series"] == "crop_progress.us_corn.good_excellent" and r["source_file"].endswith("CropProg-08-03-2015_correction.txt")]
    assert len(rows) == 1 and rows[0]["release_date"] == "2015-08-04" and rows[0]["revision_flag"] == "v1_corrected_only"
    assert rows[0]["release_ts"].startswith("2015-08-04T16:00:00")
    # a query at 2015-08-03 16:00 ET (the origin of that day) does not see it
    q = {**rows[0], "release_date": "2015-08-03", "release_ts": "2015-08-03T16:00:00-04:00", "series": "wasde.us_corn.yield", "source_file": "q"}
    assert rows[0] not in info_set(store, q)
    nxt = {**q, "release_date": "2015-08-05", "release_ts": "2015-08-05T12:00:00-04:00"}
    assert rows[0] in info_set(store, nxt)
