"""NASS Crop Progress TXT (ESMIS `CropProg-*` / `progNNNN`) -> national corn condition (Good + Excellent, percent).

Stdlib only. Two layouts: (A) the old fixed-width tables `Corn:  Crop Condition by Percent, Selected States` with
columns VP/P/F/G/EX and a national row `17 Sts` / `18 Sts` (often side by side with soybeans or another crop), and
(B) the modern `Corn Condition - Selected States: Week Ending ...` with a national row `18 States ......`.
A report with no corn condition table (April-May) returns None; a table that is present but unreadable raises.
"""
import re
from datetime import date, timedelta

from .parse_txt import DATE_RE, MONTHS

TOK = r"(\d+|-|\(NA\))"
ROW5 = r"\s*" + r"\s+".join([TOK] * 5)
NATIONAL_A = re.compile(r"^\s*\d+\s+Sts?\s*:" + ROW5)
NATIONAL_B = re.compile(r"^\s*\d+\s+States\s*\.*\s*:" + ROW5)
TITLE_A = re.compile(r"Corn:\s+Crop Condition")
TITLE_B = re.compile(r"^Corn Condition - Selected States", re.M)
RELEASED_RE = re.compile(r"Released\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})", re.I)
WEEK_B_RE = re.compile(r"Week\s+Ending\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})", re.I)
MON3 = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}
WEEK_A_RE = re.compile(r"State\s*:\s*([A-Z][a-z]{2})\.?\s+(\d{1,2}),")


def _int(tok: str) -> int:
    if tok == "-":
        return 0
    if tok == "(NA)":
        raise ValueError("national corn condition row has (NA)")
    return int(tok)


def released_date(text: str) -> date:
    m = RELEASED_RE.search(text[:2500])
    if not m:
        raise ValueError("no 'Released <date>' line in the report head")
    return date(int(m.group(3)), MONTHS.index(m.group(1).capitalize()) + 1, int(m.group(2)))


def _row(lines: list[str], rx: re.Pattern, half) -> tuple[int, list[int], list[int] | None]:
    """First national row after the title (half-line slice for side-by-side tables); also the next 'Prev Wk' row."""
    for k, ln in enumerate(lines):
        m = rx.match(half(ln))
        if m:
            cur = [_int(t) for t in m.groups()]
            prev = None
            for ln2 in lines[k + 1:k + 4]:
                m2 = re.match(r"^\s*Prev(?:ious)?\s+[Ww](?:ee)?k\s*\.*\s*:" + ROW5, half(ln2))
                if m2:
                    try:
                        prev = [_int(t) for t in m2.groups()]
                    except ValueError:
                        prev = None
                    break
            return k, cur, prev
    raise ValueError("corn condition title found but no national row")


def parse_progress(text: str) -> dict | None:
    """{'vp','p','f','g','ex','good_excellent', 'prev_good_excellent' (or None), 'week_ending' (date or None)}
    for the national corn condition line, or None if the report carries no corn condition table."""
    lines = text.replace("\x0c", "\n").splitlines()
    cur = prev = None
    for i, ln in enumerate(lines):
        m = TITLE_A.search(ln)
        if m:
            half = (lambda s: s[:41]) if m.start() < 20 else (lambda s: s[41:])
            _, cur, prev = _row(lines[i + 1:i + 45], NATIONAL_A, half)
            break
        if TITLE_B.match(ln):
            _, cur, prev = _row(lines[i + 1:i + 45], NATIONAL_B, lambda s: s)
            break
    if cur is None:
        if re.search(r"Corn[^\n]{0,12}Crop Condition|Corn[^\n]{0,12}Condition\s*-", text):  # the words are there but no recognised table
            raise ValueError("unrecognised corn condition layout")
        return None
    if not 98 <= sum(cur) <= 102:
        raise ValueError(f"corn condition row does not sum to ~100: {cur}")
    out = dict(zip(("vp", "p", "f", "g", "ex"), cur))
    out["good_excellent"] = out["g"] + out["ex"]
    out["prev_good_excellent"] = prev[3] + prev[4] if prev else None
    out["week_ending"] = week_ending(text)
    return out


def week_ending(text: str) -> date | None:
    """Week-ending date as printed (modern `Week Ending <Month D, YYYY>`; old `State:Aug 4, :Jul 28,` + year line)."""
    m = WEEK_B_RE.search(text[:20000])
    if m:
        return date(int(m.group(3)), MONTHS.index(m.group(1).capitalize()) + 1, int(m.group(2)))
    m = WEEK_A_RE.search(text[:20000])
    if m:
        y = re.search(r"^\s*:\s*(\d{4})\s*:", text[m.end():m.end() + 400], re.M)
        if y:
            return date(int(y.group(1)), MON3[m.group(1)], int(m.group(2)))
    m = re.search(r"Week\s+ending\s*:?\s*[\s\S]{0,80}?\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})", text[:20000], re.I)
    if m:
        return date(int(m.group(3)), MONTHS.index(m.group(1).capitalize()) + 1, int(m.group(2)))
    return None


def sunday_before(release: date) -> date:
    """Week ending (Sunday) of the report released on `release`: Monday -> previous day, Tuesday (after a Monday
    holiday) -> two days before; a release on a Sunday is its own week ending."""
    return release - timedelta(days=(release.weekday() + 1) % 7)
