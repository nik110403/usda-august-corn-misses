"""Build the as-of store (data/store.csv) from the listing + manifest-backed raw XML."""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path

from .fetch import DATA, ROOT, fetch, read_manifest
from .listing import build_listing, fetch_listing, first_release_files, write_listing
from .parse import parse_corn
from .times import release_ts

STORE_CSV = DATA / "store.csv"
SCHEMA = ["series", "reference_period", "release_date", "release_ts", "time_source",
          "source_file", "file_hash", "value", "revision_flag"]
XML_FROM = "2010-07-01"  # WASDE XML era (checks/items-A.md 1, 6)
RELEASES_CSV = DATA / "wasde_releases.csv"


def build_store() -> dict:
    results = fetch_listing()
    rows = build_listing(results)
    write_listing(rows)
    all_dates = {r["release_date"] for r in rows}
    xmls = {d: v for d, v in first_release_files(rows, "xml").items() if d >= XML_FROM}
    # ORIG/correction variants of the same date in other formats mean the unlabelled XML may be post-correction
    variant_dates = {r["release_date"] for r in rows
                     if r["is_orig"] or re.match(r"R1wasde-\d|wasde-\d\d-\d\d-\d{4}_revision\.", r["fname"])}
    store, rel = [], []
    for d, f in sorted(xmls.items()):
        path = fetch(f["url"], f"wasde/{d}/{f['listing_id']}", f["fname"])
        body = path.read_bytes()
        flag = "v1"
        if f["corrected_only"]:
            flag = "v1_corrected_only"
        elif d in variant_dates and not f["fname"].upper().startswith("ORIG"):
            flag = "v1_orig_missing_for_xml"
        ts, src = release_ts("wasde", date.fromisoformat(d))
        fh = hashlib.sha256(body).hexdigest()
        man = read_manifest()[f["url"]]
        assert man["sha256"] == fh, f"manifest hash mismatch for {path}"
        parsed = parse_corn(body)
        rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": man["local_path"], "revision_flag": flag,
                    "n_values": len(parsed)})
        for p in parsed:
            store.append({**p, "release_date": d, "release_ts": ts.isoformat(), "time_source": src,
                          "source_file": man["local_path"], "file_hash": fh, "value": repr(p["value"]),
                          "revision_flag": flag})
    store.sort(key=lambda r: (r["series"], r["reference_period"], r["release_date"]))
    with STORE_CSV.open("w", newline="") as fo:
        w = csv.DictWriter(fo, SCHEMA, lineterminator="\n")
        w.writeheader()
        w.writerows([{k: r[k] for k in SCHEMA} for r in store])
    with RELEASES_CSV.open("w", newline="") as fo:
        w = csv.DictWriter(fo, list(rel[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rel)
    n_flag = sum(1 for r in rel if r["revision_flag"] != "v1")
    return {"listing_rows_raw": len(results), "listing_dates": len(all_dates),
            "dup_rows": len(results) - len(all_dates), "xml_releases_parsed": len(rel),
            "flagged": n_flag, "store_rows": len(store)}


def read_store() -> list[dict]:
    with STORE_CSV.open(newline="") as f:
        return list(csv.DictReader(f))
