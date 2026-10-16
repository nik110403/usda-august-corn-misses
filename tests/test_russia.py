"""F10 Russia wheat (PREREG-AMENDMENTS 2026-10-16 "F10 optional slot"): parser, hand checks against the raw archive, truth rule,
as-of leakage (planted leaks), M1 reuse, burn-in, availability-before-coverage, gates. Synthetic data for everything that scores;
the real archive is read only for parsing / structure / leakage checks. Needs data/russia_store.csv (`make russia-store`) and data/raw."""
import csv
import re
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from cfstore import russia
from cfstore.asof import LeakageError, assert_no_leak, build_features, date_level_select, origins, ts
from cfstore.baselines import pct_error, season_of
from cfstore.conformal import band_ranks
from cfstore.evaluate import verify_prereg
from cfstore.fetch import DATA, ROOT
from cfstore.models import ALLOW, COLUMNS, RUSSIA, month_of, ref
from cfstore.parse_russia import parse_russia_txt, parse_russia_xls, parse_russia_xml
from cfstore.russia import RussiaRun, RView, SERIES, assert_truth_after_origins, russia_truths
from cfstore.times import release_ts


# ---- synthetic world -----------------------------------------------------------------------------------------------------

def synth(first=1995, last=2020, jitter=0.15):
    """Monthly WASDE for first..last (release on the 10th, real release-time table), each printing marketing years top-2..top.
    Value of MY m in a release = base(m) + a month-specific wobble; the last print of m (April of m+3) is the truth."""
    rel, store = [], []
    for y in range(first, last + 1):
        for mo in range(1, 13):
            d = date(y, mo, 10).isoformat()
            t = release_ts("wasde", date.fromisoformat(d))[0].isoformat()
            f = f"synthetic/{d}.txt"
            rel.append({"release_date": d, "release_ts": t, "file": f, "revision_flag": "v1", "n_values": "3"})
            top = russia.projection_year(d)
            for m in (top - 2, top - 1, top):
                base = 40 + 3 * ((m * 7) % 11)
                wob = ((m * 31 + mo * 17) % 13 - 6) * jitter * (1 if m == top else 0.2)
                store.append({"series": SERIES, "reference_period": ref(m), "release_date": d, "release_ts": t, "time_source": "synthetic",
                              "source_file": f, "file_hash": "", "value": repr(round(base + wob, 2)), "revision_flag": "v1"})
    return store, rel


@pytest.fixture(scope="module")
def world():
    return synth()


@pytest.fixture(scope="module")
def sruns(world):
    return RussiaRun(*world)


def _o(rr, season, month):
    return rr.by_sm[(season, month)]


def _val(store, m, d):
    return Fraction(next(r["value"] for r in store if r["reference_period"] == ref(m) and r["release_date"] == d))


# ---- parser --------------------------------------------------------------------------------------------------------------

TXT_SNIP = """\
                         World Wheat Supply and Use 1/
                             (Million Metric Tons)
===============================================================================
                      :                      1994/95
World 3/              :  142.08  524.72  110.12   99.12  548.96  111.58  117.83
    Russia            :   16.73   32.10    1.56   20.43   42.63    0.39    7.37
                      :                 1995/96 (Estimated)
    Russia            :    7.37   30.10    4.10   17.92   39.42    0.10    2.05
                      :                 1996/97 (Projected)
    Russia        Jul :    2.05   37.00    2.50   16.50   38.70    0.50    2.35
                  Aug :    2.05   38.50    2.50   16.50   38.70    0.50    2.35
    Kazakstan     Jul :    1.40   11.00    0.00    2.00    6.10    3.00    3.30
                         World Coarse Grains Supply and Use 1/
    Russia            :    1.00    2.00    3.00    4.00    5.00    6.00    7.00
"""


def test_txt_parser_takes_last_month_row_and_stops_at_the_next_table():
    rows = parse_russia_txt(TXT_SNIP, 8)
    assert [(r["reference_period"], r["value"]) for r in rows] == [("1994/95", 32.1), ("1995/96", 30.1), ("1996/97", 38.5)]


