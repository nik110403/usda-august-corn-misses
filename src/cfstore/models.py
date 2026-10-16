"""Forecast layer (SPEC F6, PREREG 2/3/6): M1, M2, M3, production band, B0/B1/B2 and the stocks-to-use step as rows of
out/forecasts.csv. NO score is computed here and the output has no truth column for the target season.

Every feature set goes through `model_features`: rows are restricted to the model's series allowlist (a model that asks for
any other series raises), then through `asof.build_features` (select + `assert_no_leak`), and every training set runs
`trend.assert_finals_before`. Training errors (past seasons' errors used inside a model) are model inputs, not scores.
Exact rationals throughout; the only floats are inside the M3 linear program (scipy/HiGHS), converted back with
Fraction(repr(x)) for the output.
"""
import csv
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor

from .asof import build_features, origins, ts
from .baselines import b0_errors, b0_point, pct_error, season_of
from .conformal import band_ranks
from .trend import FIRST_SEASON, assert_finals_before, fit_trend

YIELD = "wasde.us_corn.yield"
PROD = "wasde.us_corn.production"
RUSSIA = "wasde.russia_wheat.production"  # F10 optional slot (PREREG-AMENDMENTS 2026-10-16); read only by russia.py
REL = "crop_production.us_corn.rel_ci90_pct"
GE = "crop_progress.us_corn.good_excellent"
BS_SERIES = tuple(f"wasde.us_corn.{n}" for n in
                  ("beginning_stocks", "production", "imports", "supply_total", "total_use", "ending_stocks"))

# Series allowlist per model (SPEC F6 / brief). crop_production.* point values (yield, production, area) are cross-check
# only and appear in no allowlist. B1 additionally reads the yield only to label its derived yield band.
ALLOW: dict[str, frozenset[str]] = {
    "M1": frozenset({YIELD}), "M2": frozenset({YIELD}), "B2": frozenset({YIELD}), "B0": frozenset({YIELD}),
    "M3": frozenset({YIELD, GE}),
    "PROD": frozenset({PROD}),
    "B1": frozenset({PROD, YIELD, REL}),
    "STU": frozenset(BS_SERIES),
    "M1R": frozenset({RUSSIA}),  # F10: M1 on the Russia wheat production print
}

