"""F10 optional slot: Russia wheat production (PREREG-AMENDMENTS.md 2026-10-16 "F10 optional slot"; STRATEGY 6).

Object: the `Russia` production print (Mt) of the WASDE World Wheat table at each May-Nov origin, marketing year t/t+1.
Truth: the value printed for that year in the LAST archived WASDE whose wheat table still shows it (never FAS PSD); a year still
printed in the latest archived WASDE has no truth and is unscored. Model: M1 unchanged (models.conformal_rows / models.training_set,
exact-rational ranks of PREREG 3, percent errors, 9-season burn-in at every level). No other model, no baseline.

Commands (cfstore __main__): russia-store (parse -> data/russia_store.csv), russia-forecasts (-> out/russia/forecasts.csv, no truth
column), russia-preflight (availability counts only), russia (ONE run: gates, counts, coverage; `make russia`, orchestrator-run once).
Tested on synthetic data and on the archived store's structure only; no coverage is computed outside `run`.
"""
import csv
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from . import stats
from .asof import LeakageError, origins, ts
from .baselines import pct_error, season_of
from .models import (COLUMNS, LEVELS, META_COLS, RUSSIA, TAUS7, View, conformal_rows, model_features, month_of, ref, training_set,
                     write_csv)
from .parse_russia import parse_russia_txt, parse_russia_xls, parse_russia_xml

