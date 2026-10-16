"""Evaluation harness (SPEC F6, PREREG 4, 5, 10): `python -m cfstore evaluate` (`make evaluate`).

WRITTEN AND TESTED ON SYNTHETIC DATA ONLY; NOT RUN on real seasons at the time of writing (checkpoint 2 comes first, PREREG 8).
Reads out/forecasts.csv and the truths and computes, in ONE run, every PREREG 4, 5 and 10 number. Writes out/eval/*.csv and
out/eval/primary.txt, each carrying the code commit and a UTC timestamp. Refuses to run if PREREG.sha256 does not verify, if a leakage test fails, if out/forecasts.csv is stale (built from other code
or another store), or if a PREREG season count does not hold. Scope cuts (scope_cuts.csv) are applied to the forecast rows and
truths BEFORE any score is computed: a cut model / horizon / truth / table / test produces no row anywhere in out/eval/.

Percent-error space with exact rationals for coverage (PREREG 2); pinball loss rho_tau(u) = u (tau - 1{u<0}) on percent errors
u = 100 (final - quantile_bu/ac) / USDA_h, per-season loss = mean over the 5 tau, whose mean over seasons is the *truncated
quantile score* (not CRPS).
"""
import csv
import hashlib
import io
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from . import stats
from .models import MONTHS, TAUS5

HORIZONS = [MONTHS[m] for m in range(5, 12)]
NON_AUG = [h for h in HORIZONS if h != "Aug"]
STRESS = (2012, 2019, 2020)
HALF = Fraction(1, 2)
EIGHTY = Fraction(4, 5)
NINETY = Fraction(9, 10)
GOOD_IRWIN_NOTE = ("Good-Irwin disclosure (PREREG-AMENDMENTS 2026-10-16 item 1): USDA's own Aug-Nov point was already compared with "
                   "final in the F5 RMSE table (commit 61f8f34), overlapping the scored seasons in 10 / 19 / 20 crop years for the "
                   "windows 1995-2014 / 2005-2024 / 1995-2024; this RMSE is not blind to that earlier look.")

# PREREG 5 table, in order: (test_id, kind, model_or_pair, horizon, level)
FAMILY: list[tuple] = (
    [(f"M3vM1_{h}", "dm", "M3", h, None) for h in ("Jun", "Jul", "Sep", "Oct", "Nov")]
    + [(f"M2vM1_{h}", "dm", "M2", h, None) for h in ("Jun", "Jul", "Aug", "Sep", "Oct", "Nov")]
    + [(f"M1cov50_{h}", "cov", "M1", h, HALF) for h in HORIZONS]
    + [(f"M1cov80_{h}", "cov", "M1", h, EIGHTY) for h in NON_AUG]
    + [(f"PRODcov80_{h}", "cov", "PROD", h, EIGHTY) for h in HORIZONS]
    + [(f"B0vM1_{h}", "dm2", "B0", h, None) for h in ("Aug", "Nov")])
PRIMARY_DM_ID = "M3vM1_Aug_primary"
assert len(FAMILY) == 33

# PREREG scored seasons per horizon (PREREG 1: no October WASDE in 2013 or 2025) and the other season counts asserted before scoring
EXPECTED_N = {h: (20 if h == "Oct" else 22) for h in HORIZONS}
EXPECTED_B1 = {"Aug": 22, "Sep": 22, "Oct": 19, "Nov": 22}  # PREREG-AMENDMENTS 2026-10-16 item 2 (no B1 at Oct 2008)
EXPECTED_B0VM1 = {"Aug": 17, "Nov": 17}  # B0 scored from 2009 (>= 9 own OOS errors): seasons 2009-2025


# ---- scope cuts (STRATEGY 10, PREREG 8) ----------------------------------------------------------------------------

CUT_COLUMNS = ["item", "kind", "target", "timestamp", "commit"]
M3_TESTS = frozenset([t[0] for t in FAMILY if t[2] == "M3"] + [PRIMARY_DM_ID])
M2_TESTS = frozenset(t[0] for t in FAMILY if t[2] == "M2")
CUT_HORIZONS = ("May", "Jun", "Jul", "Oct")  # STRATEGY 10 item 5: all four together or none
CUTTABLE = {"model": ("M2", "M3"), "truth": ("sensitivity",), "table": ("B1_90",), "horizon": CUT_HORIZONS,
            "optional": ("extension", "forward_test", "F10", "writeup")}
CUT_ITEM = {("model", "M2"): "2", ("model", "M3"): "7", ("truth", "sensitivity"): "3", ("table", "B1_90"): "4",
            ("optional", "extension"): "1", ("optional", "forward_test"): "1", ("optional", "F10"): "1",  # F10 = "no optional item chosen"
            ("optional", "writeup"): "6"}  # horizon -> "5"


def _test_items() -> dict[str, frozenset]:
    """Family test id -> the STRATEGY 10 items under which it may be cut: M2 ids item 2, M3 ids (and the primary comparison) item 7,
    any id at May / Jun / Jul / Oct item 5 (M1 and PROD coverage, M2, M3). Ids with no item (B0vM1_*, anything at Aug / Sep / Nov
    that is not M2 / M3) are not cuttable as tests."""
    out: dict[str, set] = {PRIMARY_DM_ID: {"7"}}
    for tid, _kind, model, h, _lev in FAMILY:
        it = out.setdefault(tid, set())
        it |= {"M2": {"2"}, "M3": {"7"}}.get(model, set())
        if h in CUT_HORIZONS:
            it.add("5")
    return {k: frozenset(v) for k, v in out.items() if v}


TEST_ITEMS = _test_items()


@dataclass(frozen=True)
class Cuts:
    """Normalised scope cuts. A cut M2/M3 cuts all its family tests (and M3 the primary comparison); a model whose every test is
    cut is a cut model; a cut horizon cuts that horizon's family tests."""
    tests: frozenset = frozenset()
    models: frozenset = frozenset()
    truths: frozenset = frozenset()
    tables: frozenset = frozenset()
    horizons: frozenset = frozenset()
    optional: frozenset = frozenset()
    rows: tuple = ()

    @property
    def any(self) -> bool:
        return bool(self.tests or self.models or self.truths or self.tables or self.horizons or self.optional)

    def dm_cut(self, other: str, h: str) -> bool:
        tid = PRIMARY_DM_ID if (other == "M3" and h == "Aug") else f"{other}vM1_{h}"
        return tid in self.tests or other in self.models or h in self.horizons

    def cov_cut(self, model: str, h: str, lev: Fraction) -> bool:
        return (f"{model}cov{int(lev * 100)}_{h}" in self.tests) or h in self.horizons


