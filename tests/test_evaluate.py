"""Evaluation harness (PREREG 4, 5, 10) on SYNTHETIC data only. No real forecast or truth is read, nothing real is scored."""
import csv
from fractions import Fraction

import pytest
from scipy import stats as sps

from cfstore import evaluate as ev
from cfstore import stats
from cfstore.models import COLUMNS, MONTHS, TAUS5, conformal_rows

E, EIGHTY, HALF = ev, Fraction(4, 5), Fraction(1, 2)


# ---- primitives ----------------------------------------------------------------------------------------------------

def test_clopper_pearson_known_values():
    lo, hi = stats.clopper_pearson(18, 22)
    assert (round(lo * 100), round(hi * 100)) == (63, 94)  # PREREG 4.2
    lo, hi = stats.clopper_pearson(19, 22)
    assert (round(lo * 100), round(hi * 100)) == (68, 96)
    assert stats.clopper_pearson(0, 10)[0] == 0 and stats.clopper_pearson(10, 10)[1] == 1
    # exactness: lower bound solves P(X >= x | p_lo) = alpha/2 for the binomial
    lo, hi = stats.clopper_pearson(7, 20, 0.9)
    assert abs(sps.binom.sf(6, 20, lo) - 0.05) < 1e-9 and abs(sps.binom.cdf(7, 20, hi) - 0.05) < 1e-9


def test_each_wording_branch_reachable():
    w = lambda x, n, nom=EIGHTY, m=Fraction(15, 100): stats.wording(x, n, nom, m)[0]
    assert w(20, 24) == "consistent with nominal 80% within ±15 points"
    assert w(8, 22) == "too narrow" and w(22, 22) == "too wide"
    assert w(18, 22) == "not distinguishable from 80% at this sample size, and not shown equivalent"
    assert w(12, 22, HALF, Fraction(15, 100)) == "not distinguishable from 50% at this sample size, and not shown equivalent"
    assert stats.wording(6, 22, HALF)[0] == "too narrow"  # 50% exploratory band, +-15 pp margins


def test_first_wording_unreachable_at_22_and_first_reachable_at_24():
    first = lambda x, n, nom=EIGHTY: stats.wording(x, n, nom)[0].startswith("consistent")
    assert not any(first(x, 22) for x in range(23))
    assert all(not first(x, n) for n in range(1, 24) for x in range(n + 1))
    assert first(20, 24) and not any(first(x, 24) for x in range(24) if x != 20) or first(20, 24)
    assert [x for x in range(25) if first(x, 24)] == [20]
    assert not any(first(x, n, HALF) for n in range(1, 31) for x in range(n + 1))  # no 50% band at any n <= 30


def test_binomial_p_formula():
    assert stats.binom_p_two_sided(0, 2, HALF) == 0.5
    assert stats.binom_p_two_sided(1, 2, HALF) == 1.0
    n, x = 22, 15
    want = min(1.0, 2 * min(sps.binom.cdf(x, n, 0.8), sps.binom.sf(x - 1, n, 0.8)))
    assert abs(stats.binom_p_two_sided(x, n, EIGHTY) - want) < 1e-12
    assert abs(stats.binom_p_two_sided(22, 22, EIGHTY) - min(1, 2 * 0.8**22)) < 1e-12


def test_hln_dm_is_paired_t():
    d = [0.4, -0.1, 0.7, 0.2, 0.5, 0.05, -0.3, 0.6, 0.35, 0.1]
    r = stats.hln_dm(d, "greater")
    t = sps.ttest_1samp(d, 0, alternative="greater")
    assert abs(r["t"] - t.statistic) < 1e-12 and abs(r["p"] - t.pvalue) < 1e-12 and r["df"] == 9
    assert abs(stats.hln_dm(d, "two-sided")["p"] - sps.ttest_1samp(d, 0).pvalue) < 1e-12
    n = len(d)  # the long-run variance form: DM * sqrt((n-1)/n) with gamma0 = (1/n) sum (d - dbar)^2
    dbar = sum(d) / n
    g0 = sum((x - dbar) ** 2 for x in d) / n
    assert abs((dbar / (g0 / n) ** 0.5) * ((n - 1) / n) ** 0.5 - r["t"]) < 1e-12
    assert stats.hln_dm([0.0] * 5)["p"] == 1.0


def test_holm_hand_example_with_cut_entries():
    assert stats.holm([0.01, 0.04, 0.03, 1.0, 1.0]) == pytest.approx([0.05, 0.12, 0.12, 1.0, 1.0])
    # a cut test (p = 1) never makes the adjustment less strict: same raw p, family of 3 vs 3 with one cut
    assert stats.holm([0.02, 0.5, 1.0])[0] == pytest.approx(0.06)
    assert stats.holm([0.02, 0.5])[0] == pytest.approx(0.04)
    assert stats.holm([0.5, 0.2, 0.01]) == pytest.approx([0.5, 0.4, 0.03])


