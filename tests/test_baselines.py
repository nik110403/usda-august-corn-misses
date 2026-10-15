"""F5 baselines B0/B1/B2 and the replication helpers: shapes, as-of correctness, formulas. NO score is computed here
(PREREG: nothing scored before F6); bands are tested on synthetic errors, B0/B1 on a few seasons' inputs only.
Needs data/store.csv: run `make store` first."""
import math
from fractions import Fraction

import pytest

from cfstore.asof import LeakageError, origins
from cfstore.baselines import (b0_errors, b0_point, b1_band, b2_point, load, pct_error, percent_band, season_of)
from cfstore.fetch import ROOT
from cfstore.nass import read_text
from cfstore.parse_prod import SUMMARY_TITLE, parse_corn_table
from cfstore.replication import finals_a, finals_b, forecasts, printed, replicate
from cfstore.skeleton import truth_rows
from cfstore.trend import fit_trend, finals_before


@pytest.fixture(scope="module")
def data():
    return load()


def _origin(releases, ymd):
    return next(o for o in origins(releases) if o["release_date"] == ymd)


def test_pct_error_definition():
    assert pct_error(Fraction(110), Fraction(100)) == 10
    assert pct_error(Fraction(90), Fraction(100)) == -10


def test_percent_band_synthetic():
    errs = [Fraction(i) for i in range(-4, 5)]  # n = 9
    b = percent_band(Fraction(200), errs, Fraction(4, 5))
    assert (b.k_lo, b.k_hi, b.n) == (1, 9, 9) and (b.e_lo, b.e_hi) == (-4, 4)
    assert (b.lo, b.hi) == (Fraction(200) * Fraction(96, 100), Fraction(200) * Fraction(104, 100))
    assert percent_band(Fraction(200), errs[:8], Fraction(4, 5)) is None  # n = 8: k_lo = 0, not scored
    assert percent_band(Fraction(200), errs, Fraction(9, 10)) is None  # 90% needs n >= 19
    assert percent_band(Fraction(1), [Fraction(i) for i in range(19)], "9/10").k_lo == 1
    with pytest.raises(TypeError):
        percent_band(Fraction(1), errs, 0.8)


def test_b0_point_matches_independent_refit_and_excludes_future(data):
    store, rel = data
    o = _origin(rel, "2010-08-12")
    fin = finals_before(store, o)
    assert max(s for s, _, _ in fin) == 2009 and min(s for s, _, _ in fin) == 1995  # 2010's own final is not visible
    manual = fit_trend(fin).predict(2010)
    assert b0_point(store, o) == manual
    # refit including the origin's own season (leaked) changes the point: the guard is active
    leaked = fin + [(2010, Fraction(truth_rows(store, 2010)["value"]), truth_rows(store, 2010))]
    assert fit_trend(leaked).predict(2010) != manual


def _aug(releases, year):
    return next(o for o in origins(releases) if o["release_date"].startswith(f"{year}-08"))


def test_b0_needs_five_finals(data):
    store, rel = data
    with pytest.raises(ValueError):
        b0_point(store, _aug(rel, 1999))  # finals of 1995-1998 only: 4
    b0_point(store, _aug(rel, 2000))  # 5: fine


def test_b0_errors_shape_and_asof(data):
    store, rel = data
    o = _origin(rel, "2010-08-12")
    errs = b0_errors(store, rel, o)
    assert 5 <= len(errs) <= 2010 - 2000  # earlier seasons only; each needs >= 5 finals at its own origin (from 2000)
    # one error recomputed by hand: season 2005 at the August origin
    o05 = _origin(rel, "2005-08-12")
    e05 = pct_error(Fraction(truth_rows(store, 2005)["value"]), b0_point(store, o05, 2005))
    assert e05 in errs
    # an earlier origin sees strictly fewer errors (truth timing)
    assert len(b0_errors(store, rel, _origin(rel, "2006-08-11"))) < len(errs)