def make_cuts(tests=(), models=(), truths=(), tables=(), horizons=(), optional=(), rows=()) -> Cuts:
    tests, models = set(tests), set(models)
    unknown = tests - {t[0] for t in FAMILY} - {PRIMARY_DM_ID}
    if unknown:
        lk = sorted(u for u in unknown if "leak" in u.lower())
        raise SystemExit(f"REFUSING scope cut {lk}: leakage tests are never cut" if lk else f"unknown test ids in scope cuts: {sorted(unknown)}")
    no_item = sorted(tests - set(TEST_ITEMS))
    if no_item:
        raise SystemExit(f"REFUSING scope cut {no_item}: no STRATEGY 10 item covers these tests (only M2 / M3 ids and the "
                         "May / Jun / Jul / Oct ids are cuttable)")
    for kind, vals in (("model", models), ("truth", truths), ("table", tables), ("horizon", horizons), ("optional", optional)):
        bad = set(vals) - set(CUTTABLE[kind])
        if bad:
            raise SystemExit(f"REFUSING scope cut {kind} {sorted(bad)}: not cuttable (cuttable {kind}: {list(CUTTABLE[kind])}; "
                             "the primary calibration object, M1's 80% band at August, and the leakage tests are never cut)")
    if "M3" in models:
        tests |= M3_TESTS
    if "M2" in models:
        tests |= M2_TESTS
    tests |= {t[0] for t in FAMILY if t[3] in horizons}
    if M3_TESTS <= tests:
        models.add("M3")
    if M2_TESTS <= tests:
        models.add("M2")
    return Cuts(*(frozenset(x) for x in (tests, models, truths, tables, horizons, optional)), rows=tuple(rows))


def as_cuts(x) -> Cuts:
    return x if isinstance(x, Cuts) else make_cuts(tests=x)


def apply_cuts(fc_rows: list[dict], truths: "Truths", cuts: Cuts) -> "tuple[list[dict], Truths]":
    """The ONLY place a cut removes data: rows of a cut model / horizon / B1 table and the sensitivity truth are dropped here,
    before any computation, so no score of a cut item can reach any output."""
    drop_models = set(cuts.models) | ({"B1", "B1y"} if "B1_90" in cuts.tables else set())
    rows = [r for r in fc_rows if r["model"] not in drop_models and r["horizon"] not in cuts.horizons]
    if "sensitivity" in cuts.truths:
        truths = Truths(truths.yield_, truths.prod)
    return rows, truths


# ---- inputs --------------------------------------------------------------------------------------------------------

@dataclass
class Truths:
    """Final yield (bu/ac) and production (Mbu) per crop season. `sens_*` = Quick Stats latest value (pseudo-vintage) or None."""
    yield_: dict[int, Fraction]
    prod: dict[int, Fraction]
    sens_yield: dict[int, Fraction] | None = None
    sens_prod: dict[int, Fraction] | None = None

    def get(self, unit: str, season: int, which: str = "primary") -> Fraction | None:
        d = {("bu/ac", "primary"): self.yield_, ("Mbu", "primary"): self.prod,
             ("bu/ac", "sensitivity"): self.sens_yield, ("Mbu", "sensitivity"): self.sens_prod}[(unit, which)]
        return None if d is None else d.get(season)


def F(x) -> Fraction | None:
    return Fraction(x) if x not in ("", None) else None


class Data:
    """Forecast rows indexed by (model, horizon, season[, tau/level]); `exclude` is a set of (season, horizon) origins dropped."""

    def __init__(self, rows: list[dict], truths: Truths, exclude=frozenset(), cuts: Cuts | None = None):
        self.t, self.exclude, self.cuts = truths, set(exclude), cuts or Cuts()
        self.lv, self.tau, self.pt = {}, {}, {}
        for r in rows:
            key = (r["model"], r["horizon"], int(r["season"]))
            if (key[2], key[1]) in self.exclude:
                continue
            if r["kind"] == "level":
                if self.cuts.cov_cut(key[0], key[1], Fraction(r["level"])):
                    continue  # a cut coverage test removes its band everywhere (no coverage, sharpness, stress, split-half row)
                self.lv[key + (Fraction(r["level"]),)] = r
            elif r["kind"] == "tau":
                self.tau[key + (Fraction(r["tau"]),)] = r
            else:
                self.pt[key] = r
        # M3 has quantiles only: derive its 50% and 80% bands from tau .25/.75 and .10/.90 (descriptive, not a PREREG object)
        for (m, h, s, tau), r in list(self.tau.items()):
            if m == "M3" and tau == Fraction(1, 10):
                for lev, (a, b) in ((EIGHTY, (Fraction(1, 10), Fraction(9, 10))), (HALF, (Fraction(1, 4), Fraction(3, 4)))):
                    lo, hi = self.tau.get((m, h, s, a)), self.tau.get((m, h, s, b))
                    if lo and hi and lo["scored"] == "1" and hi["scored"] == "1":
                        self.lv[(m, h, s, lev)] = dict(r, kind="level", level=str(lev), q_lo=lo["q"], q_hi=hi["q"],
                                                       val_lo=lo["val"], val_hi=hi["val"], k_lo="", k_hi="", exp_cov="",
                                                       note="derived from taus", scored="1")

    def models(self) -> list[str]:
        return sorted({k[0] for k in self.lv} | {k[0] for k in self.tau} | {k[0] for k in self.pt})

    def seasons(self, model: str, h: str, level: Fraction) -> list[int]:
        return sorted(s for (m, hh, s, lv), r in self.lv.items() if m == model and hh == h and lv == level and r["scored"] == "1")

    def covered(self, model: str, h: str, s: int, level: Fraction, which: str = "primary") -> bool | None:
        r = self.lv[(model, h, s, level)]
        final = self.t.get(r["unit"], s, which)
        if final is None:
            return None
        point = Fraction(r["point"])
        e = 100 * (final - point) / point  # exact percent error, compared with exact rational quantiles (PREREG 2)
        return Fraction(r["q_lo"]) <= e <= Fraction(r["q_hi"])

    def count(self, model: str, h: str, level: Fraction, which: str = "primary") -> tuple[int, int, list[int]]:
        res = [(s, self.covered(model, h, s, level, which)) for s in self.seasons(model, h, level)]
        res = [(s, c) for s, c in res if c is not None]
        return sum(c for _, c in res), len(res), [s for s, _ in res]

    def exp_cov(self, model: str, h: str, level: Fraction, seasons: list[int]) -> Fraction | None:
        v = [F(self.lv[(model, h, s, level)]["exp_cov"]) for s in seasons]
        return None if not v or any(x is None for x in v) else sum(v) / len(v)

    def scored_taus(self, model: str, h: str) -> set[int]:
        """Seasons whose 5 tau rows all exist and are scored (rows only; no truth, no arithmetic)."""
        return {s for (m, hh, s, tau0) in self.tau if m == model and hh == h and tau0 == TAUS5[0]
                and all(self.tau.get((m, h, s, t)) is not None and self.tau[(m, h, s, t)]["scored"] == "1" for t in TAUS5)}

    def loss_inputs(self, model: str, h: str, which: str = "primary") -> dict[int, tuple]:
        """{season: (rows, final, base)} for the seasons the per-season loss can be computed on; base = USDA_h (the percent base
        of M1) for yield models, the production point for PROD."""
        out = {}
        for s in sorted(self.scored_taus(model, h)):
            rows = [self.tau[(model, h, s, t)] for t in TAUS5]
            final = self.t.get(rows[0]["unit"], s, which)
            if final is None:
                continue
            base = Fraction(rows[0]["point"]) if model == "PROD" else (Fraction(self.pt[("USDA", h, s)]["point"]) if ("USDA", h, s) in self.pt else None)
            if base is None:
                continue
            out[s] = (rows, final, base)
        return out

    def season_losses(self, model: str, h: str, which: str = "primary") -> dict[int, Fraction]:
        """Per-season truncated quantile score (mean pinball over the 5 tau) on percent errors u = 100 (final - q_bu/ac) / base."""
        out = {}
        for s, (rows, final, base) in self.loss_inputs(model, h, which).items():
            us = [100 * (final - Fraction(r["val"])) / base for r in rows]
            out[s] = stats.season_loss(us, list(TAUS5))
        return out

    def pinball_by_tau(self, model: str, h: str, which: str = "primary") -> dict[Fraction, list[Fraction]]:
        out = defaultdict(list)
        for s in self.season_losses(model, h, which):
            r0 = self.tau[(model, h, s, TAUS5[0])]
            final = self.t.get(r0["unit"], s, which)
            base = Fraction(r0["point"]) if model == "PROD" else Fraction(self.pt[("USDA", h, s)]["point"])
            for t in TAUS5:
                out[t].append(stats.pinball(100 * (final - Fraction(self.tau[(model, h, s, t)]["val"])) / base, t))
        return out


