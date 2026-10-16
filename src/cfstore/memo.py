"""F7a memo assets: tables, one figure and the headline, written to out/memo/ from the finished evaluation.

Reads out/eval/*.csv, out/forecasts.csv, out/forecasts_v2.csv, out/stocks_to_use.csv. The realised truth (August answer, the
figure's dots) comes from the store via evaluate.load_truths; every coverage number printed is read from out/eval/coverage.csv,
never recomputed (the per-season hit/miss used by the figure is cross-checked against that file's x). Never writes to out/eval.
"""
import csv
import hashlib
import re
from fractions import Fraction
from pathlib import Path

from .fetch import ROOT

HORIZONS = ["May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
LEVEL_PCT = {"1/2": "50%", "4/5": "80%", "9/10": "90%"}
INFEASIBLE = "infeasible under fixed use — implies demand rationing"
EVAL = ["coverage.csv", "stress_years.csv", "primary.txt"]
OUTPUTS = ["coverage_table.csv", "coverage_table.md", "august_answer.csv", "august_answer.md", "stocks_to_use_aug.csv",
           "stocks_to_use_aug.md", "stress_years.md", "fig_aug_band.png", "fig_aug_band.svg", "headline.md", "coverage_memo.md",
           "coverage_memo.csv"]
SHORT_WORDING = {"not distinguishable from 80% at this sample size, and not shown equivalent": "not distinguishable",
                 "descriptive, no wording": "descriptive"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p: Path) -> list[dict]:
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(p: Path, rows: list[dict], tag: str) -> None:
    cols = list(rows[0]) + ["inputs_sha256"]
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, inputs_sha256=tag))


