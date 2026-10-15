"""F6a forecast layer: allowlist, as-of correctness, M1/M2/M3 formulas, LP, stocks-to-use identity, leakage of M2's r and M3's x.
NO score on real seasons (training-set errors are model inputs). Needs data/store.csv: run `make store` first."""
import csv
import itertools
from datetime import date, datetime, time
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest

from cfstore import models
from cfstore.asof import LeakageError, build_features, date_level_select, origins
from cfstore.baselines import load, pct_error
from cfstore.models import (ALLOW, COLUMNS, GE, PROD, YIELD, Run, SeriesNotAllowed, View, assert_allowed, model_features, ols,
                            quantreg_lp, tau_rank, training_set)
from cfstore.skeleton import truth_rows
from cfstore.trend import first_finals


@pytest.fixture(scope="module")
def run():
    store, rel = load()
    return Run(store, rel)


def _o(run, ymd):
    return next(o for o in run.ors if o["release_date"] == ymd)


def _row(series, ref, d, hh, value, flag="v1"):
    t = datetime.combine(date.fromisoformat(d), time(hh, 0), tzinfo=ZoneInfo("America/New_York"))
    return {"series": series, "reference_period": ref, "release_date": d, "release_ts": t.isoformat(), "time_source": "planted",
            "source_file": "planted", "file_hash": "", "value": value, "revision_flag": flag}


# ---- allowlist -----------------------------------------------------------------------------------------------------

def test_allowlist_excludes_crop_production_point_values():
    for s in ALLOW.values():
        assert not any(x.startswith("crop_production.us_corn.") and not x.endswith("rel_ci90_pct") for x in s)
    assert GE in ALLOW["M3"] and GE not in ALLOW["M1"] and ALLOW["B1"] >= {"crop_production.us_corn.rel_ci90_pct"}


def test_planted_wrong_series_row_raises(run):
    o = _o(run, "2010-08-12")
    planted = _row("crop_production.us_corn.yield", "2010/11", "2010-07-09", 12, "999")
    with pytest.raises(SeriesNotAllowed):
        assert_allowed("M1", [planted])
    v = View("M1", model_features("M1", run.store, o))
    with pytest.raises(SeriesNotAllowed):
        v.val("crop_production.us_corn.yield", 2010, o["release_date"])
    with pytest.raises(SeriesNotAllowed):
        v.val(GE, 2010, o["release_date"])  # M1 may not read the condition series
    # the builder drops everything off the allowlist even if the store carries it
    assert {r["series"] for r in model_features("M1", run.store + [planted], o)} == {YIELD}
    assert {r["series"] for r in model_features("M3", run.store, o)} <= {YIELD, GE}


# ---- M1 ------------------------------------------------------------------------------------------------------------

def test_training_n_runs_9_to_30_and_october_gap(run):
    n = lambda ymd: len(training_set(run.view("M1", _o(run, ymd)), _o(run, ymd), YIELD, run.by_sm))
    assert n("2004-08-12") == 9 and n("2025-08-12") == 30 and n("2014-08-12") == 19
    assert n("2014-10-10") == 18 and n("2024-10-11") == 28  # October is one lower from 2014 on (no 2013 origin)
    assert n("2013-11-08") == 18  # Nov 2013 exists; trained on 1995-2012


def _manual_errors(run, o):
    """Independent re-derivation from the raw store: same-horizon errors of seasons whose first-final precedes origin_ts."""
    from cfstore.asof import ts
    out = []
    for s in range(1995, int(o["release_date"][:4])):
        os_ = run.by_sm.get((s, int(o["release_date"][5:7])))
        t = truth_rows(run.store, s)
        if os_ is None or t is None or ts(t) >= ts(o):
            continue
        p = next(Fraction(r["value"]) for r in run.store if r["series"] == YIELD and r["reference_period"] == f"{s}/{(s + 1) % 100:02d}"
                 and r["release_date"] == os_["release_date"] and r["revision_flag"] != "v2_repost")
        out.append(pct_error(Fraction(t["value"]), p))
    return out


