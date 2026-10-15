"""Leakage tests 1 and 2 (STRATEGY section 7). Needs data/store.csv: run `make store` first."""
import csv
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from cfstore.asof import LeakageError, assert_no_leak, build_features, date_level_select, info_set, is_own_release, origins, ts
from cfstore.fetch import DATA
from cfstore.store import read_store
from cfstore.times import release_ts


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
        own = [r for r in feats if r["release_date"] == o["release_date"]]
        assert own and all(r["source_file"] == o["file"] for r in own)
        assert all(r["release_date"] < o["release_date"] for r in feats if r not in own)


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
    own = [r for r in store if r["release_date"] == o["release_date"]]
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