# ---- tables --------------------------------------------------------------------------------------------------------

def _dm(d: Data, m_other: str, h: str, alt: str) -> dict | None:
    """HLN-DM of M1 against `m_other` on the seasons where both have all 5 tau scored; d_s = L(M1) - L(other)."""
    if d.cuts.dm_cut(m_other, h):
        return None  # the pre-registered comparison was cut: it is not run anywhere, so no p-value exists in any output
    a, b = d.season_losses("M1", h), d.season_losses(m_other, h)
    seas = sorted(set(a) & set(b))
    if len(seas) < 2:
        return None
    diffs = [float(a[s] - b[s]) for s in seas]
    r = stats.hln_dm(diffs, alt)
    r["seasons"] = seas
    return r


def primary_tables(d: Data) -> tuple[list[str], dict]:
    """PREREG 4.1-4.4. Returns (lines of primary.txt, machine-readable dict)."""
    x, n, seas = d.count("M1", "Aug", EIGHTY)
    word, (lo, hi) = stats.wording(x, n, EIGHTY)
    lines = ["PREREG section 4 primary analysis (computed in one run with every section 5 and 10 number)",
             "4.1/4.2 Primary calibration object: M1 80% band at the August origin, primary truth (January first-final).",
             f"  x = {x} of n = {n} seasons covered ({x / n:.1%}); two-sided 90% Clopper-Pearson interval [{lo:.1%}, {hi:.1%}].",
             f"  Wording: \"{word}\""]
    prim = {"x": x, "n": n, "cp_lo": lo, "cp_hi": hi, "wording": word}
    if PRIMARY_DM_ID in d.cuts.tests:
        w = stats.WORD_M3_CUT
        lines += ["4.4 M3 against M1 at August (one-sided HLN-DM):", f"  Wording: \"{w}\""]
        prim["m3_wording"] = w
    else:
        r = _dm(d, "M3", "Aug", "greater")
        if r is None:
            lines += ["4.4 M3 against M1 at August: M3 rows unavailable; not computed."]
            prim["m3_wording"] = "not computed"
        else:
            w = (stats.WORD_M3_REJECT.format(p=f"{r['p']:.3g}", n=r["n"]) if r["p"] <= 0.05
                 else stats.WORD_M3_NOREJ.format(n=r["n"]))
            lines += ["4.4 M3 against M1 at August, one-sided HLN-DM (H1: E[d] > 0, d = L(M1) - L(M3)), alpha = 0.05:",
                      f"  n = {r['n']}, mean d = {r['dbar']:.4f}, t = {r['t']:.3f} on {r['df']} df, p = {r['p']:.4g}",
                      f"  Wording: \"{w}\""]
            prim.update(m3_wording=w, m3_p=r["p"], m3_n=r["n"], m3_dbar=r["dbar"], m3_t=r["t"])
    return lines, prim


def coverage_table(d: Data) -> list[dict]:
    """Every model x level x horizon: n, x, exact interval, expected coverage; wording only for the exploratory bands (+-15 pp)
    and the primary object; 90% and B1 are descriptive (no wording, no test). Sensitivity-truth rows are descriptive."""
    rows = []
    levels = {"M1": (HALF, EIGHTY, NINETY), "M2": (HALF, EIGHTY, NINETY), "M3": (HALF, EIGHTY), "B0": (HALF, EIGHTY, NINETY),
              "PROD": (HALF, EIGHTY, NINETY), "B1": (NINETY,), "B1y": (NINETY,)}
    for model, levs in levels.items():
        for h in HORIZONS:
            for lev in levs:
                for which in ("primary", "sensitivity"):
                    x, n, seas = d.count(model, h, lev, which)
                    if n == 0:
                        continue
                    lo, hi = stats.clopper_pearson(x, n)
                    exp = d.exp_cov(model, h, lev, seas)
                    status = "descriptive"
                    word = ""
                    if which == "primary" and ((model == "M1" and lev in (HALF, EIGHTY)) or (model == "PROD" and lev == EIGHTY)):
                        status = "primary" if (model, h, lev) == ("M1", "Aug", EIGHTY) else "exploratory"
                        word = stats.wording(x, n, lev)[0]
                    rows.append({"model": model, "horizon": h, "level": str(lev), "truth": which, "n": n, "x": x,
                                 "coverage": f"{x / n:.4f}", "cp90_lo": f"{lo:.4f}", "cp90_hi": f"{hi:.4f}",
                                 "nominal": f"{float(lev):.2f}", "expected_coverage": "" if exp is None else f"{float(exp):.4f}",
                                 "status": status, "wording": word,
                                 "object": "derived yield band (not defined by PREREG 3)" if model == "B1y" else
                                           "bands from taus (descriptive)" if model == "M3" else ""})
    return rows