def test_m1_rows_match_independent_recomputation(run):
    o = _o(run, "2010-08-12")
    errs = sorted(_manual_errors(run, o))
    assert len(errs) == 15
    rows = run.m1(o)
    lv = {r["level"]: r for r in rows if r["kind"] == "level"}
    r80 = lv["4/5"]
    assert (r80["k_lo"], r80["k_hi"], r80["n"]) == ("1", "15", "15") and r80["scored"] == "1"
    assert Fraction(r80["q_lo"]) == errs[0] and Fraction(r80["q_hi"]) == errs[14]
    point = Fraction(r80["point"])
    assert Fraction(r80["val_lo"]) == point * (1 + errs[0] / 100)
    assert lv["9/10"]["scored"] == "0" and lv["9/10"]["q_lo"] == ""  # 90% needs n >= 19
    tau = {r["tau"]: r for r in rows if r["kind"] == "tau"}
    assert tau["1/10"]["k"] == "1" and tau["9/10"]["k"] == "15" and tau["1/2"]["k"] == "8"  # floor(1.6), ceil(14.4), ceil(8)
    assert tau["1/20"]["scored"] == "0" and tau["19/20"]["scored"] == "0"
    assert Fraction(tau["1/2"]["q"]) == errs[7]


def test_tau_rank_exact():
    assert tau_rank(9, Fraction(1, 10)) == 1 and tau_rank(9, Fraction(9, 10)) == 9
    assert tau_rank(19, Fraction(1, 20)) == 1 and tau_rank(19, Fraction(19, 20)) == 19
    assert tau_rank(29, Fraction(1, 10)) == 3 and tau_rank(29, Fraction(1, 4)) == 7 and tau_rank(29, Fraction(3, 4)) == 23
    assert tau_rank(21, Fraction(1, 2)) == 11


# ---- M2 ------------------------------------------------------------------------------------------------------------

def test_ols_exact():
    a, b = ols([Fraction(x) for x in range(5)], [Fraction(1 + 3 * x) for x in range(5)])
    assert (a, b) == (1, 3)


def test_m2_may_is_m1_and_outside_tests(run):
    o = _o(run, "2012-05-10")
    m1, m2 = run.m1(o), run.m2(o)
    assert [r["q"] for r in m1] == [r["q"] for r in m2] and {r["in_tests"] for r in m2} == {"0"}


def test_m2_r_uses_previous_existing_origin_and_hand_ols(run):
    o = _o(run, "2013-11-08")
    assert run.prev_origin(o)["release_date"].startswith("2013-09")  # no October 2013
    o = _o(run, "2015-08-12")
    rows = run.m2(o)
    v = run.view("M2", o)
    jul, aug = _o(run, "2015-07-10"), o
    r_t = pct_error(v.val(YIELD, 2015, aug["release_date"]), v.val(YIELD, 2015, jul["release_date"]))
    tr = training_set(v, o, YIELD, run.by_sm)
    rs, es = [], []
    for s, e, _, _ in tr:
        os_ = run.by_sm[(s, 8)]
        po = run.by_sm[(s, 7)]
        rs.append(pct_error(v.val(YIELD, s, os_["release_date"]), v.val(YIELD, s, po["release_date"])))
        es.append(e)
    a, b = ols(rs, es)
    r80 = next(r for r in rows if r["kind"] == "level" and r["level"] == "4/5")
    assert Fraction(r80["m2_r"]) == r_t and Fraction(r80["m2_a"]) == a and Fraction(r80["m2_b"]) == b
    resid = sorted(e - a - b * r for e, r in zip(es, rs))
    assert Fraction(r80["q_lo"]) == a + b * r_t + resid[int(r80["k_lo"]) - 1]
    # the OLS residuals sum to zero (intercept included): sanity of the exact fit
    assert sum(e - a - b * r for e, r in zip(es, rs)) == 0


def test_m2_future_row_cannot_change_r(run):
    """Leakage test for M2's r: a planted next-origin yield for the same season (released after the origin) is invisible, and the
    careless date join with a planted same-day row is caught by the guard."""
    o = _o(run, "2015-08-12")
    base = Run(run.store, run.releases).m2(o)
    planted = [_row(YIELD, "2015/16", "2015-09-11", 12, "150.0")]  # the September release, a month after the origin
    after = Run(run.store + planted, run.releases).m2(o)
    assert [r["q"] for r in base] == [r["q"] for r in after]
    same_day = _row(YIELD, "2015/16", "2015-08-12", 16, "150.0")
    with pytest.raises(LeakageError):
        build_features([r for r in run.store if r["series"] == YIELD] + [same_day], o, select=date_level_select)


# ---- M3 ------------------------------------------------------------------------------------------------------------

