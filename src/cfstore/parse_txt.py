"""WASDE TXT (Jan 1995 - Jun 2010) -> US corn rows, same series / reference_period naming as parse.py.

Stdlib only. Layout per checks/items-A.md 1: block starts at the line `CORN :` (followed by `Area :`),
ends at the `====` rule; value columns = two prior marketing years, then (optionally last month's
projection,) then the current-month projection. Any unrecognised layout raises.
"""
import re
from datetime import date

MONTHS = "January February March April May June July August September October November December".split()
DATE_RE = re.compile(r"(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})", re.I)
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
YR_RE = r"(\d{4}/(?:\d{2}|\d{4}))"
ITEM_RE = re.compile(r"Item\s*:\s*" + YR_RE + r"\s*:\s*" + YR_RE + r"\s*:")
LABELS = {  # normalised row label -> series suffix (same suffixes as parse.LABELS)
    "harvested": "area_harvested",
    "beginning stocks": "beginning_stocks",
    "production": "production",
    "imports": "imports",
    "supply, total": "supply_total",
    "exports": "exports",
    "use, total": "total_use",
    "ending stocks, total": "ending_stocks",
}


def parse_header(text: str) -> tuple[int, date] | None:
    """(WASDE number, report date) from the cover: first `WASDE-NNN` and the first `Month D, YYYY`
    within the first 1200 chars. None if the file has no such cover (narrative/cotton extracts)."""
    head = text[:1200]
    n = re.search(r"WASDE\s*-?\s*(\d{3})(?!\d)", text[:6000])
    m = DATE_RE.search(head)
    if not (n and m):
        return None
    return int(n.group(1)), date(int(m.group(3)), MONTHS.index(m.group(1).capitalize()) + 1, int(m.group(2)))


def is_correction_note(text: str) -> bool:
    return re.search(r"THIS IS A CORRECTION OF WASDE-\d+", text[:1500], re.I) is not None


def _ref(y: str) -> str:
    a, b = y.split("/")
    return f"{a}/{b[-2:]}"


def _num(tok: str) -> float:
    s = tok.replace(",", "").replace("*", "").strip()
    if not NUM_RE.match(s):
        raise ValueError(f"unparseable value {tok!r}")
    return float(s)


def _corn_starts(lines: list[str]) -> list[int]:
    return [i for i, ln in enumerate(lines)
            if re.fullmatch(r"CORN\s*:\s*", ln) and any(re.match(r"Area\s*:", x) for x in lines[i + 1:i + 3])]


def has_corn_block(text: str) -> bool:
    return bool(_corn_starts(text.replace("\x0c", "\n").splitlines()))


def parse_corn_txt(text: str, report_month: int) -> list[dict]:
    """Return [{series, reference_period, value}] for the current-month column of every market year."""
    lines = text.replace("\x0c", "\n").splitlines()
    starts = _corn_starts(lines)
    if len(starts) != 1:
        raise ValueError(f"expected exactly one US corn block, found {len(starts)}")
    s = starts[0]
    item = next((i for i in range(s - 1, max(s - 60, 0), -1) if ITEM_RE.search(lines[i])), None)
    if item is None:
        raise ValueError("no `Item : yr : yr :` header above the corn block")
    y1, y2 = ITEM_RE.search(lines[item]).groups()
    proj = next((re.search(YR_RE + r"\s+Projections", lines[i]) for i in range(item, item - 4, -1)
                 if re.search(YR_RE + r"\s+Projections", lines[i])), None)
    if proj is None:
        raise ValueError("no `YYYY/YY Projections` label above the corn block header")
    months = []
    for ln in lines[item + 1:item + 4]:  # month line sits between Item line and the closing rule
        if ln.lstrip().startswith("==="):
            continue
        months = [t for t in re.split(r"[\s:]+", ln) if t in MONTHS]
        if months:
            break
    if not months:
        raise ValueError("no forecast-month line in the corn block header")
    if months[-1] != MONTHS[report_month - 1]:
        raise ValueError(f"column order: last column month {months[-1]!r} != report month {MONTHS[report_month - 1]!r}")
    refs = [_ref(y1), _ref(y2), _ref(proj.group(1))]
    ncol = 2 + len(months)
    out, seen, pending_yield, in_area = [], set(), False, False
    for ln in lines[s + 1:]:
        if ln.startswith("==="):
            break
        if ":" not in ln:
            continue
        label, rest = ln.split(":", 1)
        lab = " ".join(label.lower().split())
        if lab == "area":
            in_area = True
            continue
        if lab.startswith("yield per harvested"):
            pending_yield = True
            continue
        if pending_yield and lab == "acre":
            key, pending_yield = "yield", False
        elif lab == "harvested" and not in_area:
            raise ValueError("`Harvested` outside the Area block")
        else:
            key = LABELS.get(lab)
        if key is None:
            continue
        if key in seen:
            raise ValueError(f"duplicate corn line {key!r}")
        seen.add(key)
        toks = [t for t in rest.replace("*", " ").split()]
        if len(toks) != ncol:
            raise ValueError(f"{key}: {len(toks)} value tokens, expected {ncol} ({rest.strip()!r})")
        vals = [_num(toks[0]), _num(toks[1]), _num(toks[-1])]
        for ref, v in zip(refs, vals):
            out.append({"series": f"wasde.us_corn.{key}", "reference_period": ref, "value": v})
    expect = set(LABELS.values()) | {"yield"}
    if seen != expect:
        raise ValueError(f"missing corn lines: {expect - seen}")
    return out
