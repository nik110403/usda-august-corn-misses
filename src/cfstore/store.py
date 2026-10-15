"""Build the as-of store (data/store.csv) from the listing + manifest-backed raw XML."""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path

from .fetch import DATA, ROOT, fetch, read_manifest
from .listing import build_listing, fetch_listing, first_release_files, write_listing
from .parse import parse_corn, parse_corn_xls
from .parse_txt import has_corn_block, is_correction_note, parse_corn_txt, parse_header
from .times import release_ts

STORE_CSV = DATA / "store.csv"
SCHEMA = ["series", "reference_period", "release_date", "release_ts", "time_source",
          "source_file", "file_hash", "value", "revision_flag"]
XML_FROM = "2010-07-01"  # WASDE XML era (checks/items-A.md 1, 6)
RELEASES_CSV = DATA / "wasde_releases.csv"
TXT_SELECTION_CSV = DATA / "txt_selection.csv"
TXT_SEL_FIELDS = ["listing_date", "listing_id", "fname", "role", "wasde_no", "header_date", "release_date",
                  "revision_flag", "reason"]
TXT_NAME_RE = re.compile(r"wasde-\d\d-\d\d-\d{4}(_revision)?\.txt$")
TXT_NUMBERS = range(298, 484)  # WASDE-298 (Jan 1995) .. WASDE-483 (Jun 2010): one number per month, contiguous
# listing date -> (header date, reason): the only places where the printed cover date differs from the ESMIS date.
# Original (non-correction) reports take the cover date (the true release; listing lags). Corrected-only files
# take the ESMIS date (the day the corrected vintage became visible; the original is not in ESMIS).
HEADER_DATE_EXCEPTIONS = {
    "1995-02-12": ("1995-02-10", "ESMIS date is a Sunday; cover says WASDE-299 - February 10, 1995"),
    "1995-08-14": ("1995-08-11", "cover says Aug 11; WASDE-304 text announces 'the August 11 issue'"),
    "2000-04-01": ("2000-04-11", "ESMIS date typo; cover says WASDE-361 April 11, 2000"),
    "1996-03-14": ("1996-03-12", "file is a correction of WASDE-312 issued Mar 12 (original not in ESMIS); corrected_only"),
    "2006-03-13": ("2006-03-10", "file name _revision, cover WASDE-432 March 10, 2006 (original not in ESMIS); corrected_only"),
}