def family_table(d: Data) -> list[dict]:
    """The fixed 33-test family (PREREG 5) in table order; cut or uncomputable tests enter Holm with p = 1."""
    raw = []
    for tid, kind, model, h, lev in FAMILY:
        rec = {"test_id": tid, "horizon": h, "model": model, "label": "exploratory", "n": "", "statistic": "", "p_raw": 1.0,
               "status": "computed"}
        if tid in d.cuts.tests:
            rec["status"] = "cut"
        elif kind == "cov":
            x, n, _ = d.count(model, h, lev)
            if n == 0:
                rec["status"] = "unavailable"
            else:
                rec.update(n=n, statistic=f"x={x}", p_raw=stats.binom_p_two_sided(x, n, lev))
        else:
            r = _dm(d, model, h, "greater" if kind == "dm" else "two-sided")
            if r is None:
                rec["status"] = "unavailable"
            else:
                rec.update(n=r["n"], statistic=f"dbar={r['dbar']:.5g}; t={r['t']:.4g}", p_raw=r["p"])
        raw.append(rec)
    for rec, adj in zip(raw, stats.holm([r["p_raw"] for r in raw])):
        rec["p_holm"] = adj
        rec["p_raw"] = f"{rec['p_raw']:.6g}"
        rec["p_holm"] = f"{adj:.6g}"
    return raw


def scores_table(d: Data) -> list[dict]:
    rows = []
    for model in ("M1", "M2", "M3", "B0", "PROD"):
        for h in HORIZONS:
            by = d.pinball_by_tau(model, h)
            if not by:
                continue
            n = len(by[TAUS5[0]])
            means = {t: sum(by[t]) / n for t in TAUS5}
            for t in TAUS5:
                rows.append({"model": model, "horizon": h, "measure": f"pinball_tau_{float(t):g}", "value": f"{float(means[t]):.6g}", "n": n})
            rows.append({"model": model, "horizon": h, "measure": "truncated_quantile_score (mean over 5 tau; not CRPS)",
                         "value": f"{float(sum(means.values()) / 5):.6g}", "n": n})
    return rows


def pit_table(d: Data) -> list[dict]:
    """Rank of the final among the 5 tau quantiles (bin = number of quantiles strictly below the final), counts by horizon."""
    rows = []
    for model in ("M1", "M2", "M3", "B0"):
        for h in HORIZONS:
            cnt = [0] * 6
            for s in d.season_losses(model, h):
                r0 = d.tau[(model, h, s, TAUS5[0])]
                final = d.t.get(r0["unit"], s)
                cnt[sum(Fraction(d.tau[(model, h, s, t)]["val"]) < final for t in TAUS5)] += 1
            if sum(cnt):
                rows.append({"model": model, "horizon": h, "n": sum(cnt),
                             **{f"bin{i}": c for i, c in enumerate(cnt)},
                             "bins": "bin i = number of the 5 quantiles (tau .10 .25 .50 .75 .90) below the final"})
    return rows


def sharpness_table(d: Data) -> list[dict]:
    rows = []
    for model in ("M1", "M2", "M3", "B0", "PROD", "B1", "B1y"):
        for h in HORIZONS:
            for lev in (HALF, EIGHTY, NINETY):
                ws = [Fraction(d.lv[(model, h, s, lev)]["val_hi"]) - Fraction(d.lv[(model, h, s, lev)]["val_lo"])
                      for s in d.seasons(model, h, lev)]
                if ws:
                    rows.append({"model": model, "horizon": h, "level": str(lev), "n": len(ws),
                                 "mean_width": f"{float(sum(ws) / len(ws)):.4f}", "unit": d.lv[(model, h, d.seasons(model, h, lev)[0], lev)]["unit"]})
    return rows


def _points(d: Data, h: str) -> dict[str, dict[int, Fraction]]:
    pts = {"USDA": {s: Fraction(r["point"]) for (m, hh, s), r in d.pt.items() if m == "USDA" and hh == h},
           "B2": {s: Fraction(r["point"]) for (m, hh, s), r in d.pt.items() if m == "B2" and hh == h}}
    for model in ("M1", "M2", "M3"):
        pts[model] = {s: Fraction(r["val"]) for (m, hh, s, t), r in d.tau.items()
                      if m == model and hh == h and t == HALF and r["scored"] == "1"}
    pts["B0"] = {s: Fraction(r["point"]) for (m, hh, s, t), r in d.tau.items() if m == "B0" and hh == h and t == HALF}
    return pts


def skill_and_mz(d: Data) -> tuple[list[dict], list[dict]]:
    """MAE and RMSE (bu/ac) of each model's point against the primary truth, as skill relative to USDA's point (= B1's point
    in Aug-Nov); Mincer-Zarnowitz regression final = a + b * point with the joint (0, 1) F test."""
    skill, mz = [], []
    for h in HORIZONS:
        pts = _points(d, h)
        for model, p in pts.items():
            seas = sorted(s for s in p if s in pts["USDA"] and d.t.get("bu/ac", s) is not None)
            if len(seas) < 3:
                continue
            fin = {s: d.t.get("bu/ac", s) for s in seas}
            err = [float(fin[s] - p[s]) for s in seas]
            ref = [float(fin[s] - pts["USDA"][s]) for s in seas]
            mae, rmse = sum(map(abs, err)) / len(err), (sum(e * e for e in err) / len(err)) ** 0.5
            mae0, rmse0 = sum(map(abs, ref)) / len(ref), (sum(e * e for e in ref) / len(ref)) ** 0.5
            skill.append({"model": model, "horizon": h, "n": len(seas), "mae": f"{mae:.4f}", "rmse": f"{rmse:.4f}",
                          "mae_skill_vs_usda_point": f"{1 - mae / mae0:.4f}", "rmse_skill_vs_usda_point": f"{1 - rmse / rmse0:.4f}",
                          "note": GOOD_IRWIN_NOTE})
            r = stats.mincer_zarnowitz([float(p[s]) for s in seas], [float(fin[s]) for s in seas])
            mz.append({"model": model, "horizon": h, "n": r["n"], "a": f"{r['a']:.4f}", "b": f"{r['b']:.4f}",
                       "F_joint_a0_b1": f"{r['F']:.4f}", "p": f"{r['p']:.4g}", "status": "descriptive"})
    return skill, mz