def test_pinball_and_truncated_score():
    assert stats.pinball(2, 0.3) == pytest.approx(0.6) and stats.pinball(-2, 0.3) == pytest.approx(1.4)
    assert stats.pinball(Fraction(1), Fraction(1, 2)) == Fraction(1, 2)
    assert stats.season_loss([0, 0, 0, 0, 0], [0.1, 0.25, 0.5, 0.75, 0.9]) == 0
    assert stats.season_loss([Fraction(2)] * 5, [Fraction(1, 2)] * 5) == 1


def test_mincer_zarnowitz_perfect_and_biased():
    r = stats.mincer_zarnowitz([1.0, 2.0, 3.0, 4.0, 5.0], [1.1, 1.9, 3.2, 3.8, 5.0])
    assert r["p"] > 0.5
    r = stats.mincer_zarnowitz([1.0, 2.0, 3.0, 4.0, 5.0], [3.1, 3.9, 5.2, 5.8, 7.0])
    assert abs(r["a"] - 2.0) < 0.1 and r["p"] < 0.01


# ---- synthetic forecast table --------------------------------------------------------------------------------------

def _origin(season, month):
    return {"release_date": f"{season}-{month:02d}-12", "release_ts": f"{season}-{month:02d}-12T12:00:00-04:00", "revision_flag": "v1"}


