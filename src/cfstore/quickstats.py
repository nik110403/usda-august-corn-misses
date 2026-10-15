"""Sensitivity truth (PREREG 1): latest NASS Quick Stats national corn-for-grain yield and production by crop year, from the
public bulk file https://www.nass.usda.gov/datasets/qs.crops_<date>.txt.gz (keyless). The file is a pseudo-vintage: it is the
latest revised value as of the file date. Raw file + sha256 go into data/manifest.csv like every other raw; the extract is
data/quickstats_truth.csv (year, yield, production). Descriptive only (PREREG 9.5): no test uses it.

Also prints the F5 decision-row check (a data-validation number, not a PREREG score): does the August 2025 NASS production
reliability replication reach the printed RMSE 3.8 with Quick Stats finals?
"""
import csv
import gzip
import hashlib
from datetime import datetime, timezone
from fractions import Fraction

from .fetch import DATA, MANIFEST, ROOT, FIELDS, read_manifest
from .fetch import _append

URL = "https://www.nass.usda.gov/datasets/qs.crops_20261015.txt.gz"
RAW = DATA / "raw" / "quickstats" / "2026-10-16" / "qs.crops_20261015.txt.gz"
TRUTH = DATA / "quickstats_truth.csv"
YIELD_DESC = "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE"
PROD_DESC = "CORN, GRAIN - PRODUCTION, MEASURED IN BU"


def register() -> None:
    part = RAW.with_name(RAW.name + ".part")
    if part.exists() and not RAW.exists():
        part.rename(RAW)
    if URL in read_manifest():
        return
    h = hashlib.sha256()
    with RAW.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = datetime.fromtimestamp(RAW.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _append({"url": URL, "retrieval_utc": got, "sha256": h.hexdigest(), "bytes": RAW.stat().st_size,
             "local_path": str(RAW.relative_to(ROOT))})


def extract() -> list[dict]:
    out = []
    with gzip.open(RAW, "rt", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            if (r["SOURCE_DESC"] == "SURVEY" and r["AGG_LEVEL_DESC"] == "NATIONAL" and r["FREQ_DESC"] == "ANNUAL"
                    and r["REFERENCE_PERIOD_DESC"] == "YEAR" and r["DOMAIN_DESC"] == "TOTAL"
                    and r["SHORT_DESC"] in (YIELD_DESC, PROD_DESC) and 1995 <= int(r["YEAR"]) <= 2025):
                out.append({"year": int(r["YEAR"]), "stat": "yield" if r["SHORT_DESC"] == YIELD_DESC else "production",
                            "value": r["VALUE"].replace(",", "").strip(), "load_time": r["LOAD_TIME"]})
    return out


def write_truth() -> dict:
    rows = extract()
    by = {}
    for r in rows:
        if r["stat"] in by.get(r["year"], {}):
            raise ValueError(f"Quick Stats: duplicate national annual {r['stat']} row for {r['year']} "
                             f"(values {by[r['year']][r['stat']]['value']} and {r['value']}); refusing to overwrite")
        by.setdefault(r["year"], {})[r["stat"]] = r
    assert sorted(by) == list(range(1995, 2026)) and all(len(v) == 2 for v in by.values()), sorted(by)
    mani = read_manifest()[URL]
    with TRUTH.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["crop_year", "yield_bu_ac", "production_bu", "load_time", "pseudo_vintage", "source_sha256"])
        for y in sorted(by):
            w.writerow([y, by[y]["yield"]["value"], by[y]["production"]["value"], by[y]["yield"]["load_time"],
                        "latest Quick Stats value as of file qs.crops_20261015 (retrieved %s)" % mani["retrieval_utc"], mani["sha256"]])
    return by


def replication_check(by: dict) -> str:
    from .baselines import load
    from .replication import PRINTED, forecasts, replicate
    store, _ = load()
    fc = forecasts(store, 8)
    fin = {y: Fraction(by[y]["production"]["value"]) / 1000 for y in by}  # BU -> thousand bushels
    lines = ["Quick Stats finals vs the August NASS production reliability (data-validation numbers, not PREREG scores)"]
    for yr in (2015, 2025):
        r = replicate(fc, fin, yr)
        lines.append(f"Aug {yr}: RMSE {r['rmse']:.2f} (printed {float(PRINTED[yr]['rmse']):.1f}); below/above {r['below']}/{r['above']} "
                     f"(printed {PRINTED[yr]['below']}/{PRINTED[yr]['above']}); mean/min/max {r['mean_mbu']:.0f}/{r['min_mbu']:.0f}/{r['max_mbu']:.0f} Mbu")
    return "\n".join(lines)


def run() -> int:
    register()
    by = write_truth()
    txt = replication_check(by)
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "quickstats_replication.txt").write_text(txt + "\n")
    print(f"{TRUTH.relative_to(ROOT)}: {len(by)} crop years\n{txt}")
    return 0