def test_lp_recovers_exact_line_and_matches_brute_force():
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    a, b, st = quantreg_lp(xs, [1 + 2 * x for x in xs], 0.3)
    assert st == 0 and abs(a - 1) < 1e-8 and abs(b - 2) < 1e-8
    xs = [-3.0, -1.0, 0.0, 2.0, 5.0, 1.0, -2.0]
    es = [4.0, -1.0, 3.0, 6.0, 12.0, 0.5, -3.0]
    for tau in (0.1, 0.5, 0.9):
        a, b, st = quantreg_lp(xs, es, tau)
        loss = lambda a_, b_: sum((e - a_ - b_ * x) * (tau - ((e - a_ - b_ * x) < 0)) for x, e in zip(xs, es))
        best = min(loss(ea - eb * xa, eb) for (xa, ea), (xb, eb_) in itertools.combinations(zip(xs, es), 2)
                   for eb in [(eb_ - ea) / (xb - xa)] if xb != xa for ea in [ea])
        assert st == 0 and abs(loss(a, b) - best) < 1e-7


def test_m3_x_is_previous_week_on_monday_origin_and_ignores_same_day(run):
    o = _o(run, "2015-08-12")
    # 2013-08-12 is a Monday WASDE origin (overlap file): its x must come from the previous Monday-or-earlier release, never that day's 16:00
    mon = _o(run, "2013-08-12")
    x = run.x_line(2013, 8)
    assert x is not None and x["release_date"] < mon["release_date"]
    cp_same = [r for r in run.store if r["series"] == GE and r["release_date"] == mon["release_date"]]
    assert cp_same, "fixture: a Crop Progress release dated on the Monday origin must exist"
    with pytest.raises(LeakageError):  # the careless join pulls the same-day 16:00 row and the guard stops it
        build_features([r for r in run.store if r["series"] in (YIELD, GE)], mon, select=date_level_select)
    planted = [_row(GE, "2013-08-11", "2013-08-12", 16, "99")]
    assert Run(run.store + planted, run.releases).x_line(2013, 8) == x
    # and x for the target season is the LAST line before the origin: no later week of that season is in the view
    later = [r for r in run.view("M3", mon).rows if r["series"] == GE and int(r["reference_period"][:4]) == 2013
             and r["release_date"] > x["release_date"]]
    assert later == []


def test_m3_planted_late_truth_in_training_set_is_caught(run):
    o = _o(run, "2010-08-12")
    feats = model_features("M3", run.store, o)
    late = _row(YIELD, "2009/10", "2010-01-12", 12, "165.2")
    late = dict(late, release_date="2010-08-13", release_ts="2010-08-13T12:00:00-04:00")  # a 2009 'final' released after the origin
    with pytest.raises(LeakageError):
        training_set(View("M3", [r for r in feats if not (r["reference_period"] == "2009/10" and r["release_date"].startswith("2010"))] + [late]),
                     o, YIELD, run.by_sm)


def test_m3_rows_sorted_and_lp_optimal(run):
    for ymd in ("2008-08-12", "2018-09-12", "2010-06-10", "2022-11-09"):
        o = _o(run, ymd)
        rows = run.m3(o)
        assert len(rows) == 5 and {r["m3_lp_status"] for r in rows} == {"0"}
        vals = [Fraction(r["q"]) for r in rows]
        assert vals == sorted(vals)
        assert all(r["m3_fallback"] == "" and int(r["m3_n"]) >= 8 for r in rows)
    assert run.m3(_o(run, "2008-08-12"))[0]["m3_n"] == "13"  # 1995-2007
    assert run.m3(_o(run, "2008-06-10"))[0]["m3_n"] == "12"  # no condition line before the 12 June 1995 origin: trains from 1996 (PREREG 3)
    assert {r["m3_fallback"] for r in run.m3(run.by_sm[(2010, 5)])} == {"may"}


# ---- production band, stocks-to-use, B1, B0 ------------------------------------------------------------------------

def test_production_truth_is_same_release_as_yield_truth(run):
    prod_rows = {(r["reference_period"], r["release_date"]) for r in run.store if r["series"] == PROD and r["revision_flag"] != "v2_repost"}
    for s, _, t in first_finals(run.store):
        if s <= 2025:
            assert (t["reference_period"], t["release_date"]) in prod_rows


def test_stocks_to_use_identity(run):
    o = _o(run, "2015-08-12")
    prod_rows, _ = run.prod(o)
    st = run.stu(o, prod_rows)
    q50 = Fraction(next(r for r in prod_rows if r["kind"] == "tau" and r["tau"] == "1/2")["val"])
    assert st["ending_stocks_q50"] == st["ending_stocks"] + (q50 - st["production"])
    assert st["stocks_to_use_pct_q50"] == 100 * st["ending_stocks_q50"] / st["total_use"]
    assert st["carry_in_revision"] == st["beginning_stocks"] - Fraction(
        next(r for r in run.store if r["series"] == "wasde.us_corn.beginning_stocks" and r["reference_period"] == "2015/16"
             and r["release_date"] == "2015-07-10" and r["revision_flag"] != "v2_repost")["value"])


