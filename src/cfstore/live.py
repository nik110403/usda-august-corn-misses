"""F11: forecast-only live 2026 M1 yield / PROD production bands (PREREG-AMENDMENTS 2026-10-16 "F11 live 2026 band").
Method unchanged (Run.m1, Run.prod); no truth, no score. Writes only out/live/bands_2026.{csv,md}. Settles against the
January 2027 Crop Production Annual Summary, outside this project."""
from fractions import Fraction

from .baselines import season_of
from .models import COLUMNS, LEVELS, MONTHS, Run, build_identity, month_of, write_csv

LIVE_SEASON = 2026
LIVE_MONTHS = (8, 9, 10)
COLS = ["model", "unit", "origin_date", "horizon", "point", "n", "level", "k_lo", "k_hi", "q_lo_pct", "q_hi_pct", "val_lo",
        "val_hi", "exp_cov", "build_commit", "code_tree", "store_sha256"]
assert set(COLS[:-3]) - {"q_lo_pct", "q_hi_pct"} <= set(COLUMNS)


def live_origins(run: Run) -> list[dict]:
    return [o for o in run.ors if season_of(o) == LIVE_SEASON and month_of(o) in LIVE_MONTHS]


def live_rows(run: Run) -> list[dict]:
    """Level rows of M1 (yield) and PROD (production) for every 2026 Aug-Oct origin, raw (exact rationals)."""
    out = []
    for o in live_origins(run):
        for rows in (run.m1(o), run.prod(o)[0]):
            out += [r for r in rows if r["kind"] == "level"]
    return out


def _num(x: str) -> str:
    return format(float(Fraction(x)), "f").rstrip("0").rstrip(".")


def _f(x: str, nd: int) -> str:
    return f"{float(Fraction(x)):.{nd}f}"


def display(rows: list[dict], ident: dict) -> list[dict]:
    out = []
    for r in rows:
        nd = 1 if r["model"] == "M1" else 0
        out.append(dict(model=r["model"], unit=r["unit"], origin_date=r["origin_date"], horizon=r["horizon"],
                        point=_num(r["point"]), n=r["n"], level=_f(r["level"], 2), k_lo=r["k_lo"], k_hi=r["k_hi"],
                        q_lo_pct=_f(r["q_lo"], 2), q_hi_pct=_f(r["q_hi"], 2), val_lo=_f(r["val_lo"], nd),
                        val_hi=_f(r["val_hi"], nd), exp_cov=_f(r["exp_cov"], 4), **ident))
    return out


def markdown(disp: list[dict]) -> str:
    def table(model: str, unit: str) -> list[str]:
        by = {}
        for r in disp:
            if r["model"] == model:
                by.setdefault((r["origin_date"], r["horizon"], r["point"]), {})[r["level"]] = r
        L = [f"| horizon (origin) | USDA point ({unit}) | 80% band | 50% band |", "|---|---|---|---|"]
        for (d, h, p), lv in sorted(by.items()):
            b = lambda k: f"{lv[k]['val_lo']} to {lv[k]['val_hi']}"
            L.append(f"| {h} ({d}) | {p} | {b('0.80')} | {b('0.50')} |")
        return L
    lines = ["# Live 2026 band (forecast only, unscored)", "", "## Yield, M1 (bu/ac)", "", *table("M1", "bu/ac"), "",
             "## Production, PROD (Mbu)", "", *table("PROD", "Mbu"), "",
             "This is M1 (USDA's own past same-month-to-January percent misses, split-conformal ranks, PREREG section 2) "
             "applied unchanged to the 2026 crop; PROD is the same construction on production. It is not scored in this project "
             "and settles against the January 2027 Crop Production Annual Summary.",
             "An 80% band is expected to miss about one season in five.",
             "exp_cov in bands_2026.csv is the estimator's expected coverage, (k_hi - k_lo)/(n + 1) for the n past seasons used.", ""]
    return "\n".join(lines)


def run(root=None) -> int:
    from .baselines import load
    from .fetch import ROOT
    root = root or ROOT
    store, releases = load()
    r = Run(store, releases)
    rows = live_rows(r)
    ident = build_identity(root)
    disp = display(rows, ident)
    d = root / "out" / "live"
    write_csv(d / "bands_2026.csv", disp, COLS)
    (d / "bands_2026.md").write_text(markdown(disp))
    print(f"out/live/bands_2026.csv: {len(disp)} rows ({len(live_origins(r))} origins); out/live/bands_2026.md")
    print(markdown(disp))
    return 0
