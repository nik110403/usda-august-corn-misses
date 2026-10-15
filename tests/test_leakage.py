"""Leakage tests 1 and 2 (STRATEGY section 7). Needs data/store.csv: run `make store` first."""
import csv
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from cfstore.asof import LeakageError, assert_no_leak, build_features, date_level_select, info_set, origins, ts
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


def test1_assertion_fires_on_later_row(store, releases):
    o = origins(releases)[0]
    later = [r for r in store if r["release_date"] > o["release_date"]][:1]
    with pytest.raises(LeakageError):
        assert_no_leak(later, o)


def test2_planted_leak(store, releases):
    # a real Monday WASDE origin from the store (2025-05-12, noon ET)
    mondays = [o for o in origins(releases) if date.fromisoformat(o["release_date"]).weekday() == 0]
    o = next(o for o in mondays if o["release_date"] == "2025-05-12")
    assert ts(o).hour == 12
    leak = _cp_row(o["release_date"], 16, 0)
    assert leak["release_ts"] == release_ts("crop_progress", date.fromisoformat(o["release_date"]))[0].isoformat()
    ok = _cp_row("2025-05-11", 15, 59)  # previous day, 15:59 ET
    # careless date-level join: the 16:00 same-day row gets through selection, the guard must fail the build
    with pytest.raises(LeakageError):
        build_features(store + [leak], o, select=date_level_select)
    # the 15:59 previous-day row is admitted
    feats = build_features(store + [ok], o, select=date_level_select)
    assert ok in feats
    # the strict information set excludes the 16:00 row and admits the 15:59 row
    strict = info_set(store + [leak, ok], o)
    assert leak not in strict and ok in strict
    assert build_features(store + [leak, ok], o) == strict