def stress_table(d: Data) -> list[dict]:
    rows = []
    for model in ("M1", "M2", "M3", "B0", "PROD"):
        for h in HORIZONS:
            for s in STRESS:
                loss = d.season_losses(model, h).get(s)
                rec = {"model": model, "horizon": h, "season": s, "tqs": "" if loss is None else f"{float(loss):.5g}"}
                for lev in (HALF, EIGHTY):
                    if (model, h, s, lev) in d.lv and d.lv[(model, h, s, lev)]["scored"] == "1":
                        rec[f"covered_{float(lev):g}"] = int(bool(d.covered(model, h, s, lev)))
                if loss is not None or len(rec) > 4:
                    rows.append(rec)
    return rows


def split_half(d: Data) -> list[dict]:
    rows = []
    for model in ("M1", "PROD"):
        for h in HORIZONS:
            for lev in (HALF, EIGHTY):
                seas = d.count(model, h, lev)[2]
                if len(seas) < 4:
                    continue
                cut = len(seas) // 2
                for name, part in (("first", seas[:cut]), ("second", seas[cut:])):
                    x = sum(bool(d.covered(model, h, s, lev)) for s in part)
                    rows.append({"model": model, "horizon": h, "level": str(lev), "half": name,
                                 "seasons": f"{part[0]}-{part[-1]}", "n": len(part), "x": x, "coverage": f"{x / len(part):.4f}"})
    return rows


def sensitivity_summary(d: Data, label: str, extra: dict | None = None) -> list[dict]:
    """M1 coverage at 50/80/90 and the M3-vs-M1 HLN-DM by horizon on this (restricted or alternative) forecast set."""
    rows = []
    for h in HORIZONS:
        for lev in (HALF, EIGHTY, NINETY):
            x, n, _ = d.count("M1", h, lev)
            if n:
                lo, hi = stats.clopper_pearson(x, n)
                rows.append({"run": label, "measure": f"M1 coverage {float(lev):g}", "horizon": h, "n": n, "x": x,
                             "value": f"{x / n:.4f}", "cp90": f"[{lo:.3f}, {hi:.3f}]", **(extra or {})})
        r = _dm(d, "M3", h, "greater") if h != "May" else None
        if r:
            rows.append({"run": label, "measure": "M3 vs M1 HLN-DM one-sided p (descriptive)", "horizon": h, "n": r["n"], "x": "",
                         "value": f"{r['p']:.4g}", "cp90": "", **(extra or {})})
    return rows


def models_tried(cuts: Cuts) -> list[dict]:
    out = []
    for item, kind, status, what, note in MODELS_TRIED:
        name = item.split(":")[0] if kind == "model" else item
        if name in cuts.models:
            status, note = "cut", "scope cut logged before any score (STRATEGY 10)"
        elif (name in ("B1",) and "B1_90" in cuts.tables) or (item == "variant: Quick Stats truth" and "sensitivity" in cuts.truths):
            status, note = "cut", "scope cut logged before any score (STRATEGY 10)"
        out.append(dict(zip(("item", "kind", "status", "what", "note"), (item, kind, status, what, note))))
    out += [dict(item=f"horizon: {h}", kind="horizon", status="cut", what="exploratory horizon dropped",
                 note="scope cut logged before any score (STRATEGY 10)") for h in sorted(cuts.horizons)]
    return out


def corrected_exclusions(fc_rows: list[dict]) -> set[tuple[int, str]]:
    """PREREG 1: drop an origin whose own WASDE (origin_flag) or whose M3 regressor (m3_x_flag) is a corrected-only release."""
    bad = "v1_corrected_only"
    ex = {(int(r["season"]), r["horizon"]) for r in fc_rows if r["origin_flag"] == bad or r.get("m3_x_flag") == bad}
    return ex


MODELS_TRIED = [
    ("B0", "model", "run", "linear trend baseline (PREREG 3)", ""),
    ("B1", "model", "run", "USDA's printed 90% production band, descriptive; yield band derived", ""),
    ("B2", "model", "run", "carry-forward of the previous origin's USDA yield, revision diagnostics only", ""),
    ("M1", "model", "run", "split-conformal on USDA's point (primary calibration object)", ""),
    ("M2", "model", "run", "revision-bias shift on M1 (exploratory)", ""),
    ("M3", "model", "run", "linear quantile regression on detrended Good+Excellent share (primary comparison model)", ""),
    ("M4", "model", "dropped", "weather model", "dropped before any score (STRATEGY 5.2, PREREG 3)"),
    ("PROD", "model", "run", "production band: conformal quantiles of percent production errors (PREREG 6)", ""),
    ("STU", "step", "run (not scored)", "stocks-to-use step through USDA's balance sheet (PREREG 6)", ""),
    ("feature: revision r", "feature", "run", "M2 regressor", ""),
    ("feature: Good+Excellent share (national)", "feature", "run", "M3 regressor; detrended by an OLS trend on season year", ""),
    ("feature: rel_ci90_pct", "feature", "run", "B1 band half-width", ""),
    ("variant: v2 re-post data", "sensitivity", "run", "forecasts rebuilt reading logged v2 re-posts", "descriptive"),
    ("variant: corrected-release drop", "sensitivity", "run", "origins on corrected-only releases dropped", "descriptive"),
    ("variant: Quick Stats truth", "sensitivity", "run if available", "latest-value truth, pseudo-vintage", "descriptive; no test"),
]


# ---- I/O -----------------------------------------------------------------------------------------------------------

