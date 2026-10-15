"""Evaluation harness (SPEC F6, PREREG 4, 5, 10): `python -m cfstore evaluate` (`make evaluate`).

WRITTEN AND TESTED ON SYNTHETIC DATA ONLY; NOT RUN on real seasons at the time of writing (checkpoint 2 comes first, PREREG 8).
Reads out/forecasts.csv and the truths and computes, in ONE run, every PREREG 4, 5 and 10 number. Writes out/eval/*.csv and
out/eval/primary.txt, each carrying the code commit and a UTC timestamp. Refuses to run if PREREG.sha256 does not verify.

Percent-error space with exact rationals for coverage (PREREG 2); pinball loss rho_tau(u) = u (tau - 1{u<0}) on percent errors
u = 100 (final - quantile_bu/ac) / USDA_h, per-season loss = mean over the 5 tau, whose mean over seasons is the *truncated
quantile score* (not CRPS).
"""
import csv
import hashlib
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

    def __init__(self, rows: list[dict], truths: Truths, exclude=frozenset()):
        self.t, self.exclude = truths, set(exclude)
        self.lv, self.tau, self.pt = {}, {}, {}
        for r in rows:
            key = (r["model"], r["horizon"], int(r["season"]))
            if (key[2], key[1]) in self.exclude:
                continue
            if r["kind"] == "level":
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

    def season_losses(self, model: str, h: str, which: str = "primary") -> dict[int, Fraction]:
        """Per-season truncated quantile score (mean pinball over the 5 tau) on percent errors u = 100 (final - q_bu/ac) / base,
        base = USDA_h (the percent base of M1) for yield models, the production point for PROD."""
        out = {}
        for (m, hh, s, tau0), r0 in self.tau.items():
            if m != model or hh != h or tau0 != TAUS5[0]:
                continue
            rows = [self.tau.get((m, h, s, t)) for t in TAUS5]
            if any(r is None or r["scored"] != "1" for r in rows):
                continue
            final = self.t.get(r0["unit"], s, which)
            if final is None:
                continue
            base = Fraction(r0["point"]) if model == "PROD" else (Fraction(self.pt[("USDA", h, s)]["point"]) if ("USDA", h, s) in self.pt else None)
            if base is None:
                continue
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
    a, b = d.season_losses("M1", h), d.season_losses(m_other, h)
    seas = sorted(set(a) & set(b))
    if len(seas) < 2:
        return None
    diffs = [float(a[s] - b[s]) for s in seas]
    r = stats.hln_dm(diffs, alt)
    r["seasons"] = seas
    return r


def primary_tables(d: Data, cuts: set[str]) -> tuple[list[str], dict]:
    """PREREG 4.1-4.4. Returns (lines of primary.txt, machine-readable dict)."""
    x, n, seas = d.count("M1", "Aug", EIGHTY)
    word, (lo, hi) = stats.wording(x, n, EIGHTY)
    lines = ["PREREG section 4 primary analysis (computed in one run with every section 5 and 10 number)",
             "4.1/4.2 Primary calibration object: M1 80% band at the August origin, primary truth (January first-final).",
             f"  x = {x} of n = {n} seasons covered ({x / n:.1%}); two-sided 90% Clopper-Pearson interval [{lo:.1%}, {hi:.1%}].",
             f"  Wording: \"{word}\""]
    prim = {"x": x, "n": n, "cp_lo": lo, "cp_hi": hi, "wording": word}
    if PRIMARY_DM_ID in cuts:
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


def family_table(d: Data, cuts: set[str]) -> list[dict]:
    """The fixed 33-test family (PREREG 5) in table order; cut or uncomputable tests enter Holm with p = 1."""
    raw = []
    for tid, kind, model, h, lev in FAMILY:
        rec = {"test_id": tid, "horizon": h, "model": model, "label": "exploratory", "n": "", "statistic": "", "p_raw": 1.0,
               "status": "computed"}
        if tid in cuts:
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


def read_cuts(path: Path) -> set[str]:
    """scope_cuts.csv (test_id,reason,logged_utc,code_commit): the checkpoint fills it before the first run; default empty."""
    if not path.exists():
        return set()
    ids = {r["test_id"] for r in read_csv(path) if r.get("test_id")}
    known = {t[0] for t in FAMILY} | {PRIMARY_DM_ID}
    if ids - known:
        raise SystemExit(f"unknown test ids in {path}: {sorted(ids - known)}")
    return ids


