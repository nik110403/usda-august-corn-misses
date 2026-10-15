"""Origins, information sets and the leakage guard (STRATEGY 5.1 / 7, PREREG 1).

Rows are dicts with at least: series, release_date (ISO date), release_ts (tz-aware ISO).
"""
from datetime import date, datetime

from .times import release_ts

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
    """The origin's own WASDE (its first-release file only, never a logged v2 re-post), and the
    Crop Production released jointly with it (same ESMIS date)."""
    root = row["series"].split(".")[0]
    if row["release_date"] != origin["release_date"]:
        return False
    if root == "wasde":
        return row.get("source_file") == origin.get("file") and row.get("revision_flag") != "v2_repost"
    return root == "crop_production"


def _guard_exempt(row: dict, origin: dict) -> bool:
    """The guard's own, independent own-release exemption (does not call is_own_release): same ESMIS date AND
    the same release_ts as the origin, plus the first-release-file / joint-Crop-Production rules."""
    if row["release_date"] != origin["release_date"] or ts(row) != ts(origin):
        return False
    root = row["series"].split(".")[0]
    if root == "wasde":
        return row.get("source_file") == origin.get("file") and row.get("revision_flag") != "v2_repost"
    return root == "crop_production"


def info_set(rows: list[dict], origin: dict) -> list[dict]:
    """PREREG 1 (release_date = the report's cover date, which equals the ESMIS date except for the logged
    exceptions in data/txt_selection.csv; other series use the ESMIS date): a row that is not the origin's own
    release is admitted only if its release_date is strictly earlier than the origin's date AND
    release_ts < origin_ts. Any other series released on the
    origin's date counts as released the next day. Logged v2 re-posts are sensitivity-only: excluded."""
    o_ts = ts(origin)
    return [r for r in rows if r.get("revision_flag") != "v2_repost"
            and (is_own_release(r, origin) or (r["release_date"] < origin["release_date"] and ts(r) < o_ts))]


def date_level_select(rows: list[dict], origin: dict) -> list[dict]:
    """The careless join the guard exists to catch: everything dated on or before the origin date."""
    return [r for r in rows if r["release_date"] <= origin["release_date"]]


def assert_no_leak(features: list[dict], origin: dict) -> None:
    """Leakage test 1, independent of the selector. Fails if any feature row (a) carries a release_ts that
    differs from the release-time table for its series and ESMIS date, (b) is a v2 re-post, or (c) is not the
    origin's own release and is dated on/after the origin date or has release_ts >= origin_ts."""
    o_ts = ts(origin)
    for r in features:
        try:
            expect, _ = release_ts(r["series"].split(".")[0], date.fromisoformat(r["release_date"]))
        except LookupError as e:
            raise LeakageError(f"no release time for row {r}: {e}") from e
        if ts(r) != expect:
            raise LeakageError(f"release_ts {r['release_ts']} != table time {expect.isoformat()}: {r}")
        if r.get("revision_flag") == "v2_repost":
            raise LeakageError(f"v2 re-post in primary feature set: {r}")
        if _guard_exempt(r, origin):
            continue
        if r["release_date"] >= origin["release_date"] or ts(r) >= o_ts:
            raise LeakageError(f"non-own row not strictly before origin ({o_ts.isoformat()}): {r}")


def build_features(rows: list[dict], origin: dict, select=info_set) -> list[dict]:
    """Feature builder: select candidate rows, then run the leakage assertion on the result."""
    feats = select(rows, origin)
    assert_no_leak(feats, origin)
    return feats

