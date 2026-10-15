"""NASS series ingest into the as-of store: Crop Progress (national corn Good + Excellent), Crop Production (corn
yield/production cross-check + Aug-Nov reliability row) and the Crop Production Annual Summary (January final).

Same conventions as the WASDE ingest (store.py): one release = one date; release_date is the report's printed
release date (the ESMIS listing lags or mis-dates a few files, logged in NASS_DATE_EXCEPTIONS), except for
corrected-only files, which take the ESMIS listing date (the day the corrected vintage became visible; the original
is not in ESMIS). The first (plain) file of a date wins; a date whose only file is `_correction` / `_revision` is
`v1_corrected_only`. The time of day always comes from data/release_times.csv, never from ESMIS (12:00 placeholder).
"""
import csv
import hashlib
import json
import re
from datetime import date

from .fetch import DATA, ROOT, fetch, read_manifest
from .listing import build_listing
from .parse_cp import released_date, sunday_before, parse_progress
from .parse_prod import SUMMARY_TITLE, parse_corn_table, parse_reliability
from .times import release_ts

SPECS = {  # ESMIS identifier -> (series root, raw dir, report file-name regex, correction-name regex)
    "CropProg": ("crop_progress", "crop_progress", re.compile(r"^(CropProg-\d\d-\d\d-\d{4}(_correction|_revision)?|prog\d{4}(_\d)?)\.txt$"),
                 re.compile(r"_(correction|revision)\.txt$")),
    "CropProd": ("crop_production", "crop_production", re.compile(r"^(CropProd-\d\d-\d\d-\d{4}(_revision)?|crop\d{4})\.txt$"),
                 re.compile(r"_revision\.txt$")),
    "CropProdSu": ("crop_production_summary", "crop_production_summary",
                   re.compile(r"^(CropProdSu-\d\d-\d\d-\d{4}(_revision)?|cropan\d\d)\.txt$"), re.compile(r"_revision\.txt$")),
}
API = "https://esmis.nal.usda.gov/api/v1/release/findByIdentifier/{}?page={}"
FIRST_YEAR = "1995"
MIN_REPORT_LINES = 300  # Crop Production / Summary files shorter than this are notices (the three Oct-Nov 2013 shutdown notices)
RELEASES_CSV = DATA / "nass_releases.csv"
SELECTION_CSV = DATA / "nass_selection.csv"
LISTING_CSV = DATA / "nass_listing.csv"
REL_FIELDS = ["series_root", "release_date", "release_ts", "file", "revision_flag", "n_values"]
SEL_FIELDS = ["ident", "listing_date", "listing_id", "fname", "role", "release_date", "revision_flag", "reason"]
# (ident, ESMIS listing date) -> (printed release date, reason): where the report's own release date differs from the
# ESMIS listing date. Anything else that differs raises. Printed date is the true release; the listing is wrong or lags.
NASS_DATE_EXCEPTIONS = {
    ("CropProg", "1999-04-15"): ("1999-04-12", "report says Released April 12, 1999 (a Monday); ESMIS lists Thursday the 15th"),
    ("CropProg", "2001-04-24"): ("2001-04-30", "the file listed on 2001-04-24 is the report 'Released April 30, 2001' (no 2001-04-30 listing exists)"),
    ("CropProd", "2002-05-12"): ("2002-05-10", "ESMIS date is a Sunday; the report says Released May 10, 2002 (same day as WASDE-361's May 10)"),
    ("CropProd", "2005-09-13"): ("2005-09-12", "report says Released September 12, 2005 (same day as the WASDE); ESMIS lists the 13th"),
}
# Files whose own text says they are a corrected / updated re-release although the file name carries no marker.
# (ident, ESMIS listing date) -> why. They are v1_corrected_only: the original is not in ESMIS.
CORRECTED_BY_TEXT = {
    ("CropProd", "2003-08-12"): ("in-text Update Alert: the corn narrative said production up 2 percent from 2001, actual up 6 percent "
                                 "(CropProd-08-12-2003.txt lines 27-30; tables untouched). Admitted as archived, PREREG-AMENDMENTS.md"),
    ("CropProg", "1997-06-24"): ("note in the file: 'These updated tables were released at 9:00 a.m., Tuesday, June 24, 1997. The crop "
                                 "condition in Kansas was published incorrectly on Monday' (printed date June 23). Stamped with the "
                                 "table time 16:00 ET on the ESMIS date, later than the 09:00 actual: conservative"),
}


