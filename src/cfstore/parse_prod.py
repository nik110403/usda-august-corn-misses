"""NASS Crop Production (monthly) and Crop Production Annual Summary TXT -> US corn-for-grain lines.

Stdlib only. Cross-check use only (never a model feature). Three things are read:
  * the national (`US` / `United States`) row of the corn-for-grain state table: area harvested (1,000 acres), yield
    (bu/acre) and production (1,000 bushels); each column group is `<earlier years..., current year or forecast>` and
    the LAST column of each group is the report's own figure;
  * the Aug-Nov reliability section row `Corn For Grain :Bu : RMSE pct, 90% pct, avg, smallest, largest (million
    bushels), years below, years above`;
  * for the Annual Summary (January) the same national row of the `Yield and Production` state table.
Any unrecognised layout raises.
"""
import re

TITLE = re.compile(r"Corn for Grain:?\s+Area Harvested, Yield,? and Production|"
                   r"Corn Area Planted for All Purposes, Area Harvested, Yield, and Production for Grain|"
                   r"Corn for Grain:\s+Yield and Production by State", re.I)
SUMMARY_TITLE = re.compile(r"Corn for Grain:\s+Yield and Production by State|"
                           r"Corn Area Planted for All Purposes and Harvested for Grain, Yield, and Production", re.I)
US_ROW = re.compile(r"^\s*(US|U\.S\.|United States)\b[\s.]*:(.*)$")
NUM = re.compile(r"^[\d,]+(\.\d+)?$")
YEARS = re.compile(r"\b(19|20)\d\d\b")
RELIABILITY = re.compile(r"^\s*Corn for grain[\s.]*(?::\s*Bu\s*:|bushels\s*:)\s*([\d.]+)\s+([\d.]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(\d+)\s+(\d+)\s*$",
                         re.I)

RELIABILITY_OLD = re.compile(r"^\s*Corn for grain\s+Bu\s*:\s*([\d.]+)\s+([\d.]+)\s+[\d,]+\s*::\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(\d+)\s+(\d+)\s*$",
                             re.I)  # 1995-98: 10-year record, an extra 'quantity at 90%' column and a '::' divider