def test_txt_parser_column_order_and_width_guards():
    with pytest.raises(ValueError, match="column order"):
        parse_russia_txt(TXT_SNIP, 9)
    with pytest.raises(ValueError, match="expected 7"):
        parse_russia_txt(TXT_SNIP.replace("16.73   32.10    1.56", "16.73   32.10"), 8)
    assert parse_russia_txt("no wheat table here\n", 8) == []


def test_every_release_has_three_consecutive_marketing_years_ending_in_the_projection_year():
    assert [russia.projection_year(d) for d in ("1995-01-12", "1995-04-11", "1995-05-11", "2026-10-09")] == [1994, 1994, 1995, 2026]
    r = {"release_date": "1996-08-12"}
    russia.check_release_rows(r, [{"reference_period": x} for x in ("1994/95", "1995/96", "1996/97")])
    with pytest.raises(ValueError):
        russia.check_release_rows(r, [{"reference_period": x} for x in ("1994/95", "1995/96", "1997/98")])


# ---- real archive: validation and hand checks ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real():
    store = russia.read_csv(DATA / "russia_store.csv")
    rel = russia.read_csv(DATA / "wasde_releases.csv")
    return store, rel


def test_every_primary_wasde_may_nov_1995_2025_has_a_russia_projection(real):
    store, rel = real
    ors = russia.validate_origins(store, rel)  # raises unless each origin carries exactly one projection value
    got = {o["release_date"] for o in ors}
    primaries = {r["release_date"] for r in rel if r["revision_flag"] in ("v1", "v1_corrected_only") and 5 <= int(r["release_date"][5:7]) <= 11}
    assert got == primaries
    assert len([o for o in ors if season_of(o) <= 2025]) == len(primaries) - len([d for d in primaries if d[:4] == "2026"])
    for o in ors:
        v = [r for r in store if r["release_date"] == o["release_date"] and r["reference_period"] == ref(season_of(o))]
        assert len(v) == 1 and 20 < float(v[0]["value"]) < 120


def test_store_matches_release_table_times_and_files(real):
    store, rel = real
    by = {r["release_date"]: r for r in rel}
    for r in store:
        assert r["release_ts"] == by[r["release_date"]]["release_ts"] and r["source_file"] == by[r["release_date"]]["file"]


def _txt_line(path, lineno):
    return (ROOT / path).read_bytes().decode("latin-1").replace("\x0c", "\n").splitlines()[lineno - 1]


def _store_val(store, d, my):
    return next(float(r["value"]) for r in store if r["release_date"] == d and r["reference_period"] == my)


# (file, 1-based line, expected label fragment, marketing year, release date, production Mt): TXT era, 2 fixed + 1 random
TXT_CHECKS = [
    ("data/raw/wasde/1996-08-12/95834/wasde-08-12-1996.txt", 850, "Russia            :    7.37   30.10", "1995/96", "1996-08-12", 30.10),
    ("data/raw/wasde/2001-08-10/95773/wasde-08-10-2001.txt", 1050, "Aug :    1.40   38.50", "2001/02", "2001-08-10", 38.50),
    ("data/raw/wasde/1996-07-12/95835/wasde-07-12-1996.txt", 933, "Jul :    2.05   37.00", "1996/97", "1996-07-12", 37.00),
]


@pytest.mark.parametrize("path,line,frag,my,d,want", TXT_CHECKS)
def test_hand_check_txt_values_against_raw_file_lines(real, path, line, frag, my, d, want):
    assert frag in _txt_line(path, line)
    assert _store_val(real[0], d, my) == want


def _xml_russia_production(path, sr):
    """Independent of ElementTree: all `Production` cells inside the Russia region group of sub-report `sr` (raw text regex)."""
    t = (ROOT / path).read_bytes().decode("utf8", "replace")
    seg = t[t.find(f'<Report Name="{sr}"'):]
    m = re.search(r'region1="\s*Russia"', seg)
    g = seg[m.start():]
    g = g[:g.find("<m1_region_group ", 10)]
    return [float(x) for x in re.findall(r'attribute1="Production"[^>]*>.*?cell_value1="([\d.]+)"', g, re.S)]