def test_b1_aug_2015_formula_and_fields(data):
    store, rel = data
    o = _origin(rel, "2015-08-12")
    b = b1_band(store, o)
    assert (b.production_kbu, b.r_pct, b.area_kac) == (13686063, Fraction("7.5"), 81101)
    assert b.prod_lo == b.production_kbu / Fraction("1.075") and b.prod_hi == b.production_kbu / Fraction("0.925")
    assert b.yield_lo == b.prod_lo / b.area_kac and b.yield_hi == b.prod_hi / b.area_kac
    assert abs(float(b.yield_point) - 168.8) < 0.05 and b.yield_lo < b.yield_point < b.yield_hi


def test_b1_only_aug_nov_and_all_origins_run(data):
    store, rel = data
    with pytest.raises(ValueError):
        b1_band(store, _origin(rel, "2015-07-10"))
    n, missing = 0, []
    for o in origins(rel):
        if 8 <= int(o["release_date"][5:7]) <= 11 and 2004 <= season_of(o) <= 2025:
            try:
                b = b1_band(store, o)
            except ValueError:
                missing.append(o["release_date"])
                continue
            assert b.prod_lo < b.production_kbu < b.prod_hi
            n += 1
    # Oct 2008: the Crop Production of 10-10 survives only as the 28-Oct reissue (PREREG-AMENDMENTS item 1), which is not
    # in the Oct-9/10 information set, so B1 has no printed reliability at that origin. Nothing else is missing.
    assert missing == ["2008-10-10"] and n >= 80


def test_b1_goes_through_guard_not_around_it(data):
    """A joint Crop Production row stamped after the origin is rejected by build_features' assertion, not silently used."""
    store, rel = data
    o = _origin(rel, "2015-08-12")
    bad = [dict(r, release_ts="2015-08-12T15:00:00-04:00") if r["series"].startswith("crop_production.") and r["release_date"] == o["release_date"] else r
           for r in store]
    with pytest.raises(LeakageError):
        b1_band(bad, o)


def test_b2_carry_forward(data):
    store, rel = data
    assert b2_point(store, rel, _origin(rel, "2015-05-12")) is None  # May: no previous origin
    sep, aug = _origin(rel, "2015-09-11"), _origin(rel, "2015-08-12")
    aug_yield = Fraction(next(r for r in store if r["series"] == "wasde.us_corn.yield" and r["reference_period"] == "2015/16"
                              and r["release_date"] == aug["release_date"] and r["revision_flag"] != "v2_repost")["value"])
    assert b2_point(store, rel, sep) == aug_yield
    nov13 = _origin(rel, "2013-11-08")
    assert b2_point(store, rel, nov13) is not None  # October 2013 has no origin: previous existing origin is September


def test_replicate_synthetic():
    fin = {y: Fraction(1000) for y in range(1995, 2015)}
    fc = {y: Fraction(1050) if y % 2 else Fraction(950) for y in fin}
    r = replicate(fc, fin, 2015)
    assert math.isclose(r["rmse"], 5.0) and r["below"] == 10 and r["above"] == 10 and r["n"] == 20
    with pytest.raises(ValueError):
        replicate(fc, {y: v for y, v in fin.items() if y != 2000}, 2015)


def test_summary_columns_and_finals_b(data):
    store, rel = data
    r = next(r for r in store if r["series"] == "crop_production_summary.us_corn.production_kbu" and r["release_date"] == "2016-01-12")
    cols = parse_corn_table(read_text(ROOT / r["source_file"]), SUMMARY_TITLE, columns=True)
    assert [y for y, _ in cols["production_kbu"]] == [2013, 2014, 2015]
    assert cols["production_kbu"][-1][1] == float(r["value"])  # last column == the stored one
    aug = _origin(rel, "2015-08-12")
    fa, fb = finals_a(store), finals_b(store, aug)
    assert fb[2014] == fa[2014] and 2015 not in fb  # the 2015 crop has no final before its own August issue
    assert fb[2013] != fa[2013]  # restated in the January 2015 Summary


def test_replication_2015_final_b_within_tolerance(data):
    store, rel = data
    aug = _origin(rel, "2015-08-12")
    r = replicate(forecasts(store, 8), finals_b(store, aug), 2015)
    p = printed(store, 2015)
    assert abs(r["rmse"] - float(p["rmse"])) <= 0.1 and (r["below"], r["above"]) == (p["below"], p["above"])
