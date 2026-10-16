"""Polite, cached, manifest-backed fetching from USDA ESMIS (keyless).

Every fetch goes to a fresh path under data/raw/ and is logged in data/manifest.csv
(url, retrieval_utc, sha256, bytes, local_path). A URL already in the manifest whose
local file exists is never refetched.
"""
import csv
import hashlib
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
MANIFEST = DATA / "manifest.csv"
FIELDS = ["url", "retrieval_utc", "sha256", "bytes", "local_path"]
DELAY_S = 0.5
UA = "usda-august-corn-misses/0.1 (nik@khosravipour.at)"
_last = [0.0]


def read_manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="") as f:
        return {r["url"]: r for r in csv.DictReader(f)}


def _append(row: dict) -> None:
    new = not MANIFEST.exists()
    with MANIFEST.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)


def fetch(url: str, rel_dir: str, name: str) -> Path:
    """Return the local path of url, downloading into data/raw/<rel_dir>/<name> if not in manifest."""
    m = read_manifest()
    if url in m and (ROOT / m[url]["local_path"]).exists():
        return ROOT / m[url]["local_path"]
    wait = DELAY_S - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    _last[0] = time.monotonic()
    dest = RAW / rel_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)  # each fetch goes into a fresh location
    dest.write_bytes(body)
    _append({
        "url": url,
        "retrieval_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "local_path": str(dest.relative_to(ROOT)),
    })
    return dest