def code_commit(root: Path) -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def verify_prereg(root: Path) -> None:
    """Refuse to run unless PREREG.md hashes to PREREG.sha256 (and the project-level copy, when present)."""
    for base in (root, root.parent):
        sf, md = base / "PREREG.sha256", base / "PREREG.md"
        if not sf.exists() and base != root:
            continue
        want = sf.read_text().split()[0]
        got = hashlib.sha256(md.read_bytes()).hexdigest()
        if got != want:
            raise SystemExit(f"REFUSING TO RUN: {md} sha256 {got} != {sf} {want}")


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def read_cuts(path: Path) -> Cuts:
    """scope_cuts.csv (item,kind,target,timestamp,commit): `item` is the STRATEGY 10 list number, `kind` one of test / model /
    truth / table / horizon / optional, `target` the test id, model {M2, M3}, truth {sensitivity}, table {B1_90}, horizon
    {May, Jun, Jul, Oct} or optional {extension, forward_test, writeup}. The checkpoint fills it before the first run; default
    empty. The primary calibration object (M1 80% Aug), M1, B0, PROD, the leakage tests and every other target are refused."""
    if not path.exists():
        return Cuts()
    rows = read_csv(path)
    if rows and set(rows[0]) != set(CUT_COLUMNS):
        raise SystemExit(f"{path}: columns must be {CUT_COLUMNS}, got {list(rows[0])}")
    acc: dict[str, set] = {k: set() for k in ("test", "model", "truth", "table", "horizon", "optional")}
    for r in rows:
        if not all((r.get(c) or "").strip() for c in CUT_COLUMNS):
            raise SystemExit(f"{path}: every cut needs item, kind, target, timestamp and commit: {r}")
        kind, target = r["kind"], r["target"]
        if kind not in acc:
            raise SystemExit(f"{path}: unknown cut kind {kind!r}")
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["timestamp"]):
            raise SystemExit(f"{path}: timestamp must be UTC ISO (YYYY-MM-DDTHH:MM:SSZ): {r['timestamp']!r}")
        if kind == "test":
            allowed = TEST_ITEMS.get(target)
            if allowed is not None and r["item"] not in allowed:
                raise SystemExit(f"{path}: test {target} is STRATEGY 10 item {' or '.join(sorted(allowed))}, not {r['item']!r}")
        else:
            want = "5" if kind == "horizon" else CUT_ITEM.get((kind, target))
            if want is not None and r["item"] != want:
                raise SystemExit(f"{path}: cut {kind} {target} is STRATEGY 10 item {want}, not {r['item']!r}")
        acc[kind].add(target)
    cuts = make_cuts(tests=acc["test"], models=acc["model"], truths=acc["truth"], tables=acc["table"], horizons=acc["horizon"],
                     optional=acc["optional"], rows=tuple(tuple(r[c] for c in CUT_COLUMNS) for r in rows))
    check_cut_order(path, rows, cuts)
    return cuts


def check_cut_order(path, rows: list[dict], cuts: Cuts) -> None:
    """STRATEGY 10: cut from the top. A row of item k is refused unless items 1..k-1 are all cut (item 1 counts as cut once any
    item-1 optional target is logged, e.g. `1,optional,F10` when no optional item was chosen). Item 5 is all-or-nothing."""
    done = {1: bool(cuts.optional & {"extension", "forward_test", "F10"}), 2: "M2" in cuts.models, 3: "sensitivity" in cuts.truths,
            4: "B1_90" in cuts.tables, 5: cuts.horizons == frozenset(CUT_HORIZONS), 6: "writeup" in cuts.optional, 7: "M3" in cuts.models}
    if cuts.horizons and not done[5] or any(r["item"] == "5" for r in rows) and not done[5]:
        raise SystemExit(f"{path}: item 5 is all-or-nothing: horizons {list(CUT_HORIZONS)} must all be cut together, got {sorted(cuts.horizons)}")
    for r in rows:
        k = int(r["item"]) if r["item"].isdigit() else 0
        if not 1 <= k <= 7:
            raise SystemExit(f"{path}: item must be a STRATEGY 10 number 1-7: {r['item']!r}")
        missing = [j for j in range(1, k) if not done[j]]
        if missing:
            raise SystemExit(f"{path}: cut of item {k} ({r['kind']} {r['target']}) refused: STRATEGY 10 cuts from the top, "
                             f"items {missing} are not cut")


def load_truths(root: Path, store: list[dict], cuts: Cuts | None = None) -> Truths:
    """Primary truths from the store; sensitivity truths from data/quickstats_truth.csv. A missing file fails the run unless the
    sensitivity truth is cut (a logged `3,truth,sensitivity` row): nothing is dropped silently."""
    from .skeleton import truth_rows
    qs = root / "data" / "quickstats_truth.csv"
    if not qs.exists() and "sensitivity" not in (cuts or Cuts()).truths:
        raise SystemExit(f"REFUSING TO RUN: {qs} is missing and no `3,truth,sensitivity` cut is logged in scope_cuts.csv; "
                         "run `make quickstats` or log the cut")
    y, p = {}, {}
    for s in range(1995, 2026):
        t = truth_rows(store, s)
        if t is None:
            continue
        y[s] = Fraction(t["value"])
        pr = [r for r in store if r["series"] == "wasde.us_corn.production" and r["reference_period"] == t["reference_period"]
              and r["release_date"] == t["release_date"] and r["revision_flag"] != "v2_repost"]
        assert len(pr) == 1, (s, len(pr))
        p[s] = Fraction(pr[0]["value"])
    sy = sp = None
    if qs.exists():
        rows = read_csv(qs)
        sy = {int(r["crop_year"]): Fraction(r["yield_bu_ac"]) for r in rows}
        sp = {int(r["crop_year"]): Fraction(r["production_bu"]) / 10**6 for r in rows}  # BU -> Mbu
    return Truths(y, p, sy, sp)


def run_leakage_tests(root: Path) -> list[dict]:
    """The four leakage tests (STRATEGY 7) plus the model-layer ones: run pytest and group by test number."""
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_leakage.py", "tests/test_models.py", "-q", "-rA",
                        "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
    return parse_leakage(p, root)


def parse_leakage(p, root: Path) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0})
    for line in p.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) (\S+?)::(\w+)", line)
        if m:
            g = re.match(r"test([1-4])_", m.group(3))
            key = f"leakage test {g.group(1)}" if g else ("model-layer leakage" if "test_models" in m.group(2) else "leakage (other)")
            groups[key]["passed" if m.group(1) == "PASSED" else "failed"] += 1
    failed = sum(g["failed"] for g in groups.values())
    if p.returncode != 0 or failed or not groups:
        raise SystemExit(f"REFUSING TO RUN: leakage tests did not all pass (pytest exit {p.returncode}, {failed} failed, "
                         f"{sum(g['passed'] for g in groups.values())} passed); no output written")
    overlap = root / "out" / "crop_progress_overlap_count.txt"
    first = overlap.read_text().splitlines()[0] if overlap.exists() else "overlap count file missing"
    return [{"group": k, **v, "overlap_origins": first} for k, v in sorted(groups.items())]


class PreregCountError(Exception):
    """A PREREG season count or a family-test computability condition does not hold; the run fails, no output is written."""


def n_scored_truth(d: Data, model: str, h: str, lev: Fraction) -> tuple[int, int]:
    """(seasons with a scored band row, of those with a truth value). Availability only: no band, point or truth comparison."""
    sea = d.seasons(model, h, lev)
    return len(sea), sum(d.t.get(d.lv[(model, h, s, lev)]["unit"], s) is not None for s in sea)