def md_table(cols: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pct(x: float, nd=1) -> str:
    return f"{100 * float(x):.{nd}f}%"


def inputs(root: Path) -> dict[str, Path]:
    d = {f"out/eval/{n}": root / "out" / "eval" / n for n in EVAL}
    for n in ("forecasts.csv", "forecasts_v2.csv", "stocks_to_use.csv"):
        d[f"out/{n}"] = root / "out" / n
    d["data/store.csv"] = root / "data" / "store.csv"  # truth only (August answer, figure dots)
    d["data/quickstats_truth.csv"] = root / "data" / "quickstats_truth.csv"  # read by load_truths; sensitivity truth, unused here
    return d


def coverage_rows(cov: list[dict]) -> list[dict]:
    """M1 and PROD at 50/80 by horizon, primary row first, then B1 90% (descriptive). Pure re-labelling of coverage.csv."""
    out = []
    want = [(m, h, lv) for m in ("M1", "PROD") for lv in ("4/5", "1/2") for h in HORIZONS] + [("B1", h, "9/10") for h in HORIZONS]
    idx = {(r["model"], r["horizon"], r["level"]): r for r in cov if r["truth"] == "primary"}
    for m, h, lv in want:
        r = idx.get((m, h, lv))
        if r is None:
            continue
        st = r["status"]
        out.append({"model": m, "horizon": h, "level": LEVEL_PCT[lv], "n": r["n"], "x": r["x"], "coverage": r["coverage"],
                    "cp90_lo": r["cp90_lo"], "cp90_hi": r["cp90_hi"], "expected_coverage": r["expected_coverage"], "label": st,
                    "wording": r["wording"]})
    k = next(i for i, r in enumerate(out) if (r["model"], r["horizon"], r["level"]) == ("M1", "Aug", "80%"))
    out.insert(0, out.pop(k))  # primary row first
    assert out[0]["label"] == "primary"
    return out


def compact_rows(rows: list[dict]) -> list[dict]:
    """Memo coverage table: M1 80% May..Nov, PROD 80% Aug, B1 90% Aug; a pure re-formatting of coverage_rows (never recomputed)."""
    idx = {(r["model"], r["horizon"], r["level"]): r for r in rows}
    want = [("M1", h, "80%") for h in HORIZONS] + [("PROD", "Aug", "80%"), ("B1", "Aug", "90%")]
    out = []
    for k in want:
        r = idx[k]
        w = r["wording"] or "descriptive, no wording"
        out.append({"Model": "M1 (primary)" if r["label"] == "primary" else r["model"], "Horizon": r["horizon"], "Level": r["level"],
                    "n": r["n"], "x": r["x"], "Coverage": pct(r["coverage"]), "CP90": f"[{pct(r['cp90_lo'])}, {pct(r['cp90_hi'])}]",
                    "Wording": SHORT_WORDING.get(w, w)})
    return out


def season_bands(fc: list[dict], model: str, h: str) -> dict[int, dict]:
    """season -> {point, q_lo, q_hi (percent-error, Fraction), val_lo, val_hi, unit} of the 80% band."""
    res = {}
    for r in fc:
        if r["model"] == model and r["horizon"] == h and r["kind"] == "level" and r["level"] == "4/5" and r["scored"] == "1":
            res[int(r["season"])] = {k: (Fraction(r[k]) if k != "unit" else r[k]) for k in ("point", "q_lo", "q_hi", "val_lo", "val_hi", "unit")}
    return res


def quant(fc: list[dict], model: str, h: str, season: int, tau: str) -> Fraction:
    r = [r for r in fc if r["model"] == model and r["horizon"] == h and r["season"] == str(season) and r["kind"] == "tau" and r["tau"] == tau]
    assert len(r) == 1, (model, h, season, tau)
    return Fraction(r[0]["val"])


def build(root: Path = ROOT) -> Path:
    from .baselines import load
    from .evaluate import load_truths
    ins = inputs(root)
    shas = {k: sha(p) for k, p in ins.items()}
    tag = hashlib.sha256("".join(f"{k}={v}\n" for k, v in sorted(shas.items())).encode()).hexdigest()
    foot = "\n\nInputs (sha256):\n" + "\n".join(f"- `{k}` {v}" for k, v in sorted(shas.items())) + f"\n\nCombined inputs hash: `{tag}`\n"
    out = root / "out" / "memo"
    out.mkdir(parents=True, exist_ok=True)

    cov = read(root / "out" / "eval" / "coverage.csv")
    fc = read(root / "out" / "forecasts.csv")
    stu = read(root / "out" / "stocks_to_use.csv")
    stress = read(root / "out" / "eval" / "stress_years.csv")
    truths = load_truths(root, load()[0])

    # 1. coverage table
    rows = coverage_rows(cov)
    write_csv(out / "coverage_table.csv", rows, tag)
    md = ["# Coverage by horizon (primary truth: January first-final)", "",
          "M1 = yield band (bu/ac), PROD = production band (Mbu), B1 = USDA's printed 90% production band. n = scored seasons, "
          "x = seasons whose truth lies inside the band, CP90 = two-sided 90% Clopper-Pearson interval for x/n, expected = "
          "coverage the conservative estimator guarantees for that n. The first row is the PREREG primary object.", ""]
    md.append(md_table(["Model", "Horizon", "Level", "n", "x", "Coverage", "CP90", "Expected", "Label", "Wording"],
                       [[r["model"], r["horizon"], r["level"], r["n"], r["x"], pct(r["coverage"]),
                         f"[{pct(r['cp90_lo'])}, {pct(r['cp90_hi'])}]", pct(r["expected_coverage"]) if r["expected_coverage"] else "",
                         r["label"], r["wording"] or "descriptive, no wording"] for r in rows]))
    (out / "coverage_table.md").write_text("\n".join(md) + foot)

    cm = compact_rows(rows)
    write_csv(out / "coverage_memo.csv", cm, tag)
    (out / "coverage_memo.md").write_text(md_table(list(cm[0]), [list(r.values()) for r in cm]) + "\n")

    # 2. August answer, latest season
    m1 = season_bands(fc, "M1", "Aug")
    pr = season_bands(fc, "PROD", "Aug")
    latest = max(m1)
    assert latest == max(pr)
    ty, tp = truths.yield_.get(latest), truths.prod.get(latest)
    ans = []
    for model, unit, b, tr in (("M1", "bu/ac", m1[latest], ty), ("PROD", "Mbu", pr[latest], tp)):
        q = {t: quant(fc, model, "Aug", latest, t) for t in ("1/10", "1/2", "9/10")}
        ok = None if tr is None else b["val_lo"] <= tr <= b["val_hi"]
        d = 1 if unit == "bu/ac" else 0
        ans.append({"season": latest, "object": model, "unit": unit, "usda_point": f"{float(b['point']):.{d}f}",
                    "q10": f"{float(q['1/10']):.{d}f}", "q50": f"{float(q['1/2']):.{d}f}", "q90": f"{float(q['9/10']):.{d}f}",
                    "band80_lo": f"{float(b['val_lo']):.{d}f}", "band80_hi": f"{float(b['val_hi']):.{d}f}",
                    "truth": "not yet released" if tr is None else f"{float(tr):.{d}f}",
                    "truth_inside_band": "" if ok is None else int(ok)})
    write_csv(out / "august_answer.csv", ans, tag)
    a1, a2 = ans
    (out / "august_answer.md").write_text(
        f"# August answer, crop {latest} (origin {next(r['origin_date'] for r in fc if r['model'] == 'M1' and r['horizon'] == 'Aug' and r['season'] == str(latest))})\n\n"
        + md_table(["Object", "Unit", "USDA point", "q10", "q50", "q90", "80% band", "Truth (Jan first-final)", "Inside"],
                   [[a["object"], a["unit"], a["usda_point"], a["q10"], a["q50"], a["q90"], f"[{a['band80_lo']}, {a['band80_hi']}]",
                     a["truth"], {"": "", 1: "yes", 0: "no"}[a["truth_inside_band"]]] for a in ans])
        + "\n\nM1 is in bushels per acre; PROD is in million bushels. The 80% band is the 10th to 90th percentile." + foot)

    # 3. stocks-to-use at August
    srows = [r for r in stu if r["horizon"] == "Aug"]
    s_out = []
    for r in srows:
        neg = float(r["ending_stocks_q10"]) < 0
        s_out.append({"season": r["season"], "origin_date": r["origin_date"], "stu_usda_pct": r["stocks_to_use_pct"],
                      "stu_q10_pct": "" if neg else r["stocks_to_use_pct_q10"], "stu_q50_pct": r["stocks_to_use_pct_q50"],
                      "stu_q90_pct": r["stocks_to_use_pct_q90"], "ending_stocks_q10_mbu": r["ending_stocks_q10"],
                      "flag": INFEASIBLE if neg else "", "label": r["label"]})
    write_csv(out / "stocks_to_use_aug.csv", s_out, tag)
    ls = s_out[-1]
    assert ls["season"] == str(latest) and not ls["flag"]
    decision = f"At the 10th percentile, stocks-to-use falls from {float(ls['stu_usda_pct']):.1f}% to {float(ls['stu_q10_pct']):.1f}%."
    lab = srows[-1]["label"]
    (out / "stocks_to_use_aug.md").write_text(
        f"# Stocks-to-use at the August origin\n\n**Crop {latest}: {decision}**\n\nThe label of every row: {lab}.\n"
        "Production quantiles pass through USDA's balance sheet at that origin; beginning stocks, imports and total use stay fixed.\n\n"
        + md_table(["Season", "USDA point", "q10", "q50", "q90"],
                   [[s["season"], f"{float(s['stu_usda_pct']):.1f}%", s["flag"] or f"{float(s['stu_q10_pct']):.1f}%",
                     f"{float(s['stu_q50_pct']):.1f}%", f"{float(s['stu_q90_pct']):.1f}%"] for s in s_out])
        + "\n\nRows marked infeasible would need ending stocks below zero at q10 with use held fixed; the model then says nothing "
        "about the percentage, because in reality demand would be rationed." + foot)

    # 4. stress years
    cols = ["Season", "Model"] + HORIZONS
    srt = sorted((r for r in stress if r["model"] in ("M1", "PROD")), key=lambda r: (int(r["season"]), r["model"]))
    cell = {(r["season"], r["model"], r["horizon"]): f"{r['tqs']} ({r['covered_0.5']}/{r['covered_0.8']})" for r in srt}
    keys = sorted({(r["season"], r["model"]) for r in srt}, key=lambda k: (int(k[0]), k[1]))
    (out / "stress_years.md").write_text(
        "# Stress years: 2012, 2019, 2020\n\nEach cell: truncated quantile score (lower is better), then (covered by 50% band / "
        "covered by 80% band), 1 = inside. Descriptive; no test, no wording.\n\n"
        + md_table(cols, [[s, m] + [cell.get((s, m, h), "") for h in HORIZONS] for s, m in keys]) + foot)

    # 5. figure
    figure(out, m1, truths, cov, shas)

    # 6. headline
    prim = (root / "out" / "eval" / "primary.txt").read_text()
    mx = re.search(r"x = (\d+) of n = (\d+) seasons covered \(([\d.]+%)\); two-sided 90% Clopper-Pearson interval \[([\d.]+%), ([\d.]+%)\]", prim)
    wd = re.search(r'Wording: "([^"]+)"', prim).group(1)
    m3 = re.findall(r'Wording: "([^"]+)"', prim)[1]
    exp = float(next(r["expected_coverage"] for r in cov if (r["model"], r["horizon"], r["level"], r["truth"]) == ("M1", "Aug", "4/5", "primary")))
    v2_same = sha(root / "out" / "forecasts.csv") == sha(root / "out" / "forecasts_v2.csv")
    assert v2_same, "forecasts_v2.csv is no longer byte-identical to forecasts.csv; the headline's v2 note would be wrong"
    x, n, c, lo, hi = mx.groups()
    (out / "headline.md").write_text(
        f"**Primary result.** M1's 80% band at the August origin covered {x} of {n} seasons ({c}); two-sided 90% "
        f"Clopper-Pearson interval [{lo}, {hi}]. Wording: \"{wd}\".\n\n"
        f"**M3 against M1 (August).** {m3}\n\n"
        f"**Estimator note.** The estimator is conservative by design: at n = {n} the 80% band is expected to cover "
        f"{pct(exp, 2)}, so about {round(100 * (exp - 0.8))} points of the excess over 80% are expected from the estimator itself.\n\n"
        "**Re-post sensitivity.** `forecasts_v2.csv` is byte-identical to `forecasts.csv` (v2 re-posts carry identical values), "
        "so the re-post sensitivity equals the primary by construction." + foot)
    (out / "manifest.txt").write_text(f"combined inputs sha256 {tag}\n" + "\n".join(f"{k} {v}" for k, v in sorted(shas.items())) + "\n")
    return out


def figure(out: Path, m1: dict, truths, cov: list[dict], shas: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "cfstore-memo"
    import matplotlib.pyplot as plt

    seasons = sorted(m1)
    rows = []
    for s in seasons:
        b, t = m1[s], truths.yield_.get(s)
        if t is None:
            continue
        e = 100 * float((t - b["point"]) / b["point"])  # percent error of the truth against USDA's August yield
        rows.append((s, float(b["q_lo"]), float(b["q_hi"]), e, b["q_lo"] <= 100 * (t - b["point"]) / b["point"] <= b["q_hi"]))
    hit = sum(r[4] for r in rows)
    ref = next(r for r in cov if (r["model"], r["horizon"], r["level"], r["truth"]) == ("M1", "Aug", "4/5", "primary"))
    assert (hit, len(rows)) == (int(ref["x"]), int(ref["n"])), "figure hit/miss disagrees with out/eval/coverage.csv"
    blue, verm = "#0072B2", "#D55E00"  # Okabe-Ito
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.axhline(0, color="0.5", lw=0.8)
    for s, lo, hi, e, ok in rows:
        ax.vlines(s, lo, hi, color=blue if ok else verm, lw=5, alpha=0.45, capstyle="butt")
        ax.plot(s, e, "o" if ok else "D", color="black" if ok else verm, ms=4.5 if ok else 7, mec="black" if not ok else None,
                zorder=3)
    ax.set_xlabel("Crop season")
    ax.set_ylabel("Final yield vs USDA August yield (%)")
    ax.set_xticks([s for s in seasons if s % 2 == 0])
    ax.tick_params(axis="x", labelrotation=45)
    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([], [], color=blue, lw=5, alpha=0.45, label="M1 80% band"),
                       Line2D([], [], marker="o", color="black", ls="", ms=4.5, label="Final yield, inside band"),
                       Line2D([], [], marker="D", color=verm, mec="black", ls="", ms=7, label="Final yield, outside band")],
              loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=3, fontsize=8, frameon=False, handlelength=1.5, columnspacing=1.2)
    fig.tight_layout()
    fig.savefig(out / "fig_aug_band.png", dpi=200, metadata={"Software": None})
    fig.savefig(out / "fig_aug_band.svg", metadata={"Date": None, "Creator": "cfstore.memo"})
    plt.close(fig)