# XML files are one physical line (line 1); the anchor is the Russia region group of sr19 (projection year, last month group = current)
XML_CHECKS = [
    ("data/raw/wasde/2013-01-11/95628/wasde-01-11-2013.xml", "2012/13", "2013-01-11", [38.0, 37.7]),
    ("data/raw/wasde/2026-10-09/796099/wasde1026.xml", "2026/27", "2026-10-09", [88.0, 88.0]),
    ("data/raw/wasde/2016-09-12/95584/wasde-09-12-2016.xml", "2016/17", "2016-09-12", [72.0, 72.0]),
]


@pytest.mark.parametrize("path,my,d,cells", XML_CHECKS)
def test_hand_check_xml_values_against_raw_text(real, path, my, d, cells):
    assert _xml_russia_production(path, "sr19") == cells
    assert _store_val(real[0], d, my) == cells[-1]


def test_hand_check_xls_origin_july_2013_page19_row47(real):
    import xlrd
    sh = xlrd.open_workbook(str(ROOT / "data/raw/wasde/2013-07-11/95622_xls/ORIGwasde-07-11-2013.xls")).sheet_by_name("Page 19")
    assert [v for v in sh.row_values(46) if v != ""][:3] == ["Jul", 5.27, 54.0]
    assert _store_val(real[0], "2013-07-11", "2013/14") == 54.0


def test_parsers_agree_with_store_on_all_three_formats(real):
    store = real[0]
    for path, my, d, _ in XML_CHECKS:
        rows = parse_russia_xml((ROOT / path).read_bytes(), int(d[5:7]))
        assert {r["reference_period"]: r["value"] for r in rows}[my] == _store_val(store, d, my)
    assert parse_russia_xls(str(ROOT / "data/raw/wasde/2013-07-11/95622_xls/ORIGwasde-07-11-2013.xls"), 7)[-1]["value"] == 54.0


# ---- truth ---------------------------------------------------------------------------------------------------------------------

def test_truth_is_the_last_print_and_latest_printed_years_are_unscored(world):
    store, rel = world
    tr = russia_truths(store)
    assert sorted(tr) == list(range(1992, 2018))  # 2018..2020 are still printed in the latest archived release (Dec 2020)
    for s, row in tr.items():
        printed = [r for r in store if r["reference_period"] == ref(s)]
        assert row["release_date"] == max(r["release_date"] for r in printed)
        if s >= 1995:
            assert row["release_date"] == f"{s + 3}-04-10"


def test_truth_vintage_strictly_after_last_origin_and_guard_fires(world, sruns):
    store, rel = world
    assert_truth_after_origins(sruns.truths, sruns.ors)
    s = 2005
    last = max((o for o in sruns.ors if season_of(o) == s), key=ts)
    early = dict(sruns.truths[s], release_date=last["release_date"], release_ts=last["release_ts"])
    with pytest.raises(LeakageError):
        assert_truth_after_origins({**sruns.truths, s: early}, sruns.ors)
    with pytest.raises(LeakageError):
        assert_truth_after_origins({**sruns.truths, s: dict(early, release_date="2005-11-01", release_ts="2005-11-01T08:30:00-05:00")}, sruns.ors)


def test_real_truth_vintages_follow_every_origin_and_latest_years_unscored(real):
    store, rel = real
    rr = RussiaRun(store, rel)  # asserts truth after origins for every season
    last_rel = max(r["release_date"] for r in store)
    still = {r["reference_period"] for r in store if r["release_date"] == last_rel}
    assert still and all(int(m[:4]) not in rr.truths for m in still)
    assert all(r["release_date"] <= last_rel for r in rr.truths.values())


# ---- as-of leakage (item 6) -------------------------------------------------------------------------------------------------

def test_russia_leak_real_store_every_origin_sees_only_earlier_releases(real):
    store, rel = real
    rows = [r for r in store if r["series"] == SERIES]
    for o in origins(rel):
        feats = build_features(rows, o)  # select + assert_no_leak
        assert feats and all(r["release_date"] <= o["release_date"] for r in feats)
        assert all(ts(r) < ts(o) for r in feats if r["source_file"] != o["file"])
        assert {r["release_date"] for r in feats if r["source_file"] == o["file"]} <= {o["release_date"]}


