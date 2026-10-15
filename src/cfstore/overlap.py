"""Leakage test 4 / FEASIBILITY item 5b: how many May-Nov WASDE origins fall on a Crop Progress release date.

Origins come from the WASDE release log, Crop Progress release dates from the NASS release log (data/nass_releases.csv:
every Crop Progress release, including the April-May reports that carry no corn condition line). Writes
out/crop_progress_overlap.csv (one row per origin) and out/crop_progress_overlap_count.txt.
"""
import csv
from datetime import date

from .asof import origins
from .fetch import DATA, ROOT

OUT = ROOT / "out"


def crop_progress_dates() -> set[str]:
    with (DATA / "nass_releases.csv").open(newline="") as f:
        return {r["release_date"] for r in csv.DictReader(f) if r["series_root"] == "crop_progress"}


def overlap_rows() -> list[dict]:
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        rels = list(csv.DictReader(f))
    cp = crop_progress_dates()
    return [{"origin_date": o["release_date"], "weekday": date.fromisoformat(o["release_date"]).strftime("%a"),
             "revision_flag": o["revision_flag"], "on_crop_progress_day": int(o["release_date"] in cp)}
            for o in origins(rels)]


def summary(rows: list[dict]) -> dict:
    out = {}
    for label, sel in (("1995-2025", lambda r: r["origin_date"] < "2026"), ("1995-present", lambda r: True)):
        sub = [r for r in rows if sel(r)]
        hits = [r for r in sub if r["on_crop_progress_day"]]
        out[label] = {"origins": len(sub), "overlap": len(hits),
                      "Mon": sum(r["weekday"] == "Mon" for r in hits), "Tue": sum(r["weekday"] == "Tue" for r in hits)}
    return out


def write() -> dict:
    rows = overlap_rows()
    OUT.mkdir(exist_ok=True)
    with (OUT / "crop_progress_overlap.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    s = summary(rows)
    with (OUT / "crop_progress_overlap_count.txt").open("w") as f:
        for k, v in s.items():
            f.write(f"{k}: {v['overlap']} of {v['origins']} May-Nov WASDE origins fall on a Crop Progress release day "
                    f"({v['Mon']} Mon, {v['Tue']} Tue)\n")
    return s
