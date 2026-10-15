"""Build the as-of store (data/store.csv) from the listing + manifest-backed raw XML."""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path

from .fetch import DATA, ROOT, fetch, read_manifest
from .listing import build_listing, fetch_listing, first_release_files, write_listing
from .parse import parse_corn, parse_corn_xls
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
    orig_xls = {r["release_date"]: r for r in rows if r["is_orig"] and r["ext"] == "xls"}
    store, rel = [], []

    def emit(d, ts, src, path, parsed, flag):
        body = path.read_bytes()
        fh = hashlib.sha256(body).hexdigest()
        man = next(m for m in read_manifest().values() if m["local_path"] == str(path.relative_to(ROOT)))
        assert man["sha256"] == fh, f"manifest hash mismatch for {path}"
        for p in parsed:
            store.append({**p, "release_date": d, "release_ts": ts.isoformat(), "time_source": src,
                          "source_file": man["local_path"], "file_hash": fh, "value": repr(p["value"]),
                          "revision_flag": flag})
        return man["local_path"]

    for d, f in sorted(xmls.items()):
        path = fetch(f["url"], f"wasde/{d}/{f['listing_id']}", f["fname"])
        flag = "v1"
        if f["corrected_only"]:
            flag = "v1_corrected_only"
        ts, src = release_ts("wasde", date.fromisoformat(d))
        parsed = parse_corn(path.read_bytes())
        if d in variant_dates and not f["fname"].upper().startswith("ORIG") and d in orig_xls:
            # ORIG xls is the first release; the unlabelled XML is a post-correction v2 re-post (sensitivity only)
            o = orig_xls[d]
            xp = fetch(o["url"], f"wasde/{d}/{o['listing_id']}_xls", o["fname"])
            primary = emit(d, ts, src, xp, parse_corn_xls(str(xp)), "v1")
            emit(d, ts, src, path, parsed, "v2_repost")
            rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": primary, "revision_flag": "v1",
                        "n_values": len(parsed)})
            continue
        if d in variant_dates and not f["fname"].upper().startswith("ORIG"):
            flag = "v1_orig_missing_for_xml"
        src_file = emit(d, ts, src, path, parsed, flag)
        rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": src_file, "revision_flag": flag,
                    "n_values": len(parsed)})
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