def avail_lines(d: Data, label: str = "primary") -> list[str]:
    """The counts `preflight` asserts, as text: per model x horizon, scored seasons and seasons with a truth value."""
    out = [f"availability counts ({label}; scored forecast rows with a truth value; no score computed):"]
    for h in HORIZONS:
        parts = []
        for model in ("M1", "PROD", "M2", "M3", "B0"):
            tau, use = len(d.scored_taus(model, h)), len(d.loss_inputs(model, h))
            if tau or model != "M3":
                parts.append(f"{model} quantiles {tau}/{use}")
        for model in ("M1", "PROD", "M2"):
            for lev in (HALF, EIGHTY):
                n, nt = n_scored_truth(d, model, h, lev)
                if n:
                    parts.append(f"{model} {float(lev):.0%} band {n}/{nt}")
        out.append(f"  {h}: PREREG {EXPECTED_N[h]}; " + "; ".join(parts))
    for model in ("B1", "B1y"):
        out.append(f"  {model} 90% band (Aug/Sep/Oct/Nov): " + " ".join(f"{n_scored_truth(d, model, h, NINETY)[0]}/{n_scored_truth(d, model, h, NINETY)[1]}" for h in EXPECTED_B1)
                   + f"; amendments {' '.join(str(v) for v in EXPECTED_B1.values())}")
    return out


def preflight(d: Data, truths: Truths, label: str = "primary") -> None:
    """PREREG season counts asserted BEFORE any score (PREREG 1, 3, 4, 5; PREREG-AMENDMENTS 2026-10-16 item 2). Availability only:
    a season counts if it has a scored forecast row and a truth value; nothing is compared with a band or a point (no `covered()`).
    A season that has scored rows but no truth / no USDA base (it would be silently dropped from a score) is a failure, as is
    any family test that is not computable and not cut."""
    cuts, bad = d.cuts, []
    for s in range(2004, 2026):
        for unit, name in (("bu/ac", "yield"), ("Mbu", "production")):
            if truths.get(unit, s) is None:
                bad.append(f"primary {name} truth missing for {s}")
        if truths.sens_yield is not None and (truths.get("bu/ac", s, "sensitivity") is None or truths.get("Mbu", s, "sensitivity") is None):
            bad.append(f"sensitivity truth missing for {s}")
    for h in HORIZONS:
        if h in cuts.horizons:
            continue
        want = EXPECTED_N[h]
        for model in ("M1", "PROD", "M2", "M3", "B0"):
            if model in cuts.models or (model == "M3" and h == "May"):
                continue
            rows_s, usable = sorted(d.scored_taus(model, h)), sorted(d.loss_inputs(model, h))
            if model == "B0":
                if h in EXPECTED_B0VM1 and len(rows_s) != EXPECTED_B0VM1[h]:
                    bad.append(f"B0 {h}: {len(rows_s)} scored seasons, PREREG 3 says {EXPECTED_B0VM1[h]}")
            elif len(rows_s) != want:
                bad.append(f"{model} {h}: {len(rows_s)} scored seasons, PREREG says {want}")
            if rows_s != usable:
                bad.append(f"{model} {h}: seasons {sorted(set(rows_s) - set(usable))} would be silently dropped (no truth or USDA base)")
        for model in ("M1", "PROD", "M2"):
            if model in cuts.models:
                continue
            for lev in (HALF, EIGHTY):
                if cuts.cov_cut(model, h, lev):
                    continue
                n, nt = n_scored_truth(d, model, h, lev)
                if n != want or nt != n:
                    bad.append(f"{model} {h} {lev}: {n} scored / {nt} with truth, PREREG says {want}")
    if "B1_90" not in cuts.tables:
        for model in ("B1", "B1y"):
            for h, want in EXPECTED_B1.items():
                if h in cuts.horizons:
                    continue
                n, nt = n_scored_truth(d, model, h, NINETY)
                if n != want or nt != n:
                    bad.append(f"{model} {h}: {n} scored seasons / {nt} with truth, amendments say {want}")
    n = n_scored_truth(d, "M1", "Aug", EIGHTY)[1]
    if n != 22:
        bad.append(f"primary calibration object M1 80% Aug: n = {n}, PREREG says 22")
    for tid, kind, model, h, lev in FAMILY:
        if tid in cuts.tests:
            continue
        if kind == "cov":
            ok = len(d.seasons(model, h, lev)) == EXPECTED_N[h]
        else:
            both = set(d.loss_inputs("M1", h)) & set(d.loss_inputs(model, h))
            ok = len(both) == (EXPECTED_B0VM1[h] if kind == "dm2" else EXPECTED_N[h])
        if not ok:
            bad.append(f"family test {tid} is not computable on the PREREG season count")
    if PRIMARY_DM_ID not in cuts.tests and len(set(d.loss_inputs("M1", "Aug")) & set(d.loss_inputs("M3", "Aug"))) != 22:
        bad.append(f"{PRIMARY_DM_ID} is not computable on 22 seasons")
    if bad:
        raise PreregCountError(f"PREREG count check failed ({label}), nothing written:\n  " + "\n  ".join(bad))