TAUS5 = (Fraction(1, 10), Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(9, 10))
TAUS7 = (Fraction(1, 20),) + TAUS5 + (Fraction(19, 20),)
LEVELS = (Fraction(1, 2), Fraction(4, 5), Fraction(9, 10))
MONTHS = {5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov"}
SEASONS = range(2004, 2026)
B0_MIN_N = 9  # PREREG 3: B0's band is scored once it has >= 9 own out-of-sample errors (seasons 2009-2025), at every level
M3_MIN_TRAIN = 3  # M3 needs at least this many training seasons with a condition line; below it M3 = M1 (flagged)

COLUMNS = ["model", "horizon", "season", "origin_date", "origin_ts", "kind", "tau", "level", "unit", "point", "n",
           "k", "k_lo", "k_hi", "q", "q_lo", "q_hi", "val", "val_lo", "val_hi", "exp_cov", "scored", "in_tests",
           "origin_flag", "note", "b1_r_pct",
           "m2_a", "m2_b", "m2_r", "m2_distinct_r",
           "m3_x", "m3_x_detrended", "m3_x_date", "m3_x_flag", "m3_trend_a", "m3_trend_b", "m3_n", "m3_alpha",
           "m3_beta", "m3_lp_status", "m3_nt_integer", "m3_rearranged", "m3_fallback"]


class SeriesNotAllowed(AssertionError):
    pass


def ref(s: int) -> str:
    return f"{s}/{(s + 1) % 100:02d}"


def month_of(o: dict) -> int:
    return int(o["release_date"][5:7])


def assert_allowed(model: str, rows: list[dict]) -> None:
    bad = sorted({r["series"] for r in rows} - ALLOW[model])
    if bad:
        raise SeriesNotAllowed(f"{model} may not use series {bad}")


def model_features(model: str, store: list[dict], origin: dict) -> list[dict]:
    """The only way a model sees data: allowlisted series only, then select + leakage assertion."""
    allowed = ALLOW[model]
    feats = build_features([r for r in store if r["series"] in allowed], origin)
    assert_allowed(model, feats)
    return feats


class View:
    """Indexed read access to a guarded feature set; every read re-checks the allowlist."""

    def __init__(self, model: str, feats: list[dict]):
        self.model, self.rows = model, feats
        self._ix: dict[tuple, list[dict]] = defaultdict(list)
        self._by_series: dict[str, list[dict]] = defaultdict(list)
        for r in feats:
            self._ix[(r["series"], r["reference_period"], r["release_date"])].append(r)
            self._by_series[r["series"]].append(r)

    def _ok(self, series: str) -> None:
        if series not in ALLOW[self.model]:
            raise SeriesNotAllowed(f"{self.model} may not use series {series}")

    def row(self, series: str, season: int, date: str) -> dict:
        self._ok(series)
        c = self._ix.get((series, ref(season), date), [])
        if len(c) != 1:
            raise ValueError(f"{self.model}: expected one {series} row for {ref(season)} released {date}, got {len(c)}")
        return c[0]

    def val(self, series: str, season: int, date: str) -> Fraction:
        return Fraction(self.row(series, season, date)["value"])

    def truth(self, series: str, season: int) -> dict | None:
        """First-final of crop `season`: the earliest release dated in calendar year season+1 that carries it (the January
        WASDE; 8 Feb 2019 after the shutdown), as skeleton.truth_rows. Only rows in the guarded view can be returned."""
        self._ok(series)
        c = [r for r in self._by_series.get(series, []) if r["reference_period"] == ref(season)
             and r["release_date"].startswith(str(season + 1)) and r["revision_flag"] != "v2_repost"]
        return min(c, key=lambda r: r["release_date"]) if c else None


def training_set(view: View, o: dict, series: str, by_sm: dict) -> list[tuple[int, Fraction, Fraction, dict]]:
    """[(season s, percent error e_s, USDA_h(s), truth row)] for crops FIRST_SEASON <= s < t with a same-month origin and
    a first-final in the view (= released before `o`); assert_finals_before on every final used (leakage test 3)."""
    t, m = season_of(o), month_of(o)
    out, finals = [], []
    for s in range(FIRST_SEASON, t):
        os_ = by_sm.get((s, m))
        tr = view.truth(series, s)
        if os_ is None or tr is None:
            continue
        p = view.val(series, s, os_["release_date"])
        out.append((s, pct_error(Fraction(tr["value"]), p), p, tr))
        finals.append((s, Fraction(tr["value"]), tr))
    assert_finals_before(finals, o)
    return out


# ---- exact OLS, ranks ----------------------------------------------------------------------------------------------

def ols(xs: list[Fraction], ys: list[Fraction]) -> tuple[Fraction, Fraction]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def tau_rank(n: int, tau: Fraction) -> int:
    """PREREG 2: floor((n+1)tau) for tau < 0.5, ceil for tau >= 0.5, exact."""
    x = (n + 1) * tau
    return floor(x) if tau < Fraction(1, 2) else ceil(x)


# ---- row construction ----------------------------------------------------------------------------------------------

def s_(x) -> str:
    return "" if x is None else str(x)


def base_row(model: str, o: dict, **kw) -> dict:
    r = {c: "" for c in COLUMNS}
    r.update(model=model, horizon=MONTHS[month_of(o)], season=str(season_of(o)), origin_date=o["release_date"],
             origin_ts=o["release_ts"], origin_flag=o["revision_flag"])
    r.update({k: s_(v) for k, v in kw.items()})
    return r


def conformal_rows(model: str, o: dict, point: Fraction, unit: str, resid: list[Fraction], shift: Fraction = Fraction(0),
                   in_tests: int = 1, note: str = "", min_n: int = 0, **diag) -> list[dict]:
    """PREREG 2 quantile/band rows around `point`: quantile = shift + order statistic of `resid` (percent space).
    `min_n`: a row is scored (scored = 1) only when it has an order statistic AND len(resid) >= min_n (B0: 9, PREREG 3)."""
    n, srt, out = len(resid), sorted(resid), []
    enough = n >= min_n
    common = dict(unit=unit, point=point, n=n, in_tests=in_tests, note=note, **diag)
    for tau in TAUS7:
        k = tau_rank(n, tau)
        ok = 1 <= k <= n
        q = shift + srt[k - 1] if ok else None
        out.append(base_row(model, o, kind="tau", tau=tau, k=k, q=q, val=None if q is None else point * (1 + q / 100),
                            scored=int(ok and enough), **common))
    for lev in LEVELS:
        k_lo, k_hi = band_ranks(n, lev)
        ok = 1 <= k_lo and k_hi <= n
        lo, hi = (shift + srt[k_lo - 1], shift + srt[k_hi - 1]) if ok else (None, None)
        out.append(base_row(model, o, kind="level", level=lev, k_lo=k_lo, k_hi=k_hi, q_lo=lo, q_hi=hi,
                            val_lo=None if ok is False else point * (1 + lo / 100),
                            val_hi=None if ok is False else point * (1 + hi / 100),
                            exp_cov=Fraction(k_hi - k_lo, n + 1), scored=int(ok and enough), **common))
    return out


# ---- the run -------------------------------------------------------------------------------------------------------

@dataclass
class M3Fit:
    quantiles: list[Fraction] | None  # rearranged, in TAUS5 order
    per_tau: list[dict]
    shared: dict
    fallback: str


def quantreg_lp(xs: list[float], es: list[float], tau: float) -> tuple[float, float, int]:
    """Exact quantile-regression LP: min tau*sum(u) + (1-tau)*sum(v) s.t. alpha + beta*x_i + u_i - v_i = e_i, u,v >= 0.
    HiGHS (scipy.optimize.linprog method='highs'); returns (alpha, beta, scipy status; 0 = optimal)."""
    import numpy as np
    from scipy.optimize import linprog
    n = len(xs)
    c = np.concatenate([[0.0, 0.0], np.full(n, tau), np.full(n, 1 - tau)])
    a_eq = np.hstack([np.ones((n, 1)), np.array(xs).reshape(n, 1), np.eye(n), -np.eye(n)])
    res = linprog(c, A_eq=a_eq, b_eq=np.array(es), bounds=[(None, None), (None, None)] + [(0, None)] * (2 * n),
                  method="highs")
    if res.status != 0:
        return float("nan"), float("nan"), int(res.status)
    return float(res.x[0]), float(res.x[1]), 0


def _fl(x: float) -> Fraction:
    return Fraction(repr(float(x)))


class Run:
    def __init__(self, store: list[dict], releases: list[dict]):
        self.store, self.releases = store, releases
        self.ors = origins(releases)
        self.by_sm = {(season_of(o), month_of(o)): o for o in self.ors}
        # B0 reads first-finals only (PREREG 3): the F5 functions get just the January-March yield rows, where every
        # first-final lives (equality with the full store asserted in tests/test_models.py); a speed device, not a filter.
        self.b0_store = [r for r in store if r["series"] == YIELD and r["release_date"][5:7] in ("01", "02", "03")]
        self._feats: dict[tuple, View] = {}
        self._x: dict[tuple, dict | None] = {}

    def view(self, model: str, o: dict) -> View:
        k = (model, o["release_date"])
        if k not in self._feats:
            self._feats[k] = View(model, model_features(model, self.store, o))
        return self._feats[k]

    def prev_origin(self, o: dict) -> dict | None:
        prev = [p for p in self.ors if season_of(p) == season_of(o) and p["release_date"] < o["release_date"]]
        return prev[-1] if prev else None

    def point(self, model: str, series: str, o: dict) -> Fraction:
        return self.view(model, o).val(series, season_of(o), o["release_date"])

    # -- M1 ---------------------------------------------------------------------------------------------------------
    def m1(self, o: dict) -> list[dict]:
        v = self.view("M1", o)
        tr = training_set(v, o, YIELD, self.by_sm)
        return conformal_rows("M1", o, self.point("M1", YIELD, o), "bu/ac", [e for _, e, _, _ in tr])

    # -- M2 ---------------------------------------------------------------------------------------------------------
    def _r(self, v: View, s: int, o_s: dict) -> Fraction | None:
        po = self.prev_origin(o_s)
        if po is None:
            return None
        return pct_error(v.val(YIELD, s, o_s["release_date"]), v.val(YIELD, s, po["release_date"]))

    def m2(self, o: dict) -> list[dict]:
        v = self.view("M2", o)
        point = self.point("M2", YIELD, o)
        tr = training_set(v, o, YIELD, self.by_sm)
        errs = [e for _, e, _, _ in tr]
        if self.prev_origin(o) is None:
            return conformal_rows("M2", o, point, "bu/ac", errs, in_tests=0, note="May: no revision exists, M2 = M1")
        r_t = self._r(v, season_of(o), o)
        rs = [self._r(v, s, self.by_sm[(s, month_of(o))]) for s, _, _, _ in tr]
        distinct = len(set(rs))
        if distinct < 3:
            return conformal_rows("M2", o, point, "bu/ac", errs, note="fewer than 3 distinct r: b=0, M2 = M1",
                                  m2_a=0, m2_b=0, m2_r=r_t, m2_distinct_r=distinct)
        a, b = ols(rs, errs)
        resid = [e - a - b * r for e, r in zip(errs, rs)]
        return conformal_rows("M2", o, point, "bu/ac", resid, shift=a + b * r_t,
                              m2_a=a, m2_b=b, m2_r=r_t, m2_distinct_r=distinct)

    # -- M3 ---------------------------------------------------------------------------------------------------------
    def x_line(self, s: int, m: int) -> dict | None:
        """x_{s,h}: the last national corn condition line of crop s in the guarded information set of s's own h-origin
        (asof.info_set + assert_no_leak via model_features), so a Monday / holiday-Tuesday origin uses the previous week."""
        k = (s, m)
        if k not in self._x:
            o_s = self.by_sm.get(k)
            if o_s is None:
                self._x[k] = None
            else:
                c = [r for r in self.view("M3", o_s).rows if r["series"] == GE and int(r["reference_period"][:4]) == s]
                self._x[k] = max(c, key=lambda r: (ts(r), r["reference_period"])) if c else None
        return self._x[k]

    def m3_fit(self, o: dict, train: list[tuple[int, Fraction, Fraction, dict]]) -> M3Fit:
        t, m = season_of(o), month_of(o)
        xr_t = self.x_line(t, m)
        pairs = []
        for s, e, _, _ in train:
            xr = self.x_line(s, m)
            if xr is not None:
                assert ts(xr) < ts(o), (s, xr)
                pairs.append((s, Fraction(xr["value"]), e))
        shared = dict(m3_n=len(pairs))
        if xr_t is None:
            return M3Fit(None, [], shared, "target_x_missing")
        if len(pairs) < M3_MIN_TRAIN or len({s for s, _, _ in pairs}) < 2:
            return M3Fit(None, [], shared, "too_few_training_x")
        x_t = Fraction(xr_t["value"])
        tr = fit_trend([(s, x, None) for s, x, _ in pairs])
        xd = [float(x - tr.predict(s)) for s, x, _ in pairs]
        es = [float(e) for _, _, e in pairs]
        xt = x_t - tr.predict(t)
        shared.update(m3_x=x_t, m3_x_detrended=xt, m3_x_date=xr_t["release_date"], m3_x_flag=xr_t["revision_flag"],
                      m3_trend_a=tr.intercept, m3_trend_b=tr.slope)
        raw, per = [], []
        for tau in TAUS5:
            a, b, st = quantreg_lp(xd, es, float(tau))
            if st != 0:
                per.append(dict(m3_lp_status=st))
                return M3Fit(None, per, shared, f"lp_status_{st}")
            raw.append(_fl(a) + _fl(b) * xt)
            per.append(dict(m3_alpha=_fl(a), m3_beta=_fl(b), m3_lp_status=0,
                            m3_nt_integer=int((len(pairs) * tau).denominator == 1)))
        srt = sorted(raw)
        shared["m3_rearranged"] = int(srt != raw)
        return M3Fit(srt, per, shared, "")

    def m3(self, o: dict) -> list[dict]:
        point = self.point("M3", YIELD, o)
        tr = training_set(self.view("M3", o), o, YIELD, self.by_sm)
        if month_of(o) == 5:
            return conformal_rows("M3", o, point, "bu/ac", [e for _, e, _, _ in tr], in_tests=0,
                                  note="May: no condition line exists, M3 = M1", m3_fallback="may")
        fit = self.m3_fit(o, tr)
        if fit.quantiles is None:
            rows = conformal_rows("M3", o, point, "bu/ac", [e for _, e, _, _ in tr], note="M3 = M1 (d_s = 0)",
                                  m3_fallback=fit.fallback, **fit.shared)
            for r, p in zip([r for r in rows if r["kind"] == "tau" and Fraction(r["tau"]) in TAUS5], fit.per_tau):
                r.update({k: s_(v) for k, v in p.items()})
            return [r for r in rows if r["kind"] == "tau" and Fraction(r["tau"]) in TAUS5]
        out = []
        for tau, q, p in zip(TAUS5, fit.quantiles, fit.per_tau):
            out.append(base_row("M3", o, kind="tau", tau=tau, unit="bu/ac", point=point, n=fit.shared["m3_n"], q=q,
                                val=point * (1 + q / 100), scored=1, in_tests=1, **fit.shared, **p))
        return out

    # -- production band and stocks-to-use --------------------------------------------------------------------------
    def prod(self, o: dict) -> tuple[list[dict], Fraction]:
        v = self.view("PROD", o)
        tr = training_set(v, o, PROD, self.by_sm)
        point = v.val(PROD, season_of(o), o["release_date"])
        return conformal_rows("PROD", o, point, "Mbu", [e for _, e, _, _ in tr]), point

    def stu(self, o: dict, prod_rows: list[dict]) -> dict:
        v = self.view("STU", o)
        s, d = season_of(o), o["release_date"]
        g = {n: v.val(f"wasde.us_corn.{n}", s, d) for n in
             ("beginning_stocks", "production", "imports", "supply_total", "total_use", "ending_stocks")}
        po = self.prev_origin(o)
        row = dict(season=s, horizon=MONTHS[month_of(o)], origin_date=d, beginning_stocks=g["beginning_stocks"],
                   production=g["production"], imports=g["imports"], total_use=g["total_use"],
                   ending_stocks=g["ending_stocks"], stocks_to_use_pct=100 * g["ending_stocks"] / g["total_use"],
                   label="conditional on USDA's use projections; not scored; never called calibrated")
        if po is not None:
            row.update(prev_origin_date=po["release_date"],
                       carry_in_revision=g["beginning_stocks"] - v.val(BS_SERIES[0], s, po["release_date"]),
                       production_revision=g["production"] - v.val(PROD, s, po["release_date"]))
        taus = {t: r for r in prod_rows if r["kind"] == "tau" and r["scored"] == "1" and (t := Fraction(r["tau"])) in
                (Fraction(1, 10), Fraction(1, 2), Fraction(9, 10))}
        for t, lab in ((Fraction(1, 10), "10"), (Fraction(1, 2), "50"), (Fraction(9, 10), "90")):
            if t in taus:
                p_q = Fraction(taus[t]["val"])
                e_q = g["ending_stocks"] + (p_q - g["production"])  # beginning stocks, imports, total use held fixed
                row.update({f"production_q{lab}": p_q, f"ending_stocks_q{lab}": e_q,
                            f"stocks_to_use_pct_q{lab}": 100 * e_q / g["total_use"]})
        return row

    # -- baselines --------------------------------------------------------------------------------------------------
    def b0(self, o: dict) -> list[dict]:
        pt = b0_point(self.b0_store, o)
        errs = b0_errors(self.b0_store, self.releases, o)
        return conformal_rows("B0", o, pt, "bu/ac", errs, min_n=B0_MIN_N,
                              note="" if len(errs) >= B0_MIN_N else f"B0 own OOS errors n < {B0_MIN_N}: not scored (PREREG 3)")

    def b1(self, o: dict) -> list[dict]:
        if month_of(o) < 8:
            return []
        s, d = season_of(o), o["release_date"]
        v = self.view("B1", o)
        p, y = v.val(PROD, s, d), v.val(YIELD, s, d)
        try:
            r = v.val(REL, s, d)
        except ValueError:
            return [base_row(m, o, kind="level", level=Fraction(9, 10), unit=u, point=pt, scored=0, in_tests=0,
                             note="no same-day Crop Production in the information set (Oct 2008)")
                    for m, u, pt in (("B1", "Mbu", p), ("B1y", "bu/ac", y))]
        assert ts(v.row(REL, s, d)) == ts(o)  # the joint release carries the origin's own timestamp
        lo, hi = p / (1 + r / 100), p / (1 - r / 100)
        out = []
        for m, u, pt, scale in (("B1", "Mbu", p, p), ("B1y", "bu/ac", y, y)):
            f_lo, f_hi = lo / p, hi / p  # derived yield band = same ratios on USDA's yield = production band / implied area (PREREG-AMENDMENTS 2026-10-16 F6a fix 1)
            out.append(base_row(m, o, kind="level", level=Fraction(9, 10), unit=u, point=pt, q_lo=100 * (f_lo - 1),
                                q_hi=100 * (f_hi - 1), val_lo=pt * f_lo, val_hi=pt * f_hi, scored=1, in_tests=0,
                                note="USDA band, descriptive; B1y is the derived yield band" if m == "B1y" else "USDA band, descriptive",
                                b1_r_pct=r))
        return out

    def b2(self, o: dict) -> list[dict]:
        po = self.prev_origin(o)
        if po is None:
            return []
        v = self.view("B2", o)
        return [base_row("B2", o, kind="point", unit="bu/ac", point=v.val(YIELD, season_of(o), po["release_date"]),
                         scored=1, in_tests=0, note="carry-forward of the previous origin's USDA yield")]

    def usda(self, o: dict) -> list[dict]:
        return [base_row("USDA", o, kind="point", unit="bu/ac", point=self.point("M1", YIELD, o), scored=1, in_tests=0)]

    def origin_rows(self, o: dict) -> tuple[list[dict], dict]:
        rows = []
        rows += self.usda(o) + self.m1(o) + self.m2(o) + self.m3(o)
        prod_rows, _ = self.prod(o)
        rows += prod_rows + self.b0(o) + self.b1(o) + self.b2(o)
        return rows, self.stu(o, prod_rows)

    def all_rows(self, seasons=SEASONS) -> tuple[list[dict], list[dict]]:
        rows, stu = [], []
        for o in self.ors:
            if season_of(o) in seasons and 5 <= month_of(o) <= 11:
                r, s = self.origin_rows(o)
                rows += r
                stu.append(s)
        return rows, stu


STU_COLUMNS = ["season", "horizon", "origin_date", "prev_origin_date", "beginning_stocks", "carry_in_revision", "production",
               "production_revision", "production_q10", "production_q50", "production_q90", "imports", "total_use",
               "ending_stocks", "ending_stocks_q10", "ending_stocks_q50", "ending_stocks_q90", "stocks_to_use_pct",
               "stocks_to_use_pct_q10", "stocks_to_use_pct_q50", "stocks_to_use_pct_q90", "label"]


def v2_variant(store: list[dict], releases: list[dict]) -> tuple[list[dict], list[dict]]:
    """Sensitivity (PREREG 1 'Corrections'): every release with a logged v2 re-post is read from the re-post instead of the
    first archived file. Used only for the v2 sensitivity run; the primary run never sees these rows."""
    v2 = {(r["series"], r["reference_period"], r["release_date"]): r for r in store if r["revision_flag"] == "v2_repost"}
    st = []
    for r in store:
        k = (r["series"], r["reference_period"], r["release_date"])
        if r["revision_flag"] == "v2_repost":
            st.append(dict(r, revision_flag="v1"))
        elif k not in v2:
            st.append(r)
    v2_file = {r["release_date"]: r["source_file"] for r in store if r["revision_flag"] == "v2_repost"
               and r["series"].startswith("wasde.")}
    rel = [dict(o, file=v2_file[o["release_date"]]) if o["release_date"] in v2_file else o for o in releases]
    return st, rel


META_COLS = ["build_commit", "code_tree", "store_sha256"]


def build_identity(root) -> dict:
    """Provenance stamped on every out/forecasts.csv row and re-derived by `evaluate` (staleness gate). `code_tree` hashes the git
    object ids of src/, pyproject.toml and uv.lock (+ '+dirty' if any differs from HEAD): it identifies the code that built the file without changing when
    the outputs themselves are committed; `build_commit` is HEAD at build time (informational). `store_sha256` hashes the
    store and release tables the forecasts were built from."""
    import hashlib
    import subprocess
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    paths = ("src", "pyproject.toml", "uv.lock")
    tree = hashlib.sha256("\n".join(f"{p} {git('rev-parse', 'HEAD:' + p)}" for p in paths).encode()).hexdigest()[:40]
    if git("status", "--porcelain", "--", *paths):
        tree += "+dirty"
    h = hashlib.sha256()
    for name in ("store.csv", "wasde_releases.csv"):
        h.update((root / "data" / name).read_bytes())
    return {"build_commit": git("rev-parse", "HEAD"), "code_tree": tree, "store_sha256": h.hexdigest()}


def write_csv(path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n", extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({c: s_(r.get(c, "")) for c in cols})


def run() -> None:
    from .baselines import load
    from .fetch import ROOT
    store, releases = load()
    out = ROOT / "out"
    rows, stu = Run(store, releases).all_rows()
    ident = build_identity(ROOT)
    write_csv(out / "forecasts.csv", [dict(r, **ident) for r in rows], COLUMNS + META_COLS)
    dec = lambda v: f"{float(v):.4f}" if isinstance(v, Fraction) and v.denominator != 1 else v  # display-only file
    write_csv(out / "stocks_to_use.csv", [{k: dec(v) for k, v in r.items()} for r in stu], STU_COLUMNS)
    print(f"out/forecasts.csv: {len(rows)} rows; out/stocks_to_use.csv: {len(stu)} rows")
    st2, rel2 = v2_variant(store, releases)
    if any(r["revision_flag"] == "v2_repost" for r in store):
        rows2, _ = Run(st2, rel2).all_rows()
        write_csv(out / "forecasts_v2.csv", [dict(r, **ident) for r in rows2], COLUMNS + META_COLS)
        print(f"out/forecasts_v2.csv (v2 re-post sensitivity): {len(rows2)} rows")
