"""Release-time table (data/release_times.csv) -> timezone-aware release_ts.

The ESMIS 12:00 listing time is a placeholder and is never used. For WASDE the date is the report's cover
date (day_rule `cover_date`): it equals the ESMIS date except for the exceptions logged in data/txt_selection.csv
(1995-02-10, 1995-08-11, 2000-04-11 take the cover date; corrected-only files keep the ESMIS date).
"""
import csv
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .fetch import DATA

_TABLE: list[dict] | None = None


def table() -> list[dict]:
    global _TABLE
    if _TABLE is None:
        with (DATA / "release_times.csv").open(newline="") as f:
            _TABLE = list(csv.DictReader(f))
    return _TABLE


def release_ts(series: str, d: date) -> tuple[datetime, str]:
    """Return (tz-aware release datetime, time_source) for `series` released on ESMIS date d."""
    hits = [r for r in table() if r["series"] == series
            and date.fromisoformat(r["valid_from"]) <= d <= date.fromisoformat(r["valid_to"])]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} release-time rows for {series} on {d}")
    r = hits[0]
    hh, mm = map(int, r["local_time"].split(":"))
    ts = datetime.combine(d, time(hh, mm), tzinfo=ZoneInfo(r["tz"]))
    return ts, f"release_times.csv:{r['series']}:{r['valid_from']}"
