"""ESMIS WASDE listing ingest: full listing (1995+) -> data/listing.csv, dedup, ORIG/v2 flags.

One release = one ESMIS date. Row = one listed file. First-release rule: among the
rows of a date, the earliest listing id's file set is v1; ORIG-tagged files win over
anything; files tagged revision/correction are corrections (v2). A date whose only
file of a format is correction-tagged is `corrected_only`.
"""
import csv
import json
import re
from pathlib import Path

from .fetch import DATA, fetch

API = "https://esmis.nal.usda.gov/api/v1/release/findByIdentifier/wasde?page={}"
LISTING_CSV = DATA / "listing.csv"
CORR_RE = re.compile(r"(_revision|_revisions|_correction|^R1|[_-]R1[._]|reissu)", re.I)
FIELDS = ["release_date", "listing_id", "n_rows_for_date", "is_dup_row", "url", "ext",
          "fname", "is_orig", "is_correction", "esmis_release_datetime"]


def fetch_listing() -> list[dict]:
    first = json.loads(fetch(API.format(0), "wasde_listing", "page_00.json").read_text())
    pages = first["pager"]["total_pages"]
    results = list(first["results"])
    for p in range(1, pages):
        results += json.loads(fetch(API.format(p), "wasde_listing", f"page_{p:02d}.json").read_text())["results"]
    return results


def build_listing(results: list[dict]) -> list[dict]:
    per_date: dict[str, list[dict]] = {}
    for r in results:
        per_date.setdefault(r["release_datetime"][:10], []).append(r)
    rows = []
    for d, rs in sorted(per_date.items()):
        rs.sort(key=lambda r: int(r["id"]))
        for i, r in enumerate(rs):
            for u in r["files"]:
                fn = u.rsplit("/", 1)[1]
                rows.append({
                    "release_date": d, "listing_id": r["id"], "n_rows_for_date": len(rs),
                    "is_dup_row": int(i > 0), "url": u,
                    "ext": fn.rsplit(".", 1)[-1].lower() if "." in fn else "",
                    "fname": fn, "is_orig": int(fn.upper().startswith("ORIG")),
                    "is_correction": int(bool(CORR_RE.search(fn))),
                    "esmis_release_datetime": r["release_datetime"],
                })
    return rows


def write_listing(rows: list[dict]) -> None:
    with LISTING_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_listing() -> list[dict]:
    with LISTING_CSV.open(newline="") as f:
        return list(csv.DictReader(f))


def first_release_files(rows: list[dict], ext: str) -> dict[str, dict]:
    """Per release date: the file of format `ext` that counts as the first release.

    Preference: ORIG-tagged file; else the earliest listing id's non-correction file;
    else (corrected-only) the earliest correction file with flag corrected_only=1.
    Returns {date: {url, fname, listing_id, corrected_only, has_orig_and_later}}.
    """
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        if r["ext"] == ext:
            by_date.setdefault(r["release_date"], []).append(r)
    out = {}
    for d, rs in by_date.items():
        rs = sorted(rs, key=lambda r: (int(r["listing_id"]), r["fname"]))
        orig = [r for r in rs if r["is_orig"]]
        plain = [r for r in rs if not r["is_correction"]]
        pick, corr_only = (orig or plain or rs)[0], 0
        if not (orig or plain):
            corr_only = 1
        out[d] = {"url": pick["url"], "fname": pick["fname"], "listing_id": pick["listing_id"],
                  "corrected_only": corr_only, "n_files": len(rs)}
    return out