# Corrected-only files whose reissue date is documented: the content became public on that date, so that is the
# release_date (the normal date rule then keeps it out of earlier information sets). Source: checks/items-C.md section 14
# (CropProd 2008-10-10 `_revision` text: "reissued on October 28"; CropProg 2015-08-03 `_correction`: "Update Alert - August 4, 2015").
REISSUE_DATES = {("CropProd", "2008-10-10"): "2008-10-28", ("CropProg", "2015-08-03"): "2015-08-04"}


def fetch_results(ident: str) -> list[dict]:
    first = json.loads(fetch(API.format(ident, 0), f"nass_listing/{ident}", "page_00.json").read_text())
    res = list(first["results"])
    for p in range(1, first["pager"]["total_pages"]):
        res += json.loads(fetch(API.format(ident, p), f"nass_listing/{ident}", f"page_{p:02d}.json").read_text())["results"]
    return res


def _candidates(ident: str, results: list[dict]) -> list[dict]:
    """One dict per listed txt file from 1995, with a unique raw sub-directory (two files of one release can share a name)."""
    out = []
    for r in sorted(results, key=lambda r: (r["release_datetime"], int(r["id"]))):
        d = r["release_datetime"][:10]
        if d < FIRST_YEAR:
            continue
        seen: set[str] = set()
        for u in r["files"]:
            fn = u.rsplit("/", 1)[1]
            if not fn.endswith(".txt"):
                continue
            sub = r["id"] if fn not in seen else f"{r['id']}_dup"
            seen.add(fn)
            out.append({"ident": ident, "listing_date": d, "listing_id": r["id"], "fname": fn, "url": u,
                        "dir": f"{SPECS[ident][1]}/{d}/{sub}"})
    return out


def is_report(ident: str, text: str) -> bool:
    """Crop Progress: a report prints `Week Ending` tables (old reports can be short, 50+ lines); delay / cancellation
    notices do not. Crop Production and the Annual Summary: notices are far shorter than any report."""
    if ident == "CropProg":
        return bool(re.search(r"Week\s+Ending", text, re.I))
    return len(text.splitlines()) >= MIN_REPORT_LINES


def read_text(path) -> str:
    return path.read_bytes().decode("latin-1")