def test_b1_uses_wasde_production_and_oct_2008_missing(run):
    rows = run.b1(_o(run, "2015-08-12"))
    p = next(r for r in rows if r["model"] == "B1")
    from cfstore.baselines import b1_band
    ref = b1_band(run.store, _o(run, "2015-08-12"))  # F5 reference built from Crop Production values: cross-check only
    assert abs(Fraction(p["val_lo"]) - ref.prod_lo / 1000) < 1  # Mbu vs kbu, WASDE rounds to whole Mbu
    assert p["scored"] == "1" and p["level"] == "9/10"
    o08 = run.b1(_o(run, "2008-10-10"))
    assert {r["scored"] for r in o08} == {"0"}
    assert run.b1(_o(run, "2015-07-10")) == []


def test_b0_store_reduction_is_equivalent(run):
    assert [(s, v) for s, v, _ in first_finals(run.b0_store)] == [(s, v) for s, v, _ in first_finals(run.store)]


def test_forecast_origin_rows_structure_and_no_truth_columns(run):
    assert not any(("truth" in c or "final" in c) for c in COLUMNS)
    rows, stu = run.origin_rows(_o(run, "2010-08-12"))
    models_seen = {}
    for r in rows:
        models_seen[(r["model"], r["kind"])] = models_seen.get((r["model"], r["kind"]), 0) + 1
    assert models_seen[("M1", "tau")] == 7 and models_seen[("M1", "level")] == 3 and models_seen[("M3", "tau")] == 5
    assert models_seen[("PROD", "level")] == 3 and models_seen[("B1", "level")] == 1 and models_seen[("B2", "point")] == 1
    assert all(set(r) == set(COLUMNS) for r in rows)
    # no value in this origin's rows is the target season's first-final yield or production as a *truth* column (structure only)
    assert stu["horizon"] == "Aug"


def test_v2_variant_swaps_only_logged_reposts(run):
    st2, rel2 = models.v2_variant(run.store, run.releases)
    assert not any(r["revision_flag"] == "v2_repost" for r in st2)
    changed = {o["release_date"] for o, o2 in zip(run.releases, rel2) if o != o2}
    assert changed <= {"2008-12-12", "2013-07-11", "2018-12-14"}
    assert "2013-07-11" in changed
    assert Run(st2, rel2).m1(_o(Run(st2, rel2), "2013-07-11"))  # still builds through the guard


def test_b0_real_origins_scored_only_from_nine_errors_at_every_level(run):
    """Structure only (scored flags), no score: B0 rows are scored from the first season with >= 9 own OOS errors (2009)."""
    for ymd, want in (("2008-08-12", "0"), ("2009-08-12", "1")):
        rows = run.b0(_o(run, ymd))
        assert {r["scored"] for r in rows if r["kind"] == "level" and r["level"] in ("1/2", "4/5")} == {want}, ymd  # 90% needs n >= 19
        assert {r["scored"] for r in rows if r["kind"] == "tau" and Fraction(r["tau"]) in models.TAUS5} == {want}, ymd
    assert {r["n"] for r in run.b0(_o(run, "2009-08-12"))} == {"9"}


def test_build_identity_is_stamped_and_stable():
    from cfstore.fetch import ROOT
    a, b = models.build_identity(ROOT), models.build_identity(ROOT)
    assert a == b and len(a["store_sha256"]) == 64 and set(a) == set(models.META_COLS)


def test_quickstats_duplicate_row_raises(tmp_path, monkeypatch):
    import gzip
    from cfstore import quickstats
    hdr = ["SOURCE_DESC", "AGG_LEVEL_DESC", "FREQ_DESC", "REFERENCE_PERIOD_DESC", "DOMAIN_DESC", "SHORT_DESC", "YEAR", "VALUE", "LOAD_TIME"]
    base = ["SURVEY", "NATIONAL", "ANNUAL", "YEAR", "TOTAL", quickstats.YIELD_DESC, "2000", "100", "t"]
    p = tmp_path / "qs.txt.gz"
    with gzip.open(p, "wt") as f:
        f.write("\t".join(hdr) + "\n" + "\t".join(base) + "\n" + "\t".join(base[:7] + ["101", "t"]) + "\n")
    monkeypatch.setattr(quickstats, "RAW", p)
    monkeypatch.setattr(quickstats, "TRUTH", tmp_path / "truth.csv")
    with pytest.raises(ValueError, match="duplicate"):
        quickstats.write_truth()