def test_russia_leak_planted_future_value_is_excluded_and_a_careless_selector_fails(world, sruns):
    store, rel = world
    o = _o(sruns, 2008, 8)
    rows = list(sruns.store)
    base = {"series": SERIES, "reference_period": ref(2008), "source_file": "planted", "time_source": "planted", "file_hash": "", "revision_flag": "v1"}
    same_day_late = dict(base, release_date=o["release_date"], release_ts=o["release_ts"].replace("08:30:00", "16:00:00"), value="999.0")
    next_day = dict(base, release_date="2008-08-11", release_ts="2008-08-11T08:30:00-04:00", value="999.0")
    control = dict(base, reference_period=ref(2007), release_date="2008-08-09", release_ts="2008-08-09T08:30:00-04:00", value="41.0")
    got = build_features(rows + [same_day_late, next_day, control], o)
    assert control in got and same_day_late not in got and next_day not in got
    with pytest.raises(LeakageError):  # the mutated (date-level) selector admits the same-day-late row; the guard must fail
        build_features(rows + [same_day_late], o, select=date_level_select)
    with pytest.raises(LeakageError):
        build_features(rows + [next_day], o, select=lambda r, org: list(r))  # a selector that admits everything


def test_russia_leak_future_truth_cannot_enter_training(world, sruns):
    """A future release must not change a past origin's forecast; a truth view that ignores the origin must fail the guard."""
    store, rel = world
    o = _o(sruns, 2010, 8)
    base_rows = sruns.m1(o)
    poisoned = [dict(r, value="500.0") if r["release_date"] > o["release_date"] else r for r in store]
    assert RussiaRun(poisoned, rel).m1(o) == base_rows  # values released after the origin are never read
    leaky = type("Leaky", (RView,), {"truth": lambda self, series, season: self._truths.get(season)})
    feats = build_features(sruns.store, o)
    from cfstore.models import training_set
    with pytest.raises(LeakageError):
        training_set(leaky(feats, sruns.truths), o, SERIES, sruns.by_sm)


@pytest.mark.parametrize("season,month", [(2006, 5), (2010, 8), (2015, 11), (2021, 8), (2024, 10), (2025, 9), (2026, 8)])
def test_russia_leak_real_forecast_is_identical_when_all_later_releases_are_removed(real, season, month):
    store, rel = real
    full = RussiaRun(store, rel)
    o = _o(full, season, month)
    cut_store = [r for r in store if ts(r) <= ts(o)]
    cut_rel = [r for r in rel if ts(r) <= ts(o)]
    assert RussiaRun(cut_store, cut_rel).m1(o) == full.m1(o)


# ---- M1 -----------------------------------------------------------------------------------------------------------------------

def test_m1_uses_the_models_conformal_path_and_hand_computed_errors(world, sruns):
    store, _ = world
    t, m = 2012, 8
    o = _o(sruns, t, m)
    d = lambda s: _o(sruns, s, m)["release_date"]
    errs = [pct_error(Fraction(sruns.truths[s]["value"]), _val(store, s, d(s))) for s in range(1995, t) if ts(sruns.truths[s]) < ts(o)]
    assert len(errs) == len(range(1995, t - 2))  # truth of s lands in April of s+3: seasons 1995..t-3
    point = _val(store, t, o["release_date"])
    rows = sruns.m1(o)
    n = len(errs)
    k_lo, k_hi = band_ranks(n, Fraction(4, 5))
    r80 = next(r for r in rows if r["kind"] == "level" and r["level"] == "4/5")
    assert (int(r80["n"]), int(r80["k_lo"]), int(r80["k_hi"])) == (n, k_lo, k_hi)
    srt = sorted(errs)
    assert Fraction(r80["q_lo"]) == srt[k_lo - 1] and Fraction(r80["q_hi"]) == srt[k_hi - 1]
    assert Fraction(r80["val_lo"]) == point * (1 + srt[k_lo - 1] / 100)
    assert {r["model"] for r in rows} == {"M1"} and {r["unit"] for r in rows} == {"Mt"}
    assert len([r for r in rows if r["kind"] == "tau"]) == 7 and len([r for r in rows if r["kind"] == "level"]) == 3