def synth(n_out_aug: int = 4, m3_scale: Fraction = Fraction(1, 2)):
    """Rows in the forecasts.csv shape for seasons 2004-2025, every horizon. Training errors are symmetric around 0; the truth
    of every season is exactly the USDA point (error 0), except the last `n_out_aug` seasons at August (error +1000)."""
    rows, yield_, prod = [], {}, {}
    for s in range(2004, 2026):
        pt = Fraction(100 + (s * 7) % 11)
        yield_[s], prod[s] = pt, Fraction(10000)
        for m in range(5, 12):
            if m == 10 and s in (2013, 2025):
                continue  # no October WASDE in 2013 or 2025 (PREREG 1)
            o = _origin(s, m)
            n = s - 1995
            resid = [Fraction(i - n // 2) for i in range(n)]
            rows.append(ev_row("USDA", o, "point", unit="bu/ac", point=pt))
            m1 = conformal_rows("M1", o, pt, "bu/ac", resid)
            rows += m1
            rows += conformal_rows("M2", o, pt, "bu/ac", resid)
            for r in m1:
                if r["kind"] == "tau" and Fraction(r["tau"]) in TAUS5:
                    q = Fraction(r["q"]) * m3_scale
                    rows.append(dict(r, model="M3", q=str(q), val=str(pt * (1 + q / 100)), m3_lp_status="0"))
            rows += conformal_rows("PROD", o, Fraction(10000), "Mbu", resid)
            rows += conformal_rows("B0", o, pt, "bu/ac", resid[: n - 5], min_n=9)  # B0 has s - 2000 own errors: scored from 2009
            if m >= 8:
                b1ok = "0" if (s, m) == (2008, 10) else "1"  # B1 unavailable at Oct 2008 (PREREG-AMENDMENTS 2026-10-16 item 2)
                rows.append(ev_row("B1", o, "level", level="9/10", unit="Mbu", point=Fraction(10000), q_lo="-5", q_hi="5",
                                   val_lo=str(Fraction(9500)), val_hi=str(Fraction(10500)), scored=b1ok))
                rows.append(ev_row("B1y", o, "level", level="9/10", unit="bu/ac", point=pt, q_lo="-5", q_hi="5",
                                   val_lo="95", val_hi="105", scored=b1ok))
            if m > 5:
                rows.append(ev_row("B2", o, "point", unit="bu/ac", point=pt))
    for s in range(2026 - n_out_aug, 2026):  # Aug misses
        yield_[s] = Fraction(100 + (s * 7) % 11) * 11
    return rows, ev.Truths(yield_, prod)


def ev_row(model, o, kind, **kw):
    r = {c: "" for c in COLUMNS}
    r.update(model=model, horizon=MONTHS[int(o["release_date"][5:7])], season=o["release_date"][:4], origin_date=o["release_date"],
             origin_ts=o["release_ts"], kind=kind, origin_flag=o["revision_flag"], in_tests="1", scored="1")
    r.update({k: str(v) for k, v in kw.items()})
    return r


@pytest.fixture(scope="module")
def table():
    return synth()


def test_end_to_end_driver_on_synthetic_table(table, tmp_path):
    rows, truths = table
    cuts = {"M2vM1_Jun", "M1cov50_May"}  # a plain set of test ids is still accepted
    out = ev.evaluate(rows, truths, cuts, leakage=[{"group": "leakage test 1", "passed": 3, "failed": 0, "overlap_origins": "24 of 215"}])
    prim = out["primary"]
    # the truth is 11x the USDA point (a miss) for the last 4 seasons at EVERY horizon in this fixture, so the 80% band covers 18 of 22 everywhere
    assert (prim["x"], prim["n"]) == (18, 22)
    lo, hi = stats.clopper_pearson(18, 22)
    assert prim["wording"] == "not distinguishable from 80% at this sample size, and not shown equivalent"
    assert abs(prim["cp_lo"] - lo) < 1e-12
    assert any("Wording" in ln for ln in out["primary.txt"])
    # 33 tests in the exact table order; cut tests enter Holm with p = 1
    fam = out["family.csv"]
    assert len(fam) == 33 and [r["test_id"] for r in fam] == [t[0] for t in ev.FAMILY]
    assert fam[0]["test_id"] == "M3vM1_Jun" and fam[4]["test_id"] == "M3vM1_Nov" and fam[5]["test_id"] == "M2vM1_Jun"
    assert fam[11]["test_id"] == "M1cov50_May" and fam[-1]["test_id"] == "B0vM1_Nov"
    cutrows = {r["test_id"]: r for r in fam if r["status"] == "cut"}
    assert set(cutrows) == cuts and all(r["p_raw"] == "1" and r["p_holm"] == "1" for r in cutrows.values())
    p_raw = [float(r["p_raw"]) for r in fam]
    assert [float(r["p_holm"]) for r in fam] == pytest.approx(stats.holm(p_raw), abs=1e-5)
    # Oct tests use their own seasons: here all 22 exist, and the M1 80% counts are the same 18/22
    cov80 = next(r for r in out["coverage.csv"] if r["model"] == "M1" and r["horizon"] == "Aug" and r["level"] == "4/5" and r["truth"] == "primary")
    assert (cov80["x"], cov80["n"], cov80["status"]) == (18, 22, "primary")
    assert next(r for r in out["coverage.csv"] if r["model"] == "M1" and r["horizon"] == "Jun" and r["level"] == "4/5")["status"] == "exploratory"
    assert all(r["status"] == "descriptive" for r in out["coverage.csv"] if r["model"] == "B1")
    assert all(r["wording"] == "" for r in out["coverage.csv"] if r["level"] == "9/10")
    # M3 has narrower taus: the loss differential is negative at the 4 missed seasons, positive at the 18 hits
    d = ev.Data(rows, truths)
    l1, l3 = d.season_losses("M1", "Aug"), d.season_losses("M3", "Aug")
    diffs = [float(l1[s] - l3[s]) for s in sorted(l1)]
    ref = stats.hln_dm(diffs, "greater")
    assert prim["m3_p"] == pytest.approx(ref["p"]) and prim["m3_n"] == 22
    # hand-check one season loss: season 2004 Aug, n = 9, truth error 0
    qs = {Fraction(r["tau"]): Fraction(r["q"]) for r in rows if r["model"] == "M1" and r["season"] == "2004" and r["horizon"] == "Aug"
          and r["kind"] == "tau" and Fraction(r["tau"]) in TAUS5}
    assert l1[2004] == sum(stats.pinball(-q, t) for t, q in qs.items()) / 5 and l1[2004] > 0
    # all the other tables exist and carry no real data
    for name in ("scores.csv", "pit.csv", "sharpness.csv", "skill.csv", "mincer_zarnowitz.csv", "stress_years.csv", "split_half.csv",
                 "sensitivity.csv", "models_tried.csv", "leakage.csv"):
        assert out[name], name
    assert any(r["item"] == "M4" and r["status"] == "dropped" for r in out["models_tried.csv"])
    assert any("Good-Irwin" in r["note"] for r in out["skill.csv"])
    # outputs carry the code commit and a timestamp
    files = ev.write_outputs(out, tmp_path / "eval", "abc123", "2026-01-01T00:00:00Z")
    assert {p.name for p in files} >= {"primary.txt", "family.csv", "coverage.csv"}
    for p in files:
        assert "abc123" in p.read_text() and "2026-01-01T00:00:00Z" in p.read_text()


def test_primary_dm_cut_uses_the_not_run_wording(table):
    rows, truths = table
    out = ev.evaluate(rows, truths, {ev.PRIMARY_DM_ID})
    assert out["primary"]["m3_wording"] == "the pre-registered comparison was not run"


def test_m3_not_distinguishable_when_identical(table):
    rows, truths = table
    rows2, truths2 = synth(m3_scale=Fraction(1))  # M3 == M1: d_s = 0 everywhere
    out = ev.evaluate(rows2, truths2, set())
    assert out["primary"]["m3_wording"] == "M3 and M1 are not distinguishable at August with 22 seasons."


def test_pit_counts_sum_to_seasons(table):
    rows, truths = table
    pit = ev.pit_table(ev.Data(rows, truths))
    assert all(sum(r[f"bin{i}"] for i in range(6)) == r["n"] for r in pit)


def test_corrected_release_exclusion_counts():
    rows, truths = synth()
    for r in rows:
        if r["season"] == "2010" and r["horizon"] == "Jul":
            r["origin_flag"] = "v1_corrected_only"
        if r["model"] == "M3" and r["season"] == "2012" and r["horizon"] == "Sep":
            r["m3_x_flag"] = "v1_corrected_only"
    assert ev.corrected_exclusions(rows) == {(2010, "Jul"), (2012, "Sep")}
    out = ev.evaluate(rows, truths, set())
    assert any(r.get("dropped_origins") == 2 for r in out["sensitivity.csv"])
    d = ev.Data(rows, truths, {(2010, "Jul")})
    assert len(d.seasons("M1", "Jul", EIGHTY)) == 21


def test_missing_sensitivity_truth_is_handled(table):
    rows, truths = table
    out = ev.evaluate(rows, truths, set())
    assert not any(r["truth"] == "sensitivity" for r in out["coverage.csv"])
    assert any("unavailable" in ln for ln in out["primary.txt"])
    sens = ev.Truths(truths.yield_, truths.prod, {s: v for s, v in truths.yield_.items()}, {s: v for s, v in truths.prod.items()})
    out2 = ev.evaluate(rows, sens, set())
    assert any(r["truth"] == "sensitivity" for r in out2["coverage.csv"])


def test_refuses_without_valid_prereg_hash(tmp_path):
    import hashlib
    (tmp_path / "PREREG.md").write_text("frozen\n")
    (tmp_path / "PREREG.sha256").write_text(hashlib.sha256(b"frozen\n").hexdigest() + "  PREREG.md\n")
    ev.verify_prereg(tmp_path)  # ok; the parent has no PREREG.sha256 here (tmp_path.parent is not a project)
    (tmp_path / "PREREG.md").write_text("edited\n")
    with pytest.raises(SystemExit):
        ev.verify_prereg(tmp_path)


def test_scope_cuts_file_default_empty_and_validated(tmp_path):
    assert not ev.read_cuts(tmp_path / "missing.csv").any
    p = tmp_path / "scope_cuts.csv"
    hdr = "item,kind,target,timestamp,commit\n"
    p.write_text(hdr + "1,optional,F10,2026-01-01T00:00:00Z,abc\n2,test,M2vM1_Aug,2026-01-01T00:00:00Z,abc\n")
    assert ev.read_cuts(p).tests == {"M2vM1_Aug"}
    for bad in ("1,test,nope,2026-01-01T00:00:00Z,abc", "1,test,leakage_test_1,2026-01-01T00:00:00Z,abc",
                "2,test,M2vM1_Jun,yesterday,abc", "2,test,M2vM1_Jun,2026-01-01T00:00:00Z,",
                "2,model,M1,2026-01-01T00:00:00Z,abc", "2,model,B0,2026-01-01T00:00:00Z,abc",
                "5,horizon,Aug,2026-01-01T00:00:00Z,abc", "5,horizon,Nov,2026-01-01T00:00:00Z,abc",
                "7,model,M2,2026-01-01T00:00:00Z,abc", "3,truth,primary,2026-01-01T00:00:00Z,abc", "4,table,B1_80,2026-01-01T00:00:00Z,abc",
                "1,widget,x,2026-01-01T00:00:00Z,abc"):
        p.write_text(hdr + bad + "\n")
        with pytest.raises(SystemExit):
            ev.read_cuts(p)
    p.write_text("test_id,reason,logged_utc,code_commit\nM3vM1_Jun,cut,2026-01-01T00:00:00Z,abc\n")
    with pytest.raises(SystemExit):  # the old schema is refused
        ev.read_cuts(p)


def test_family_size_and_groups():
    ids = [t[0] for t in ev.FAMILY]
    assert len(ids) == 33 == len(set(ids))
    assert sum(i.startswith("M3vM1") for i in ids) == 5 and sum(i.startswith("M2vM1") for i in ids) == 6
    assert sum(i.startswith("M1cov") for i in ids) == 13 and sum(i.startswith("PRODcov80") for i in ids) == 7
    assert "M1cov80_Aug" not in ids and "M3vM1_Aug" not in ids and "M2vM1_May" not in ids


# ---- F6a fix round 1: scope cuts remove scoring, B0 threshold, count assertions, gates ------------------------------------

def _with_sens(truths):
    return ev.Truths(truths.yield_, truths.prod, dict(truths.yield_), dict(truths.prod))


def _written(out, tmp_path):
    ev.write_outputs(out, tmp_path / "eval", "abc", "2026-01-01T00:00:00Z")
    return {p.name: p for p in (tmp_path / "eval").iterdir()}


def _csv_rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


M2M3_TESTS = {t[0] for t in ev.FAMILY if t[2] in ("M2", "M3")} | {ev.PRIMARY_DM_ID}


def test_tribune_case_cut_primary_dm_and_all_m2_m3_ids_leaves_no_m2_m3_number(tmp_path):
    rows, truths = synth()
    out = ev.evaluate(rows, _with_sens(truths), set(M2M3_TESTS))
    files = _written(out, tmp_path)
    for name, path in files.items():
        if name in ("models_tried.csv", "family.csv"):
            continue
        text = path.read_text()
        assert "M3 vs M1" not in text and "M2 vs M1" not in text, name
        if name.endswith(".csv"):
            assert not [r for r in _csv_rows(path) if r.get("model") in ("M2", "M3")], name
    sens = files["sensitivity.csv"].read_text()
    assert "M3" not in sens and "HLN-DM" not in sens
    prim = files["primary.txt"].read_text()
    assert "the pre-registered comparison was not run" in prim and "p = " not in prim and "t = " not in prim
    assert out["primary"]["m3_wording"] == "the pre-registered comparison was not run" and "m3_p" not in out["primary"]
    fam = {r["test_id"]: r for r in out["family.csv"]}
    assert all(fam[t]["status"] == "cut" and fam[t]["p_raw"] == "1" and fam[t]["p_holm"] == "1" for t in M2M3_TESTS - {ev.PRIMARY_DM_ID})
    cut = ev.make_cuts(tests=M2M3_TESTS)
    assert cut.models == {"M2", "M3"}  # every test of a model cut -> the model is cut
    assert {r["status"] for r in out["models_tried.csv"] if r["item"] in ("M2", "M3")} == {"cut"}


def test_cut_model_m3_removes_all_m3_scoring(tmp_path):
    rows, truths = synth()
    out = ev.evaluate(rows, _with_sens(truths), ev.make_cuts(models={"M3"}))
    files = _written(out, tmp_path)
    assert any(r["model"] == "M2" for r in out["scores.csv"]) and not any(r["model"] == "M3" for r in out["scores.csv"])
    for name, path in files.items():
        if name.endswith(".csv") and name not in ("models_tried.csv", "family.csv"):
            assert not [r for r in _csv_rows(path) if r.get("model") == "M3"], name
    assert "M3 vs M1" not in files["sensitivity.csv"].read_text()
    assert {r["status"] for r in out["family.csv"] if r["model"] == "M3"} == {"cut"}
    assert any(r["model"] == "M2" for r in out["skill.csv"]) and not any(r["model"] == "M3" for r in out["skill.csv"])
    assert out["primary"]["m3_wording"] == "the pre-registered comparison was not run"


def test_cut_model_m2_removes_all_m2_scoring(tmp_path):
    rows, truths = synth()
    out = ev.evaluate(rows, _with_sens(truths), ev.make_cuts(models={"M2"}))
    files = _written(out, tmp_path)
    for name, path in files.items():
        if name.endswith(".csv") and name not in ("models_tried.csv", "family.csv"):
            assert not [r for r in _csv_rows(path) if r.get("model") == "M2"], name
    assert {r["status"] for r in out["family.csv"] if r["model"] == "M2"} == {"cut"}
    assert any(r["model"] == "M3" for r in out["scores.csv"]) and "m3_p" in out["primary"]


def test_cut_truth_sensitivity_removes_every_sensitivity_row(tmp_path):
    rows, truths = synth()
    sens = _with_sens(truths)
    base = ev.evaluate(rows, sens, set())
    assert any(r["truth"] == "sensitivity" for r in base["coverage.csv"])
    out = ev.evaluate(rows, sens, ev.make_cuts(truths={"sensitivity"}))
    assert not any(r["truth"] == "sensitivity" for r in out["coverage.csv"])
    assert any("cut (scope cut)" in ln for ln in out["primary.txt"])
    assert {r["status"] for r in out["models_tried.csv"] if r["item"] == "variant: Quick Stats truth"} == {"cut"}


def test_cut_table_b1_90_removes_b1_and_b1y_rows(tmp_path):
    rows, truths = synth()
    out = ev.evaluate(rows, truths, ev.make_cuts(tables={"B1_90"}))
    for name in ("coverage.csv", "sharpness.csv"):
        assert not [r for r in out[name] if r["model"] in ("B1", "B1y")], name
    assert any(r["model"] == "B1" for r in ev.evaluate(rows, truths, set())["coverage.csv"])
    assert {r["status"] for r in out["models_tried.csv"] if r["item"] == "B1"} == {"cut"}


@pytest.mark.parametrize("h", ["May", "Jun", "Jul", "Oct"])
def test_cut_horizon_removes_that_horizon_everywhere(h):
    rows, truths = synth()
    out = ev.evaluate(rows, _with_sens(truths), ev.make_cuts(horizons={h}))
    for name, rs in out.items():
        if name.endswith(".csv") and name not in ("models_tried.csv", "family.csv", "leakage.csv"):
            assert not [r for r in rs if r.get("horizon") == h], name
    assert all(r["status"] == "cut" for r in out["family.csv"] if r["horizon"] == h)
    assert any(r["horizon"] == "Aug" for r in out["coverage.csv"])


def test_cut_single_cov_test_removes_its_band_and_its_holm_entry():
    rows, truths = synth()
    out = ev.evaluate(rows, truths, {"M1cov50_Jun"})
    assert not [r for r in out["coverage.csv"] if (r["model"], r["horizon"], r["level"]) == ("M1", "Jun", "1/2")]
    assert not [r for r in out["split_half.csv"] if (r["model"], r["horizon"], r["level"]) == ("M1", "Jun", "1/2")]
    assert any((r["model"], r["horizon"], r["level"]) == ("M1", "Jun", "4/5") for r in out["coverage.csv"])
    assert {r["test_id"]: r["status"] for r in out["family.csv"]}["M1cov50_Jun"] == "cut"


def test_cut_single_dm_test_gives_no_p_value_for_that_comparison():
    rows, truths = synth()
    out = ev.evaluate(rows, truths, {"M3vM1_Jul"})
    assert not [r for r in out["sensitivity.csv"] if r["horizon"] == "Jul" and "HLN-DM" in r["measure"]]
    assert [r for r in out["sensitivity.csv"] if r["horizon"] == "Sep" and "HLN-DM" in r["measure"]]
    out = ev.evaluate(rows, truths, {ev.PRIMARY_DM_ID})
    assert not [r for r in out["sensitivity.csv"] if r["horizon"] == "Aug" and "HLN-DM" in r["measure"]]
    assert "m3_p" not in out["primary"]


def test_cut_optional_has_no_effect_on_numbers():
    rows, truths = synth()
    a = ev.evaluate(rows, truths, set())
    b = ev.evaluate(rows, truths, ev.make_cuts(optional={"extension", "writeup"}))
    assert a["family.csv"] == b["family.csv"] and a["coverage.csv"] == b["coverage.csv"]


def test_uncuttable_targets_are_refused():
    for kw in ({"models": {"M1"}}, {"models": {"B0"}}, {"models": {"PROD"}}, {"horizons": {"Aug"}}, {"truths": {"primary"}},
               {"tables": {"coverage"}}, {"tests": {"leakage_test_3"}}, {"tests": {"M1cov80_Aug"}}):
        with pytest.raises(SystemExit):
            ev.make_cuts(**kw)


def test_scope_cuts_csv_roundtrip_applies_cuts(tmp_path):
    p = tmp_path / "scope_cuts.csv"
    p.write_text(_cuts_csv(["1,optional,extension", "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + [f"5,horizon,{h}" for h in ev.CUT_HORIZONS]
                           + ["5,test,M1cov50_Jun"]))
    c = ev.read_cuts(p)
    assert (c.models, c.horizons, c.truths, c.tables, c.optional) == ({"M2"}, set(ev.CUT_HORIZONS), {"sensitivity"}, {"B1_90"}, {"extension"})
    assert "M1cov50_Jun" in c.tests and "M2vM1_Aug" in c.tests and "M3vM1_Oct" in c.tests and "M1cov80_Oct" in c.tests
    rows, truths = synth()
    out = ev.evaluate(rows, _with_sens(truths), c)
    assert not any(r["model"] in ("M2", "B1", "B1y") for r in out["coverage.csv"]) and not any(r["horizon"] == "Oct" for r in out["scores.csv"])


def _cuts_csv(rows):
    return "item,kind,target,timestamp,commit\n" + "".join(f"{r},2026-10-16T10:00:00Z,abc123\n" for r in rows)


def _read(tmp_path, rows):
    p = tmp_path / "scope_cuts.csv"
    p.write_text(_cuts_csv(rows))
    return ev.read_cuts(p)


H5 = [f"5,horizon,{h}" for h in ev.CUT_HORIZONS]
OPT = "1,optional,F10"


def test_test_cuts_need_a_strategy_item_and_the_matching_number(tmp_path):
    # fix2 #1: ids with no STRATEGY 10 item, and rows whose item number does not match the id's item, are refused
    for tid in ("B0vM1_Aug", "B0vM1_Nov", "M1cov50_Aug", "M1cov50_Sep", "M1cov50_Nov", "PRODcov80_Aug", "PRODcov80_Nov", "M1cov80_Aug", "M1cov80_Sep"):
        with pytest.raises(SystemExit):
            _read(tmp_path, [OPT, f"2,test,{tid}"])
        with pytest.raises(SystemExit):
            ev.make_cuts(tests={tid})
    with pytest.raises(SystemExit, match="item 5"):
        _read(tmp_path, [OPT, "2,test,M1cov50_Jun"])  # a May-Oct id is item 5, not 2
    with pytest.raises(SystemExit, match="item 2"):
        _read(tmp_path, [OPT, "7,test,M2vM1_Aug"])  # M2 id is item 2 (or 5 if at a non-Aug/Sep/Nov horizon), not 7
    with pytest.raises(SystemExit, match="item 7"):
        _read(tmp_path, [OPT, "2,test,M3vM1_Sep"])
    assert _read(tmp_path, [OPT, "2,test,M2vM1_Aug"]).tests == {"M2vM1_Aug"}
    assert _read(tmp_path, [OPT] + H5[:0] + ["2,test,M2vM1_Jun"]).tests == {"M2vM1_Jun"}  # item 2 or 5 for an M2 id at Jun
    assert _read(tmp_path, [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + H5 + ["6,optional,writeup", "7,test,M3vM1_Sep"]).tests >= {"M3vM1_Sep"}
    assert ev.TEST_ITEMS["M1cov50_Jun"] == {"5"} and ev.TEST_ITEMS[ev.PRIMARY_DM_ID] == {"7"} and "B0vM1_Aug" not in ev.TEST_ITEMS


def test_cuts_refused_out_of_order_and_item_5_all_or_nothing(tmp_path):
    for rows in (["2,model,M2"], [OPT, "3,truth,sensitivity"], [OPT, "2,model,M2", "4,table,B1_90"], [OPT, "7,model,M3"],
                 [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + H5 + ["7,model,M3"],  # item 6 not cut
                 [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90", "5,horizon,Oct"],  # 5 partial
                 [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + H5[:3] + ["5,test,M1cov50_Jun"],
                 ["2,test,M2vM1_Aug"]):  # item 1 not cut
        with pytest.raises(SystemExit):
            _read(tmp_path, rows)
    with pytest.raises(SystemExit, match="all-or-nothing"):
        _read(tmp_path, [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + H5[:3])
    c = _read(tmp_path, [OPT, "2,model,M2", "3,truth,sensitivity", "4,table,B1_90"] + H5 + ["6,optional,writeup", "7,model,M3"])  # legal full order
    assert c.models == {"M2", "M3"} and c.horizons == set(ev.CUT_HORIZONS)
    assert _read(tmp_path, [OPT]).optional == {"F10"} and _read(tmp_path, [OPT, "2,model,M2"]).models == {"M2"}


def test_b0_scored_only_from_nine_own_errors_at_every_level():
    o = _origin(2012, 8)
    for n, want in ((8, "0"), (9, "1")):
        rows = conformal_rows("B0", o, Fraction(100), "bu/ac", [Fraction(i) for i in range(n)], min_n=9)
        assert {r["scored"] for r in rows if r["kind"] == "level" and Fraction(r["level"]) == HALF} == {want}
        assert {r["scored"] for r in rows if r["kind"] == "tau" and Fraction(r["tau"]) in TAUS5} == {want}
    rows, truths = synth()
    d = ev.Data(rows, truths)
    assert d.seasons("B0", "Aug", HALF) == d.seasons("B0", "Aug", EIGHTY) == list(range(2009, 2026))


def test_preflight_fails_on_dropped_truth_and_on_wrong_counts():
    rows, truths = synth()
    t2 = ev.Truths({s: v for s, v in truths.yield_.items() if s != 2010}, truths.prod)
    with pytest.raises(ev.PreregCountError, match="truth missing for 2010"):
        ev.evaluate(rows, t2, set())
    with pytest.raises(ev.PreregCountError, match="M1 Aug"):
        ev.evaluate([r for r in rows if not (r["model"] == "M1" and r["season"] == "2010" and r["horizon"] == "Aug")], truths, set())
    with pytest.raises(ev.PreregCountError, match="M1 Oct"):
        ev.evaluate([r for r in rows if not (r["season"] == "2010" and r["horizon"] == "Oct")], truths, set())
    ev.evaluate([r for r in rows if not (r["season"] == "2010" and r["horizon"] == "Oct")], truths, ev.make_cuts(horizons={"Oct"}))  # cut: fine


def test_preflight_requires_b0_17_and_b1_oct_19():
    rows, truths = synth()
    with pytest.raises(ev.PreregCountError, match="B0 Aug"):
        ev.evaluate([r for r in rows if not (r["model"] == "B0" and r["season"] == "2020" and r["horizon"] == "Aug")], truths, set())
    with pytest.raises(ev.PreregCountError, match="B1 Oct"):
        ev.evaluate([dict(r, scored="0") if (r["model"] == "B1" and r["season"] == "2010" and r["horizon"] == "Oct") else r for r in rows],
                    truths, set())


def test_preflight_never_calls_covered(monkeypatch):
    # fix2 #3: availability only; no band / truth comparison before the gate passes
    def boom(*a, **k):
        raise AssertionError("covered() called before the preflight gate")
    monkeypatch.setattr(ev.Data, "covered", boom)
    monkeypatch.setattr(ev.Data, "count", boom)
    rows, truths = synth()
    d = ev.Data(rows, truths)
    ev.preflight(d, truths)
    assert any("M1" in ln for ln in ev.avail_lines(d))
    t2 = ev.Truths({s: v for s, v in truths.yield_.items() if s != 2010}, truths.prod)
    with pytest.raises(ev.PreregCountError):  # the failing gate raises its own error, not a scoring AssertionError
        ev.evaluate(rows, t2, set())


def test_v2_forecast_set_goes_through_the_same_preflight():
    rows, truths = synth()
    ev.evaluate(rows, truths, set(), rows)
    bad = [r for r in rows if not (r["model"] == "M1" and r["season"] == "2010" and r["horizon"] == "Aug")]
    with pytest.raises(ev.PreregCountError, match="v2 re-post forecasts"):
        ev.evaluate(rows, truths, set(), bad)


def test_missing_quickstats_truth_fails_unless_sensitivity_cut_is_logged(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(SystemExit, match="quickstats_truth.csv is missing"):
        ev.load_truths(tmp_path, [])
    with pytest.raises(SystemExit):
        ev.load_truths(tmp_path, [], ev.make_cuts(models={"M2"}))
    t = ev.load_truths(tmp_path, [], ev.make_cuts(truths={"sensitivity"}))
    assert t.sens_yield is None


def test_forecast_hash_recorded_in_outputs_and_checked(tmp_path):
    f = tmp_path / "out"
    f.mkdir()
    (f / "forecasts.csv").write_text("a,b\n1,2\n")
    rows, h = ev.read_hashed(f / "forecasts.csv")
    assert rows == [{"a": "1", "b": "2"}] and len(h) == 64
    ev.check_unchanged(tmp_path, {"forecasts_sha256": h})
    ev.write_outputs({"x.csv": [{"k": "v"}], "p.txt": ["line"]}, tmp_path / "eval", "abc", "2026-01-01T00:00:00Z", {"forecasts_sha256": h})
    assert _csv_rows(tmp_path / "eval" / "x.csv")[0]["forecasts_sha256"] == h and f"forecasts_sha256: {h}" in (tmp_path / "eval" / "p.txt").read_text()
    (f / "forecasts.csv").write_text("a,b\n1,3\n")
    with pytest.raises(SystemExit, match="changed while"):
        ev.check_unchanged(tmp_path, {"forecasts_sha256": h})


def test_leakage_failure_aborts_with_no_outputs(tmp_path):
    from types import SimpleNamespace as NS
    bad = NS(returncode=1, stdout="PASSED tests/test_leakage.py::test1_a\nFAILED tests/test_leakage.py::test2_b - boom\n")
    with pytest.raises(SystemExit, match="leakage tests did not all pass"):
        ev.parse_leakage(bad, tmp_path)
    with pytest.raises(SystemExit):
        ev.parse_leakage(NS(returncode=0, stdout=""), tmp_path)  # nothing collected is a failure too
    ok = NS(returncode=0, stdout="PASSED tests/test_leakage.py::test1_a\nPASSED tests/test_models.py::test_x\n")
    assert {r["group"] for r in ev.parse_leakage(ok, tmp_path)} == {"leakage test 1", "model-layer leakage"}


def test_stale_forecasts_are_refused():
    ident = {"code_tree": "t1", "store_sha256": "s1", "build_commit": "c"}
    ok = [{"code_tree": "t1", "store_sha256": "s1"}] * 3
    ev.check_fresh(ok, ident)
    for bad in ([{"code_tree": "t0", "store_sha256": "s1"}], [{"code_tree": "t1", "store_sha256": "s0"}], [{"code_tree": "t1"}], [],
                ok + [{"code_tree": "t1", "store_sha256": "s0"}]):
        with pytest.raises(SystemExit):
            ev.check_fresh(bad, ident)
    with pytest.raises(SystemExit):
        ev.check_fresh([{"code_tree": "t1+dirty", "store_sha256": "s1"}], dict(ident, code_tree="t1+dirty"))