TOKEN = re.compile(r"\(NA\)|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _nums(s: str) -> list[float | None]:
    """Numbers of a national row. Comma-grouped numbers are split by their grouping so that the one place where the
    source text glues two neighbours together (CropProd 2004-09-10: `10,113,88710,960,710`) still reads correctly;
    anything left over besides separators raises."""
    toks = TOKEN.findall(s)
    if not toks or TOKEN.sub("", s).strip(" :."):
        raise ValueError(f"national row is not numeric: {s!r}")
    return [None if t == "(NA)" else float(t.replace(",", "")) for t in toks]


MONTH_COL = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+1\b")


def _groups(years: list[int]) -> list[int]:
    """Sizes of the column groups of a year header: a new group starts whenever the year does not increase."""
    sizes, prev = [], None
    for y in years:
        if prev is None or y <= prev:
            sizes.append(0)
        sizes[-1] += 1
        prev = y
    return sizes


def parse_corn_table(text: str, title=TITLE, columns: bool = False) -> dict | None:
    """{'area_harvested_kac', 'yield', 'production_kbu'} (the report's own, last column of each group; the Annual
    Summary table has no area) from the national row of the first matching corn-for-grain table, or None if the
    report has no such table. A table is recognised by its header: area harvested + yield + production (3 column
    groups) or yield + production (2 groups); a title whose header is something else (e.g. the area-only page of a
    two-page table) is skipped. With columns=True (Annual Summary layout only) every column is returned instead:
    {'yield': [(crop year, value), ...], 'production_kbu': [...]}, years from the table's own year header."""
    lines = text.replace("\x0c", "\n").splitlines()
    for i, ln in enumerate(lines):
        if not title.search(ln) or "silage" in ln.lower():
            continue
        rule = next((k for k in range(i + 1, min(i + 8, len(lines))) if lines[k].lstrip().startswith("----")), None)
        if rule is None:
            continue  # table-of-contents entry
        jy = next((k for k in range(rule + 1, min(rule + 16, len(lines)))
                   if lines[k].count(":") >= 2 and len(YEARS.findall(lines[k])) >= 4), None)
        if jy is None:
            raise ValueError(f"no year header after title {ln!r}")
        end = next((k for k in range(jy + 1, min(jy + 12, len(lines))) if lines[k].lstrip().startswith("----")), jy)
        block = " ".join(lines[rule:end + 1])
        header = block.lower()
        yrs = [int(m.group(0)) for m in re.finditer(r"\b(?:19|20)\d\d\b", lines[jy])]
        if "yield" in header and "production" in header and "area harv" in header:
            names = ["area_harvested_kac", "yield", "production_kbu"]
        elif "yield" in header and "production" in header and "area" not in header and len(_groups(yrs)) == 2:
            names = ["yield", "production_kbu"]
        else:
            continue
        for ln2 in lines[jy + 1:jy + 300]:
            m = US_ROW.match(ln2)
            if not m:
                continue
            vals = _nums(m.group(2))
            if len(names) == 2:  # Annual Summary: two groups of years
                sizes = _groups(yrs)
                if len(vals) != sum(sizes):
                    raise ValueError(f"national row has {len(vals)} values for years {yrs}: {ln2!r}")
            elif len(vals) == 6:
                sizes = [2, 2, 2]
            elif len(vals) == 7:  # one extra column: the previous month's forecast (yield, from Sep 2024) or a third production year
                sizes = [2, 3, 2] if MONTH_COL.search(block) else [2, 2, 3]
            else:
                raise ValueError(f"unexpected national row width {len(vals)}: {ln2!r}")
            if columns:
                if len(names) != 2:
                    raise ValueError("columns=True is for the two-group Annual Summary layout")
                k, res = 0, {}
                for n, sz in zip(names, sizes):
                    res[n] = list(zip(yrs[k:k + sz], vals[k:k + sz]))
                    k += sz
                if any(v is None for col in res.values() for _, v in col):
                    raise ValueError(f"(NA) in the Annual Summary national row: {ln2!r}")
                return res
            out, k = {}, 0
            for n, sz in zip(names, sizes):
                k += sz
                if vals[k - 1] is None:
                    raise ValueError(f"current-report {n} is (NA): {ln2!r}")
                out[n] = vals[k - 1]
            if "area_harvested_kac" in out and abs(out["yield"] * out["area_harvested_kac"] - out["production_kbu"]) > 0.005 * out["production_kbu"]:
                raise ValueError(f"yield x area != production (column mix-up?): {out} from {ln2!r}")
            return out
        raise ValueError(f"corn table {ln!r} without a national row")
    return None


def parse_reliability(text: str, month: int) -> dict | None:
    """Corn-for-grain row of the 'Reliability of <Month> 1 Crop Production Forecasts' table. Required (raises if the
    section or the row is missing) for the August-November reports; None for other months."""
    lines = text.replace("\x0c", "\n").splitlines()
    for i, ln in enumerate(lines):
        if re.search(r"Reliability of \w+ 1 Crop Production Forecasts", ln):
            for ln2 in lines[i:i + 40]:
                m = RELIABILITY.match(ln2) or RELIABILITY_OLD.match(ln2)
                if m:
                    f = [x.replace(",", "") for x in m.groups()]
                    return {"rmse_pct": float(f[0]), "ci90_pct": float(f[1]), "mean_abs_diff_mbu": float(f[2]),
                            "min_abs_diff_mbu": float(f[3]), "max_abs_diff_mbu": float(f[4]),
                            "n_below": int(f[5]), "n_above": int(f[6])}
    if 8 <= month <= 11:
        raise ValueError("no corn-for-grain reliability row in an August-November report")
    return None