def evaluate(fc_rows, truths, cuts=frozenset(), fc_v2_rows=None, leakage=None) -> dict:
    """Everything in PREREG 4, 5 and 10 from forecast rows + truths. Returns {file name: rows} and {'primary.txt': lines}.
    Order: cuts are applied to rows and truths first, then the PREREG counts are asserted, then (only then) scores are computed."""
    cuts = as_cuts(cuts)
    fc_rows, truths = apply_cuts(fc_rows, truths, cuts)
    if fc_v2_rows:
        fc_v2_rows, _ = apply_cuts(fc_v2_rows, truths, cuts)
    d = Data(fc_rows, truths, cuts=cuts)
    preflight(d, truths)
    if fc_v2_rows:
        preflight(Data(fc_v2_rows, truths, cuts=cuts), truths, "v2 re-post forecasts")  # same expected counts as the primary set
    lines, prim = primary_tables(d)
    skill, mz = skill_and_mz(d)
    ex = corrected_exclusions(fc_rows)
    d_ex = Data(fc_rows, truths, ex, cuts)
    sens = sensitivity_summary(d_ex, "corrected-release drop", {"dropped_origins": len(ex)})
    if fc_v2_rows:
        sens += sensitivity_summary(Data(fc_v2_rows, truths, cuts=cuts), "v2 re-post data")
    if cuts.any:
        lines += ["", "Scope cuts applied before any score (scope_cuts.csv; STRATEGY 10): "
                  + "; ".join(f"{k} {sorted(v)}" for k, v in (("tests", cuts.tests), ("models", cuts.models), ("truths", cuts.truths),
                                                              ("tables", cuts.tables), ("horizons", cuts.horizons), ("optional", cuts.optional)) if v)]
    if "sensitivity" in cuts.truths:
        lines.append("Sensitivity truth (Quick Stats latest) cut (scope cut): no sensitivity-truth row exists.")
    elif truths.sens_yield is None:
        lines.append("Sensitivity truth (Quick Stats latest) unavailable: descriptive sensitivity-truth rows omitted.")
    lines += ["", f"Corrected-release sensitivity: {len(ex)} origins dropped (own WASDE or M3 regressor from a corrected-only release); "
              "training errors kept.", "See out/eval/*.csv for sections 5 and 10 (family.csv: 33 tests with Holm-adjusted p)."]
    lines.append(GOOD_IRWIN_NOTE)
    out = {"coverage.csv": coverage_table(d), "family.csv": family_table(d), "scores.csv": scores_table(d),
           "pit.csv": pit_table(d), "sharpness.csv": sharpness_table(d), "skill.csv": skill, "mincer_zarnowitz.csv": mz,
           "stress_years.csv": stress_table(d), "split_half.csv": split_half(d), "sensitivity.csv": sens,
           "models_tried.csv": models_tried(cuts),
           "primary.txt": lines, "primary": prim}
    if leakage is not None:
        out["leakage.csv"] = leakage
    return out


def write_outputs(out: dict, outdir: Path, commit: str, stamp: str, fc_hashes: dict | None = None) -> list[Path]:
    """`fc_hashes` = {column name: sha256 of the forecast file the numbers were computed from}, recorded in every output."""
    fc_hashes = fc_hashes or {}
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in out.items():
        if name == "primary":
            continue
        path = outdir / name
        if name.endswith(".txt"):
            hdr = [f"code commit: {commit}", f"generated: {stamp}"] + [f"{k}: {v}" for k, v in fc_hashes.items()]
            path.write_text("\n".join(hdr + [""] + rows) + "\n")
        else:
            cols = list(dict.fromkeys(k for r in rows for k in r)) + ["code_commit", "generated_utc"] + list(fc_hashes)
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, cols, lineterminator="\n", restval="")
                w.writeheader()
                for r in rows:
                    w.writerow({**r, "code_commit": commit, "generated_utc": stamp, **fc_hashes})
        written.append(path)
    return written


def check_fresh(fc: list[dict], ident: dict, name: str = "out/forecasts.csv") -> None:
    """Staleness gate: the forecast file must carry, on every row, the code tree and store hash it is now being evaluated under."""
    if not fc:
        raise SystemExit(f"REFUSING TO RUN: {name} is empty")
    for col in ("code_tree", "store_sha256"):
        got = {r.get(col, "") for r in fc}
        if got != {ident[col]}:
            raise SystemExit(f"REFUSING TO RUN: {name} is stale ({col}: file {sorted(got)} vs current {ident[col]}); run `make forecasts`")
    if ident["code_tree"].endswith("+dirty"):
        raise SystemExit("REFUSING TO RUN: src/ has uncommitted changes; commit, then `make forecasts`")


def read_hashed(path: Path) -> tuple[list[dict], str]:
    """Rows and sha256 of the SAME bytes (the hash is of what was parsed, not of a later read of the file)."""
    raw = path.read_bytes()
    return list(csv.DictReader(io.StringIO(raw.decode(), newline=""))), hashlib.sha256(raw).hexdigest()


def load_forecasts(root: Path) -> tuple[list[dict], list[dict] | None, dict]:
    """out/forecasts.csv (+ forecasts_v2.csv), checked fresh against the current build identity; returns the rows and the content
    hashes recorded in every eval output."""
    from .models import build_identity
    ident = build_identity(root)
    fc, h1 = read_hashed(root / "out" / "forecasts.csv")
    check_fresh(fc, ident)
    hashes = {"forecasts_sha256": h1}
    v2p = root / "out" / "forecasts_v2.csv"
    v2 = None
    if v2p.exists():
        v2, h2 = read_hashed(v2p)
        check_fresh(v2, ident, "out/forecasts_v2.csv")
        hashes["forecasts_v2_sha256"] = h2
    return fc, v2, hashes


def check_unchanged(root: Path, hashes: dict) -> None:
    """The forecast files must still hash to what was read (no regeneration mid-run)."""
    for key, name in (("forecasts_sha256", "forecasts.csv"), ("forecasts_v2_sha256", "forecasts_v2.csv")):
        if key in hashes and hashlib.sha256((root / "out" / name).read_bytes()).hexdigest() != hashes[key]:
            raise SystemExit(f"REFUSING TO WRITE: out/{name} changed while the evaluation ran")


def run_preflight(root: Path | None = None) -> int:
    """`python -m cfstore preflight`: ONLY the availability check (scored forecast row + truth value per season) on real data.
    Computes no score and no coverage; prints the counts."""
    from .baselines import load
    from .fetch import ROOT
    root = root or ROOT
    cuts = read_cuts(root / "scope_cuts.csv")
    store, _ = load()
    fc, v2, _ = load_forecasts(root)
    truths = load_truths(root, store, cuts)
    status = 0
    for label, rows in (("primary", fc), ("v2 re-post forecasts", v2)):
        if rows is None:
            continue
        rows, t = apply_cuts(rows, truths, cuts)
        d = Data(rows, t, cuts=cuts)
        print("\n".join(avail_lines(d, label)))
        try:
            preflight(d, t, label)
            print(f"PREFLIGHT OK ({label})")
        except PreregCountError as e:
            print(e)
            status = 1
    return status


def run(root: Path | None = None) -> int:
    from .baselines import load
    from .fetch import ROOT
    root = root or ROOT
    verify_prereg(root)
    cuts = read_cuts(root / "scope_cuts.csv")  # fails before anything is computed on a refused cut
    leakage = run_leakage_tests(root)  # exits on any failure: no output is written
    store, _ = load()
    fc, v2, hashes = load_forecasts(root)
    truths = load_truths(root, store, cuts)
    out = evaluate(fc, truths, cuts, v2, leakage)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    check_unchanged(root, hashes)
    files = write_outputs(out, root / "out" / "eval", code_commit(root), stamp, hashes)
    print("\n".join(out["primary.txt"]))
    print(f"\nwrote {len(files)} files to out/eval/")
    return 0
