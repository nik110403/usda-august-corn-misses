"""Origins, information sets and the leakage guard (STRATEGY 5.1 / 7, PREREG 1).

Rows are dicts with at least: series, release_date (ISO date), release_ts (tz-aware ISO).
"""
from datetime import date, datetime

ORIGIN_MONTHS = range(5, 12)  # May-Nov


class LeakageError(AssertionError):
    pass


def ts(row: dict) -> datetime:
    t = datetime.fromisoformat(row["release_ts"])
    if t.tzinfo is None:
        raise ValueError(f"naive release_ts in {row}")
    return t


def origins(release_rows: list[dict]) -> list[dict]:
    """Regular monthly WASDE of May-Nov: the first WASDE release of each such month.

    Reissues (later releases in the same month) are never origins. release_rows come from
    data/wasde_releases.csv (release_date, release_ts). Months without a release are absent.
    """
    first: dict[tuple[int, int], dict] = {}
    for r in sorted(release_rows, key=lambda r: r["release_date"]):
        d = date.fromisoformat(r["release_date"])
        if d.month in ORIGIN_MONTHS:
            first.setdefault((d.year, d.month), r)
    return list(first.values())


def is_own_release(row: dict, origin: dict) -> bool:
    """The origin's own WASDE, and the Crop Production released jointly with it (same ESMIS date)."""
    return (row["release_date"] == origin["release_date"]
            and row["series"].split(".")[0] in ("wasde", "crop_production"))


def info_set(rows: list[dict], origin: dict) -> list[dict]:
    """Strict rule: release_ts < origin_ts, plus the origin's own release.

    A same-day row of another series therefore enters only when its documented time is
    earlier than the origin's; otherwise it is lagged to the next day (it appears in the
    information set of origins on later days).
    """
    o_ts = ts(origin)
    return [r for r in rows if ts(r) < o_ts or is_own_release(r, origin)]


def date_level_select(rows: list[dict], origin: dict) -> list[dict]:
    """The careless join the guard exists to catch: everything dated on or before the origin date."""
    return [r for r in rows if r["release_date"] <= origin["release_date"]]


def assert_no_leak(features: list[dict], origin: dict) -> None:
    """Leakage test 1: fail if any feature row has release_ts >= origin_ts and is not the origin's own release."""
    o_ts = ts(origin)
    bad = [r for r in features if ts(r) >= o_ts and not is_own_release(r, origin)]
    if bad:
        raise LeakageError(f"{len(bad)} feature row(s) not strictly before origin_ts {o_ts.isoformat()}: {bad[0]}")


def build_features(rows: list[dict], origin: dict, select=info_set) -> list[dict]:
    """Feature builder: select candidate rows, then run the leakage assertion on the result."""
    feats = select(rows, origin)
    assert_no_leak(feats, origin)
    return feats