def select_txt(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """TXT era (before XML_FROM): fetch every report-named TXT, read its cover (WASDE number + date) and pick
    one primary file per WASDE number. Returns (primaries, selection-log rows). Raises on anything unexpected."""
    seen_urls, cands, log = set(), {}, []
    for r in rows:
        if r["ext"] != "txt" or r["release_date"] >= XML_FROM or r["url"] in seen_urls:
            continue  # repeated listing rows (same file under several ESMIS entries) collapse by URL
        seen_urls.add(r["url"])
        base = {"listing_date": r["release_date"], "listing_id": r["listing_id"], "fname": r["fname"],
                "wasde_no": "", "header_date": "", "release_date": "", "revision_flag": ""}
        if not TXT_NAME_RE.match(r["fname"]):
            log.append({**base, "role": "excluded", "reason": "not a report file name (cotton/news/readme/announcement/special)"})
            continue
        path = fetch(r["url"], f"wasde/{r['release_date']}/{r['listing_id']}", r["fname"])
        text = path.read_bytes().decode("latin-1")
        hdr = parse_header(text)
        if not has_corn_block(text):
            log.append({**base, "wasde_no": hdr[0] if hdr else "", "header_date": hdr[1].isoformat() if hdr else "",
                        "role": "excluded", "reason": "no US corn table (narrative/cotton extract or notice)"})
            continue
        if hdr is None:
            raise ValueError(f"report with corn table but no cover (WASDE number + date): {path}")
        corr = bool(re.search(r"_revision", r["fname"])) or is_correction_note(text)
        cands.setdefault(hdr[0], []).append({**base, "path": path, "text": text, "hdr": hdr, "corr": corr})
    prim = []
    for no, cs in sorted(cands.items()):
        cs.sort(key=lambda c: (c["corr"], c["listing_date"], c["fname"]))
        p = cs[0]
        for c in cs[1:]:
            log.append({k: c[k] for k in ("listing_date", "listing_id", "fname")} | {
                "wasde_no": no, "header_date": c["hdr"][1].isoformat(), "release_date": "", "revision_flag": "",
                "role": "excluded", "reason": f"later/corrected file for WASDE-{no}; primary is {p['fname']} (first, non-correction wins)"})
        ld, hd = p["listing_date"], p["hdr"][1].isoformat()
        if hd != ld:
            if HEADER_DATE_EXCEPTIONS.get(ld, (None,))[0] != hd:
                raise ValueError(f"WASDE-{no}: cover date {hd} != ESMIS date {ld} and not a documented exception")
        rel = ld if p["corr"] else hd
        flag = "v1_corrected_only" if p["corr"] else "v1"
        reason = HEADER_DATE_EXCEPTIONS[ld][1] if hd != ld else "cover date == ESMIS date"
        p.update(release_date=rel, flag=flag)
        prim.append(p)
        log.append({k: p[k] for k in ("listing_date", "listing_id", "fname")} | {
            "wasde_no": no, "header_date": hd, "release_date": rel, "revision_flag": flag, "role": "primary", "reason": reason})
    nums = [p["hdr"][0] for p in prim]
    if nums != list(TXT_NUMBERS):
        raise ValueError(f"WASDE numbers not contiguous {TXT_NUMBERS.start}..{TXT_NUMBERS.stop - 1}: "
                         f"missing {sorted(set(TXT_NUMBERS) - set(nums))}, extra {sorted(set(nums) - set(TXT_NUMBERS))}")
    if [p["release_date"] for p in prim] != sorted(p["release_date"] for p in prim):
        raise ValueError("WASDE numbers and release dates are not in the same order")
    log.sort(key=lambda r: (r["listing_date"], r["fname"]))
    return prim, log


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

    prim_txt, sel_log = select_txt(rows)
    for p in prim_txt:
        d = p["release_date"]
        ts, src = release_ts("wasde", date.fromisoformat(d))
        parsed = parse_corn_txt(p["text"], p["hdr"][1].month)
        src_file = emit(d, ts, src, p["path"], parsed, p["flag"])
        rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": src_file, "revision_flag": p["flag"],
                    "n_values": len(parsed)})

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
            xparsed = parse_corn_xls(str(xp))
            primary = emit(d, ts, src, xp, xparsed, "v1")
            emit(d, ts, src, path, parsed, "v2_repost")
            rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": primary, "revision_flag": "v1",
                        "n_values": len(xparsed)})
            continue
        if d in variant_dates and not f["fname"].upper().startswith("ORIG"):
            flag = "v1_orig_missing_for_xml"
        src_file = emit(d, ts, src, path, parsed, flag)
        rel.append({"release_date": d, "release_ts": ts.isoformat(), "file": src_file, "revision_flag": flag,
                    "n_values": len(parsed)})
    store.sort(key=lambda r: (r["series"], r["reference_period"], r["release_date"], r["revision_flag"]))
    rel.sort(key=lambda r: r["release_date"])
    with TXT_SELECTION_CSV.open("w", newline="") as fo:
        w = csv.DictWriter(fo, TXT_SEL_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sel_log)
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
            "dup_rows": len(results) - len(all_dates), "txt_releases": len(prim_txt),
            "xml_releases_parsed": len(rel) - len(prim_txt),
            "flagged": n_flag, "store_rows": len(store)}


def read_store() -> list[dict]:
    with STORE_CSV.open(newline="") as f:
        return list(csv.DictReader(f))
