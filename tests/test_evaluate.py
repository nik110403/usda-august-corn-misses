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
            rows += conformal_rows("B0", o, pt, "bu/ac", resid[: max(0, n - 9)] if n > 9 else [])
            if m >= 8:
                rows.append(ev_row("B1", o, "level", level="9/10", unit="Mbu", point=Fraction(10000), q_lo="-5", q_hi="5",
                                   val_lo=str(Fraction(9500)), val_hi=str(Fraction(10500)), scored="1"))
                rows.append(ev_row("B1y", o, "level", level="9/10", unit="bu/ac", point=pt, q_lo="-5", q_hi="5",
                                   val_lo="95", val_hi="105", scored="1"))
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
    cuts = {"M2vM1_Jun", "B0vM1_Nov"}
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
    assert ev.read_cuts(tmp_path / "missing.csv") == set()
    p = tmp_path / "scope_cuts.csv"
    p.write_text("test_id,reason,logged_utc,code_commit\nM3vM1_Jun,cut,2026-01-01T00:00:00Z,abc\n")
    assert ev.read_cuts(p) == {"M3vM1_Jun"}
    p.write_text("test_id,reason,logged_utc,code_commit\nnope,cut,x,y\n")
    with pytest.raises(SystemExit):
        ev.read_cuts(p)


def test_family_size_and_groups():
    ids = [t[0] for t in ev.FAMILY]
    assert len(ids) == 33 == len(set(ids))
    assert sum(i.startswith("M3vM1") for i in ids) == 5 and sum(i.startswith("M2vM1") for i in ids) == 6
    assert sum(i.startswith("M1cov") for i in ids) == 13 and sum(i.startswith("PRODcov80") for i in ids) == 7
    assert "M1cov80_Aug" not in ids and "M3vM1_Aug" not in ids and "M2vM1_May" not in ids
