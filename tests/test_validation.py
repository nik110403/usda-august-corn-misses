"""Store validation: WASDE identity (all eras) and TXT-era header/date/coverage assertions.

Needs data/store.csv and data/raw/: run `make store` first. No scoring of any kind.
Identity tolerance: <= 1 unit of the printed precision (1 million bushels; WASDE tables print integer million
bushels and note 'Totals may not add due to rounding').
"""
import collections
import csv
import re
from datetime import date
from pathlib import Path

import pytest

from cfstore.fetch import DATA, ROOT
from cfstore.parse import parse_corn
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