SERIES = RUSSIA
MIN_N = 9  # PREREG 3: 9-season burn-in, a band is scored only from nine own out-of-sample errors, at every level
HEADLINE_MIN = 15  # PREREG-AMENDMENTS F10 item 5: below 15 August seasons the August band is descriptive, no wording
HEADLINE = ("Aug", Fraction(4, 5))
HORIZONS = ["May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
MEMO_QUESTION = "how far USDA's first print of Russian wheat moves before USDA settles it"
STORE_SCHEMA = ["series", "reference_period", "release_date", "release_ts", "time_source", "source_file", "file_hash", "value",
                "revision_flag"]
FC_COLUMNS = ["model", "horizon", "season", "origin_date", "origin_ts", "kind", "tau", "level", "unit", "point", "n", "k", "k_lo",
              "k_hi", "q", "q_lo", "q_hi", "val", "val_lo", "val_hi", "exp_cov", "scored", "origin_flag", "note"]
assert set(FC_COLUMNS) <= set(COLUMNS)


# ---- store -----------------------------------------------------------------------------------------------------------

def parse_release(path: Path, month: int) -> list[dict]:
    if path.suffix == ".txt":
        return parse_russia_txt(path.read_bytes().decode("latin-1"), month)
    if path.suffix == ".xml":
        return parse_russia_xml(path.read_bytes(), month)
    if path.suffix == ".xls":
        return parse_russia_xls(str(path), month)
    raise ValueError(f"unsupported WASDE file {path}")


def projection_year(release_date: str) -> int:
    """The marketing year (start year) that is the projection year in a WASDE of this date: t from May of t (new MY), t-1 before."""
    y, m = int(release_date[:4]), int(release_date[5:7])
    return y if m >= 5 else y - 1


def check_release_rows(release: dict, rows: list[dict]) -> None:
    """Three consecutive marketing years, the last = the projection year of the release date (raises otherwise)."""
    ys = [int(r["reference_period"][:4]) for r in rows]
    top = projection_year(release["release_date"])
    if ys != [top - 2, top - 1, top] or any(r["reference_period"] != ref(int(r["reference_period"][:4])) for r in rows):
        raise ValueError(f"{release['release_date']}: Russia marketing years {[r['reference_period'] for r in rows]}, expected {top - 2}..{top}")


def build_russia_store(releases: list[dict], root: Path, manifest: dict | None = None) -> list[dict]:
    """One row per (release, marketing year) of the Russia production line, from every release in wasde_releases.csv (primary,
    corrected-only and the one reissue; v2 re-posts are not in that table). Release date/time come from the release table (the same
    release-time table as the US series). A release with no wheat table (the abbreviated Oct 2008 reissue) contributes nothing;
    any other release without a Russia row raises."""
    store = []
    for r in releases:
        path = root / r["file"]
        rows = parse_release(path, int(r["release_date"][5:7]))
        if not rows:
            if r["revision_flag"] == "v1_reissue":
                continue
            raise ValueError(f"{r['release_date']} ({r['file']}): no Russia row in the world wheat table")
        check_release_rows(r, rows)
        fh = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest is not None:
            man = next(m for m in manifest.values() if m["local_path"] == r["file"])
            assert man["sha256"] == fh, f"manifest hash mismatch for {path}"
        for p in rows:
            store.append({"series": SERIES, "reference_period": p["reference_period"], "release_date": r["release_date"],
                          "release_ts": r["release_ts"], "time_source": "wasde_releases.csv (release_times.csv:wasde)",
                          "source_file": r["file"], "file_hash": fh, "value": repr(p["value"]), "revision_flag": r["revision_flag"]})
    store.sort(key=lambda x: (x["reference_period"], x["release_date"], x["revision_flag"]))
    return store


def validate_origins(store: list[dict], releases: list[dict]) -> list[dict]:
    """Every primary WASDE May-Nov carries exactly one Russia projection value for its own marketing year."""
    ors = origins(releases)
    for o in ors:
        c = [r for r in store if r["release_date"] == o["release_date"] and r["source_file"] == o["file"]
             and r["reference_period"] == ref(season_of(o))]
        if len(c) != 1:
            raise ValueError(f"origin {o['release_date']}: {len(c)} Russia projection values for {ref(season_of(o))}")
    return ors


def write_store(path: Path, store: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, STORE_SCHEMA, lineterminator="\n")
        w.writeheader()
        w.writerows(store)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---- truth -----------------------------------------------------------------------------------------------------------

def russia_truths(store: list[dict]) -> dict[int, dict]:
    """{season: truth row}: the value printed in the last archived WASDE whose wheat table still shows that marketing year. A year
    printed in the latest archived WASDE has no truth (absent from the dict)."""
    rows = [r for r in store if r["series"] == SERIES and r["revision_flag"] != "v2_repost"]
    if not rows:
        return {}
    latest = max(ts(r) for r in rows)
    still = {r["reference_period"] for r in rows if ts(r) == latest}
    by_ref: dict[str, dict] = {}
    for r in rows:
        if r["reference_period"] in still:
            continue
        cur = by_ref.get(r["reference_period"])
        if cur is None or (ts(r), r["release_date"], r["source_file"]) > (ts(cur), cur["release_date"], cur["source_file"]):
            by_ref[r["reference_period"]] = r
    return {int(k[:4]): v for k, v in by_ref.items()}


def assert_truth_after_origins(truths: dict[int, dict], ors: list[dict]) -> None:
    """PREREG-AMENDMENTS F10 items 3/6: a truth vintage strictly later (date and time) than every origin of its season."""
    last = {}
    for o in ors:
        s = season_of(o)
        if s not in last or ts(o) > ts(last[s]):
            last[s] = o
    for s, tr in truths.items():
        if s in last and (ts(tr) <= ts(last[s]) or tr["release_date"] <= last[s]["release_date"]):
            raise LeakageError(f"Russia truth vintage {tr['release_ts']} of season {s} does not follow the last origin {last[s]['release_ts']}")


# ---- M1 ---------------------------------------------------------------------------------------------------------------

class RView(View):
    """models.View whose truth is the Russia truth vintage, available only if that release is inside the guarded view (= strictly
    before the origin, as-of); never a revised or in-view-latest value."""

    def __init__(self, feats: list[dict], truths: dict[int, dict]):
        super().__init__("M1R", feats)
        self._truths = truths

    def truth(self, series: str, season: int) -> dict | None:
        self._ok(series)
        tr = self._truths.get(season)
        if tr is None or tr not in self._ix.get((series, tr["reference_period"], tr["release_date"]), []):
            return None
        return tr


class RussiaRun:
    def __init__(self, store: list[dict], releases: list[dict]):
        self.store = [r for r in store if r["series"] == SERIES]
        self.ors = validate_origins(self.store, releases)
        self.by_sm = {(season_of(o), month_of(o)): o for o in self.ors}
        self.truths = russia_truths(self.store)
        assert_truth_after_origins(self.truths, self.ors)

    def m1(self, o: dict) -> list[dict]:
        v = RView(model_features("M1R", self.store, o), self.truths)
        tr = training_set(v, o, SERIES, self.by_sm)
        point = v.val(SERIES, season_of(o), o["release_date"])
        return conformal_rows("M1", o, point, "Mt", [e for _, e, _, _ in tr], min_n=MIN_N, in_tests=0,
                              note="F10 Russia wheat, exploratory")

    def all_rows(self) -> list[dict]:
        return [row for o in self.ors for row in self.m1(o)]


# ---- gates and identity -------------------------------------------------------------------------------------------------

def russia_identity(root: Path) -> dict:
    """As models.build_identity (git object ids of src/, pyproject.toml, uv.lock; +dirty), but the data hash covers
    data/russia_store.csv and data/wasde_releases.csv, the inputs of the Russia forecasts."""
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    paths = ("src", "pyproject.toml", "uv.lock")
    tree = hashlib.sha256("\n".join(f"{p} {git('rev-parse', 'HEAD:' + p)}" for p in paths).encode()).hexdigest()[:40]
    if git("status", "--porcelain", "--", *paths):
        tree += "+dirty"
    h = hashlib.sha256()
    for name in ("russia_store.csv", "wasde_releases.csv"):
        h.update((root / "data" / name).read_bytes())
    return {"build_commit": git("rev-parse", "HEAD"), "code_tree": tree, "store_sha256": h.hexdigest()}


def run_leakage(root: Path) -> list[dict]:
    """The as-of leakage tests (tests/test_leakage.py, tests/test_models.py, tests/test_russia.py; same set as evaluate plus Russia): any failure blocks the Russia output."""
    from .evaluate import parse_leakage
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_leakage.py", "tests/test_models.py", "tests/test_russia.py", "-q", "-rA",
                        "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
    return parse_leakage(p, root)


# ---- availability (no band, point or truth comparison) ------------------------------------------------------------------

def fc_index(rows: list[dict]) -> dict[tuple[str, int, Fraction], dict]:
    return {(r["horizon"], int(r["season"]), Fraction(r["level"])): r for r in rows if r["kind"] == "level"}


def count_lines(rows: list[dict], truths: dict[int, dict], label: str = "") -> list[str]:
    """Scored seasons per horizon and level, and how many of them have a truth value. Availability only: nothing is compared."""
    ix = fc_index(rows)
    out = [f"Russia wheat M1 availability counts{label} (scored forecast row / of those with a truth value; no coverage computed):"]
    for h in HORIZONS:
        parts = []
        for lev in LEVELS:
            seas = sorted(s for (hh, s, lv), r in ix.items() if hh == h and lv == lev and r["scored"] == "1")
            parts.append(f"{float(lev):.0%} band {len(seas)}/{sum(s in truths for s in seas)}")
        out.append(f"  {h}: " + "; ".join(parts))
    return out


def scored_seasons(rows: list[dict], truths: dict[int, dict], h: str, lev: Fraction) -> list[int]:
    return sorted(s for (hh, s, lv), r in fc_index(rows).items() if hh == h and lv == lev and r["scored"] == "1" and s in truths)


# ---- scoring (only inside run) -----------------------------------------------------------------------------------------

def covered(row: dict, truth: dict) -> bool:
    e = pct_error(Fraction(truth["value"]), Fraction(row["point"]))  # exact percent error vs exact rational quantiles (PREREG 2)
    return Fraction(row["q_lo"]) <= e <= Fraction(row["q_hi"])


def coverage_rows(rows: list[dict], truths: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    ix = fc_index(rows)
    cov, per = [], []
    n_aug80 = len(scored_seasons(rows, truths, *HEADLINE))
    for h in HORIZONS:
        for lev in LEVELS:
            seas = scored_seasons(rows, truths, h, lev)
            if not seas:
                continue
            hits = {s: covered(ix[(h, s, lev)], truths[s]) for s in seas}
            x, n = sum(hits.values()), len(seas)
            lo, hi = stats.clopper_pearson(x, n)
            exp = sum(Fraction(ix[(h, s, lev)]["exp_cov"]) for s in seas) / n
            headline = (h, lev) == HEADLINE
            word = ""
            if headline and n >= HEADLINE_MIN:
                status, word = "exploratory", stats.wording(x, n, lev)[0]
            else:
                status = "descriptive"
            cov.append({"horizon": h, "level": str(lev), "n": n, "x": x, "coverage": f"{x / n:.4f}", "cp90_lo": f"{lo:.4f}",
                        "cp90_hi": f"{hi:.4f}", "nominal": f"{float(lev):.2f}", "expected_coverage": f"{float(exp):.4f}",
                        "status": status, "wording": word,
                        "note": ("headline object, exploratory, outside the 33-test family, no Holm adjustment" if status == "exploratory" else
                                 f"August 80% has {n_aug80} < {HEADLINE_MIN} scored seasons: descriptive, no wording" if headline else "")})
            per += [{"horizon": h, "level": str(lev), "season": s, "covered": int(c)} for s, c in hits.items()]
    return cov, per


def truth_rows_out(truths: dict[int, dict], ors: list[dict], store: list[dict]) -> list[dict]:
    last = {}
    for o in ors:
        last[season_of(o)] = max(last.get(season_of(o), o["release_date"]), o["release_date"])
    seasons = sorted({season_of(o) for o in ors})
    return [{"season": s, "reference_period": ref(s), "status": "scored" if s in truths else "unscored (still printed in the latest archived WASDE)",
             "truth_mt": truths[s]["value"] if s in truths else "", "truth_vintage": truths[s]["release_date"] if s in truths else "",
             "last_origin": last[s]} for s in seasons]


# Scored seasons with truth per (horizon, level), pinned from `make russia-preflight` on 2026-10-16 before any score existed.
EXPECTED_N = {**{(h, Fraction(1, 2)): 18 for h in ("May", "Jun", "Jul", "Aug", "Sep", "Nov")},
              **{(h, Fraction(4, 5)): 18 for h in ("May", "Jun", "Jul", "Aug", "Sep", "Nov")},
              **{(h, Fraction(9, 10)): 8 for h in ("May", "Jun", "Jul", "Aug", "Sep", "Nov")},
              ("Oct", Fraction(1, 2)): 17, ("Oct", Fraction(4, 5)): 17, ("Oct", Fraction(9, 10)): 7}


def evaluate_russia(rows: list[dict], truths: dict[int, dict], ors: list[dict], store: list[dict],
                    expected: dict | None = EXPECTED_N) -> dict:
    print("\n".join(count_lines(rows, truths)))  # the scored-season count is printed before any coverage is computed
    sys.stdout.flush()
    if expected is not None:
        bad = {k: (len(scored_seasons(rows, truths, *k)), v) for k, v in expected.items()
               if len(scored_seasons(rows, truths, *k)) != v}
        if bad:
            raise SystemExit(f"REFUSING: Russia scored-season counts differ from the pinned preflight counts (got, pinned): {bad}")
    cov, per = coverage_rows(rows, truths)
    n = len(scored_seasons(rows, truths, *HEADLINE))
    prim = [f"Russia wheat production, M1 (exploratory; PREREG-AMENDMENTS 2026-10-16 F10). Question: {MEMO_QUESTION}.",
            f"Scored August seasons (80% band, with truth): {n}"
            + ("" if n >= HEADLINE_MIN else f" (< {HEADLINE_MIN}: descriptive only, no wording)"), ""]
    for c in cov:
        prim.append(f"{c['horizon']} {float(Fraction(c['level'])):.0%}: x/n = {c['x']}/{c['n']}, coverage {c['coverage']}, "
                    f"90% CP [{c['cp90_lo']}, {c['cp90_hi']}], {c['status']}" + (f" - {c['wording']}" if c["wording"] else ""))
    return {"coverage.csv": cov, "seasons_covered.csv": per, "truth.csv": truth_rows_out(truths, ors, store), "primary.txt": prim}


def write_outputs(out: dict, outdir: Path, commit: str, stamp: str, hashes: dict) -> list[Path]:
    from .evaluate import write_outputs as w
    return w(out, outdir, commit, stamp, hashes)


# ---- commands ----------------------------------------------------------------------------------------------------------

def _root(root):
    from .fetch import ROOT
    return root or ROOT


def cmd_store(root: Path | None = None) -> str:
    from .fetch import read_manifest
    root = _root(root)
    releases = read_csv(root / "data" / "wasde_releases.csv")
    store = build_russia_store(releases, root, read_manifest())
    validate_origins(store, releases)
    write_store(root / "data" / "russia_store.csv", store)
    return f"data/russia_store.csv: {len(store)} rows from {len({r['release_date'] for r in store})} releases"


def load(root: Path) -> tuple[list[dict], list[dict]]:
    return read_csv(root / "data" / "russia_store.csv"), read_csv(root / "data" / "wasde_releases.csv")


def cmd_forecasts(root: Path | None = None) -> str:
    root = _root(root)
    store, releases = load(root)
    rows = RussiaRun(store, releases).all_rows()
    ident = russia_identity(root)
    write_csv(root / "out" / "russia" / "forecasts.csv", [dict(r, **ident) for r in rows], FC_COLUMNS + META_COLS)
    return f"out/russia/forecasts.csv: {len(rows)} rows"


def run_preflight(root: Path | None = None) -> int:
    """Availability counts on the real store: forecast rows built in memory, no truth-vs-band comparison, no coverage."""
    root = _root(root)
    store, releases = load(root)
    rr = RussiaRun(store, releases)
    print("\n".join(count_lines([{k: str(v) for k, v in r.items()} for r in rr.all_rows()], rr.truths)))
    n25 = sum(season_of(o) <= 2025 for o in rr.ors)
    print(f"origins with a Russia projection: {len(rr.ors)} ({n25} in 1995-2025, {len(rr.ors) - n25} in 2026); "
          f"marketing years with a truth vintage: {len(rr.truths)}")
    return 0


def run(root: Path | None = None) -> int:
    from .evaluate import check_fresh, code_commit, read_hashed, verify_prereg
    root = _root(root)
    verify_prereg(root)
    leakage = run_leakage(root)  # exits on any failure: no output is written
    store, releases = load(root)
    rr = RussiaRun(store, releases)
    fc, h = read_hashed(root / "out" / "russia" / "forecasts.csv")
    check_fresh(fc, russia_identity(root), "out/russia/forecasts.csv")
    out = evaluate_russia(fc, rr.truths, rr.ors, rr.store, expected=EXPECTED_N)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hashes = {"forecasts_sha256": h}
    if hashlib.sha256((root / "out" / "russia" / "forecasts.csv").read_bytes()).hexdigest() != h:
        raise SystemExit("REFUSING TO WRITE: out/russia/forecasts.csv changed while the evaluation ran")
    files = write_outputs(out, root / "out" / "russia", code_commit(root), stamp, hashes)
    print("\n".join(out["primary.txt"]))
    print(f"\nwrote {len(files)} files to out/russia/ (leakage groups passed: {', '.join(g['group'] for g in leakage)})")
    return 0