def test_burn_in_nine_errors_at_every_level_and_ninety_needs_nineteen(world, sruns):
    ns = {}
    for o in sruns.ors:
        if month_of(o) != 8:
            continue
        rows = sruns.m1(o)
        n = int(rows[0]["n"])
        ns[season_of(o)] = n
        for r in rows:
            if n < 9:
                assert r["scored"] == "0"
        for r in rows:
            if r["kind"] == "level" and r["level"] == "9/10":
                assert (r["scored"] == "1") == (n >= 19)
            if r["kind"] == "level" and r["level"] == "4/5" and n >= 9:
                assert r["scored"] == "1"
    assert ns[1995] == 0 and ns[2006] == 9 and ns[2005] == 8  # nine seasons with a settled truth before the first scored origin


def test_allowlist_for_russia_is_only_the_russia_series():
    assert ALLOW["M1R"] == frozenset({RUSSIA})
    assert all(RUSSIA not in v for k, v in ALLOW.items() if k != "M1R")


# ---- scoring, counts, gates (synthetic only) --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fc_rows(sruns):
    return [{k: str(v) for k, v in r.items()} for r in sruns.all_rows()]


def test_forecast_file_has_no_truth_column():
    assert not any("truth" in c or "final" in c for c in russia.FC_COLUMNS)


def test_counts_use_only_availability_and_never_call_covered(monkeypatch, fc_rows, sruns):
    monkeypatch.setattr(russia, "covered", lambda *a, **k: (_ for _ in ()).throw(AssertionError("covered() called")))
    lines = russia.count_lines(fc_rows, sruns.truths)
    aug = next(ln for ln in lines if ln.strip().startswith("Aug:"))
    assert "80% band 15/12" in aug  # scored 2006..2020; settled (truth) 2006..2017; 2018-2020 still printed in the latest release


def test_counts_are_printed_before_any_coverage_is_computed(monkeypatch, capsys, fc_rows, sruns):
    seen = []
    orig = russia.coverage_rows
    monkeypatch.setattr(russia, "coverage_rows", lambda *a: (seen.append(capsys.readouterr().out), orig(*a))[1])
    out = russia.evaluate_russia(fc_rows, sruns.truths, sruns.ors, sruns.store, expected=None)
    assert "availability counts" in seen[0] and "coverage" not in seen[0].lower().replace("no coverage computed", "")
    assert out["coverage.csv"]


def test_headline_wording_only_with_fifteen_august_seasons(world):
    store, rel = synth(1995, 2024)  # truth through 2021: August scored 2006..2021 = 16 seasons
    rr = RussiaRun(store, rel)
    rows = [{k: str(v) for k, v in r.items()} for r in rr.all_rows()]
    cov, per = russia.coverage_rows(rows, rr.truths)
    aug = next(c for c in cov if (c["horizon"], c["level"]) == ("Aug", "4/5"))
    assert aug["n"] == 16 and aug["status"] == "exploratory" and aug["wording"] and "outside the 33-test family" in aug["note"]
    store2, rel2 = synth(1995, 2022)  # truth through 2019: 14 seasons -> descriptive, no wording
    rr2 = RussiaRun(store2, rel2)
    cov2, _ = russia.coverage_rows([{k: str(v) for k, v in r.items()} for r in rr2.all_rows()], rr2.truths)
    aug2 = next(c for c in cov2 if (c["horizon"], c["level"]) == ("Aug", "4/5"))
    assert aug2["n"] == 14 and aug2["status"] == "descriptive" and aug2["wording"] == "" and "14 < 15" in aug2["note"]
    other = next(c for c in cov if (c["horizon"], c["level"]) == ("Sep", "4/5"))
    assert other["status"] == "descriptive" and other["wording"] == ""
    for c in cov:
        assert 0 <= float(c["cp90_lo"]) <= float(c["coverage"]) <= float(c["cp90_hi"]) <= 1


def test_coverage_counts_are_exact_hits_of_the_stored_band(fc_rows, sruns):
    cov, per = russia.coverage_rows(fc_rows, sruns.truths)
    ix = russia.fc_index(fc_rows)
    c = next(c for c in cov if (c["horizon"], c["level"]) == ("Aug", "4/5"))
    hits = [p for p in per if (p["horizon"], p["level"]) == ("Aug", "4/5")]
    assert c["x"] == sum(p["covered"] for p in hits) and c["n"] == len(hits)
    for p in hits:
        r, tr = ix[("Aug", p["season"], Fraction(4, 5))], sruns.truths[p["season"]]
        e = pct_error(Fraction(tr["value"]), Fraction(r["point"]))
        assert bool(p["covered"]) == (Fraction(r["q_lo"]) <= e <= Fraction(r["q_hi"]))