def select(ident: str, results: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Pick one primary file per release date. Returns (primaries, selection-log rows, listing rows)."""
    _, _, name_rx, corr_rx = SPECS[ident]
    log, by_date = [], {}
    for c in _candidates(ident, results):
        base = {"ident": ident, "listing_date": c["listing_date"], "listing_id": c["listing_id"], "fname": c["fname"],
                "release_date": "", "revision_flag": ""}
        if not name_rx.match(c["fname"]) or "Special" in c["fname"]:
            log.append({**base, "role": "excluded", "reason": "not a report file name (special report / notice / readme)"})
            continue
        path = fetch(c["url"], c["dir"], c["fname"])
        text = read_text(path)
        if not is_report(ident, text):
            log.append({**base, "role": "excluded", "reason": f"notice, not a report ({len(text.splitlines())} lines)"})
            continue
        c.update(path=path, text=text, corr=bool(corr_rx.search(c["fname"])) or (ident, c["listing_date"]) in CORRECTED_BY_TEXT, base=base)
        by_date.setdefault(c["listing_date"], []).append(c)
    prim = []
    for ld, cs in sorted(by_date.items()):
        cs.sort(key=lambda c: (c["corr"], int(c["listing_id"]), c["fname"], c["dir"]))
        p = cs[0]
        for c in cs[1:]:
            if not c["corr"] and not p["corr"]:
                if c["path"].read_bytes() != p["path"].read_bytes():
                    raise ValueError(f"{ident} {ld}: two different plain files {p['fname']} / {c['fname']}")
                why = f"byte-identical duplicate of {p['fname']} (same release, listed twice)"
            else:
                why = f"correction copy of a date whose plain file {p['fname']} is kept (first-release rule)"
            log.append({**c["base"], "role": "excluded", "reason": why})
        flag = "v1_corrected_only" if p["corr"] else "v1"
        if ident == "CropProdSu":
            rel, why = ld, "ESMIS date (Annual Summary carries no printed release date; checked against the January WASDE date in the tests)"
        elif p["corr"]:
            rel, why = ld, "corrected-only file: ESMIS listing date (the original is not in ESMIS)"
            if (ident, ld) in REISSUE_DATES:
                rel = REISSUE_DATES[(ident, ld)]
                why = (f"corrected-only file; original ESMIS listing date {ld}, stamped at the documented reissue date {rel} "
                       "(when its content became public; checks/items-C.md section 14)")
            if (ident, ld) in CORRECTED_BY_TEXT:
                why += "; " + CORRECTED_BY_TEXT[(ident, ld)]
        else:
            try:
                cover = released_date(p["text"])
            except ValueError:
                cover = None
            if cover is None:
                rel, why = ld, "no printed release date in the file: ESMIS listing date"
            elif cover.isoformat() == ld:
                rel, why = ld, "printed release date == ESMIS date"
            else:
                exc = NASS_DATE_EXCEPTIONS.get((ident, ld))
                if exc is None or exc[0] != cover.isoformat():
                    raise ValueError(f"{ident} {ld}: printed release date {cover} != ESMIS date and not a documented exception")
                rel, why = cover.isoformat(), exc[1]
        if prim and any(q["release_date"] == rel for q in prim):
            raise ValueError(f"{ident}: two primary files for release date {rel}")
        p.update(release_date=rel, flag=flag)
        prim.append(p)
        log.append({**p["base"], "release_date": rel, "revision_flag": flag, "role": "primary", "reason": why})
    return prim, log, [{k: c[k] for k in ("ident", "listing_date", "listing_id", "fname", "url")} for c in _candidates(ident, results)]


def _row(series: str, ref: str, value: float) -> dict:
    return {"series": series, "reference_period": ref, "value": value}


def parse_release(ident: str, p: dict) -> list[dict]:
    """Store rows (series, reference_period, value) of one primary file."""
    d = date.fromisoformat(p["release_date"])
    if ident == "CropProg":
        r = parse_progress(p["text"])
        if r is None:
            if (6, 15) <= (d.month, d.day) <= (9, 30):  # April-May and Oct-Nov reports may lack it; mid-season ones never do
                raise ValueError(f"{p['fname']}: no corn condition line in a mid-season Crop Progress report")
            return []
        we = sunday_before(d)
        if r["week_ending"] is not None and r["week_ending"] != we:
            raise ValueError(f"{p['fname']}: printed week ending {r['week_ending']} != Sunday before release {we}")
        return [_row("crop_progress.us_corn.good_excellent", (r["week_ending"] or we).isoformat(), float(r["good_excellent"]))]
    if ident == "CropProd":
        t = parse_corn_table(p["text"])
        rel = parse_reliability(p["text"], d.month)
        if (t is None) != (rel is None):
            raise ValueError(f"{p['fname']}: corn table and reliability row disagree (table {t is not None}, reliability {rel is not None})")
        if t is None:
            if 8 <= d.month <= 11:
                raise ValueError(f"{p['fname']}: no corn table in an August-November report")
            return []
        ref = f"{d.year}/{str(d.year + 1)[2:]}"
        return ([_row(f"crop_production.us_corn.{k}", ref, v) for k, v in t.items()]
                + [_row(f"crop_production.us_corn.rel_{k}", ref, float(v)) for k, v in rel.items()])
    t = parse_corn_table(p["text"], SUMMARY_TITLE)
    if t is None:
        raise ValueError(f"{p['fname']}: no corn yield/production table in the Annual Summary")
    ref = f"{d.year - 1}/{str(d.year)[2:]}"
    return [_row(f"crop_production_summary.us_corn.{k}", ref, v) for k, v in t.items()]


def build_nass() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(store rows without sort, release log rows, selection log rows, listing rows) for all three NASS series."""
    store, rel, sel, lst = [], [], [], []
    by_path = {m["local_path"]: m for m in read_manifest().values()}
    for ident, (root, *_rest) in SPECS.items():
        prim, log, listing = select(ident, fetch_results(ident))
        sel += log
        lst += listing
        for p in prim:
            rows = parse_release(ident, p)
            ts, src = release_ts(root, date.fromisoformat(p["release_date"]))
            body = p["path"].read_bytes()
            fh = hashlib.sha256(body).hexdigest()
            man = by_path[str(p["path"].relative_to(ROOT))]
            assert man["sha256"] == fh, f"manifest hash mismatch for {p['path']}"
            for r in rows:
                store.append({**r, "release_date": p["release_date"], "release_ts": ts.isoformat(), "time_source": src,
                              "source_file": man["local_path"], "file_hash": fh, "value": repr(r["value"]),
                              "revision_flag": p["flag"]})
            rel.append({"series_root": root, "release_date": p["release_date"], "release_ts": ts.isoformat(),
                        "file": man["local_path"], "revision_flag": p["flag"], "n_values": len(rows)})
    rel.sort(key=lambda r: (r["series_root"], r["release_date"]))
    return store, rel, sel, lst


def write_logs(rel: list[dict], sel: list[dict], lst: list[dict]) -> None:
    for path, fields, rows in ((RELEASES_CSV, REL_FIELDS, rel), (SELECTION_CSV, SEL_FIELDS, sel),
                               (LISTING_CSV, ["ident", "listing_date", "listing_id", "fname", "url"], lst)):
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