def load_truths(root: Path, store: list[dict]) -> Truths:
    from .skeleton import truth_rows
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
    qs = root / "data" / "quickstats_truth.csv"
    sy = sp = None
    if qs.exists():
        rows = read_csv(qs)
        sy = {int(r["crop_year"]): Fraction(r["yield_bu_ac"]) for r in rows}
        sp = {int(r["crop_year"]): Fraction(r["production_bu"]) / 10**6 for r in rows}  # BU -> Mbu
    else:
        print("sensitivity truth unavailable: data/quickstats_truth.csv missing; sensitivity-truth rows omitted", file=sys.stderr)
    return Truths(y, p, sy, sp)


def run_leakage_tests(root: Path) -> list[dict]:
    """The four leakage tests (STRATEGY 7) plus the model-layer ones: run pytest and group by test number."""
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_leakage.py", "tests/test_models.py", "-q", "-rA",
                        "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
    groups: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0})
    for line in p.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) (\S+?)::(\w+)", line)
        if m:
            g = re.match(r"test([1-4])_", m.group(3))
            key = f"leakage test {g.group(1)}" if g else ("model-layer leakage" if "test_models" in m.group(2) else "leakage (other)")
            groups[key]["passed" if m.group(1) == "PASSED" else "failed"] += 1
    overlap = root / "out" / "crop_progress_overlap_count.txt"
    first = overlap.read_text().splitlines()[0] if overlap.exists() else "overlap count file missing"
    return [{"group": k, **v, "overlap_origins": first} for k, v in sorted(groups.items())]


def evaluate(fc_rows, truths, cuts=frozenset(), fc_v2_rows=None, leakage=None) -> dict:
    """Everything in PREREG 4, 5 and 10 from forecast rows + truths. Returns {file name: rows} and {'primary.txt': lines}."""
    d = Data(fc_rows, truths)
    lines, prim = primary_tables(d, set(cuts))
    skill, mz = skill_and_mz(d)
    ex = corrected_exclusions(fc_rows)
    d_ex = Data(fc_rows, truths, ex)
    sens = sensitivity_summary(d_ex, "corrected-release drop", {"dropped_origins": len(ex)})
    if fc_v2_rows:
        sens += sensitivity_summary(Data(fc_v2_rows, truths), "v2 re-post data")
    if truths.sens_yield is None:
        lines.append("Sensitivity truth (Quick Stats latest) unavailable: descriptive sensitivity-truth rows omitted.")
    lines += ["", f"Corrected-release sensitivity: {len(ex)} origins dropped (own WASDE or M3 regressor from a corrected-only release); "
              "training errors kept.", "See out/eval/*.csv for sections 5 and 10 (family.csv: 33 tests with Holm-adjusted p)."]
    lines.append(GOOD_IRWIN_NOTE)
    out = {"coverage.csv": coverage_table(d), "family.csv": family_table(d, set(cuts)), "scores.csv": scores_table(d),
           "pit.csv": pit_table(d), "sharpness.csv": sharpness_table(d), "skill.csv": skill, "mincer_zarnowitz.csv": mz,
           "stress_years.csv": stress_table(d), "split_half.csv": split_half(d), "sensitivity.csv": sens,
           "models_tried.csv": [dict(zip(("item", "kind", "status", "what", "note"), t)) for t in MODELS_TRIED],
           "primary.txt": lines, "primary": prim}
    if leakage is not None:
        out["leakage.csv"] = leakage
    return out


def write_outputs(out: dict, outdir: Path, commit: str, stamp: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in out.items():
        if name == "primary":
            continue
        path = outdir / name
        if name.endswith(".txt"):
            path.write_text("\n".join([f"code commit: {commit}", f"generated: {stamp}", ""] + rows) + "\n")
        else:
            cols = list(dict.fromkeys(k for r in rows for k in r)) + ["code_commit", "generated_utc"]
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, cols, lineterminator="\n", restval="")
                w.writeheader()
                for r in rows:
                    w.writerow({**r, "code_commit": commit, "generated_utc": stamp})
        written.append(path)
    return written


def run(root: Path | None = None) -> int:
    from .baselines import load
    from .fetch import ROOT
    root = root or ROOT
    verify_prereg(root)
    store, _ = load()
    truths = load_truths(root, store)
    fc = read_csv(root / "out" / "forecasts.csv")
    v2p = root / "out" / "forecasts_v2.csv"
    out = evaluate(fc, truths, read_cuts(root / "scope_cuts.csv"), read_csv(v2p) if v2p.exists() else None, run_leakage_tests(root))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = write_outputs(out, root / "out" / "eval", code_commit(root), stamp)
    print("\n".join(out["primary.txt"]))
    print(f"\nwrote {len(files)} files to out/eval/")
    return 0