def _root(tmp_path, world, fc_rows, prereg_ok=True):
    store, rel = world
    (tmp_path / "data").mkdir()
    (tmp_path / "out" / "russia").mkdir(parents=True)
    russia.write_store(tmp_path / "data" / "russia_store.csv", store)
    with (tmp_path / "data" / "wasde_releases.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, list(rel[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rel)
    md = tmp_path / "PREREG.md"
    md.write_text("prereg")
    import hashlib
    (tmp_path / "PREREG.sha256").write_text(hashlib.sha256(md.read_bytes()).hexdigest() + ("" if prereg_ok else "0") + "  PREREG.md\n")
    from cfstore.models import META_COLS, write_csv
    write_csv(tmp_path / "out" / "russia" / "forecasts.csv", [dict(r, **IDENT) for r in fc_rows], russia.FC_COLUMNS + META_COLS)
    return tmp_path


IDENT = {"build_commit": "c0ffee", "code_tree": "tree1", "store_sha256": "store1"}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(russia, "russia_identity", lambda root: dict(IDENT))
    monkeypatch.setattr(russia, "run_leakage", lambda root: [{"group": "leakage (other)", "passed": 1, "failed": 0}])


def test_run_end_to_end_on_synthetic_data_writes_outputs_after_the_counts(tmp_path, world, fc_rows, patched, capsys, monkeypatch):
    monkeypatch.setattr(russia, "EXPECTED_N", None)  # the pinned counts belong to the real store, not the synthetic world
    root = _root(tmp_path, world, fc_rows)
    assert russia.run(root) == 0
    out = capsys.readouterr().out
    assert out.index("availability counts") < out.index("Aug 80%") < out.index("wrote")
    files = {p.name for p in (root / "out" / "russia").iterdir()}
    assert {"coverage.csv", "truth.csv", "seasons_covered.csv", "primary.txt", "forecasts.csv"} <= files
    prim = (root / "out" / "russia" / "primary.txt").read_text()
    assert "forecasts_sha256:" in prim and russia.MEMO_QUESTION in prim
    truth = list(csv.DictReader((root / "out" / "russia" / "truth.csv").open()))
    assert any(t["status"].startswith("unscored") for t in truth) and all(t["truth_vintage"] > t["last_origin"] for t in truth if t["truth_mt"])


def test_run_refuses_bad_prereg_hash_failed_leakage_and_stale_forecasts(tmp_path, world, fc_rows, patched, monkeypatch):
    bad = _root(tmp_path / "a", world, fc_rows, prereg_ok=False) if (tmp_path / "a").mkdir() is None else None
    with pytest.raises(SystemExit, match="REFUSING TO RUN"):
        russia.run(bad)
    good = _root((tmp_path / "b"), world, fc_rows) if (tmp_path / "b").mkdir() is None else None
    monkeypatch.setattr(russia, "run_leakage", lambda root: (_ for _ in ()).throw(SystemExit("REFUSING TO RUN: leakage tests did not all pass")))
    with pytest.raises(SystemExit, match="leakage"):
        russia.run(good)
    assert not (good / "out" / "russia" / "coverage.csv").exists()
    monkeypatch.setattr(russia, "run_leakage", lambda root: [{"group": "x"}])
    monkeypatch.setattr(russia, "russia_identity", lambda root: dict(IDENT, code_tree="other"))
    with pytest.raises(SystemExit, match="stale"):
        russia.run(good)
    assert not (good / "out" / "russia" / "coverage.csv").exists()


def test_prereg_is_unchanged():
    verify_prereg(ROOT)


def test_pinned_counts_refuse_a_mismatch(tmp_path, world, fc_rows, patched):
    root = _root(tmp_path, world, fc_rows)
    with pytest.raises(SystemExit, match="pinned preflight counts"):
        russia.run(root)  # the synthetic world does not have the real store's counts
