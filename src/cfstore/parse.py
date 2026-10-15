"""WASDE XML (Jul 2010+) -> US corn rows. Corn = sr12/matrix2 by position (checks/items-A.md 6)."""
import re
import xml.etree.ElementTree as ET

LABELS = {  # normalised attribute2 text -> series suffix
    "area harvested": "area_harvested",
    "yield per harvested acre": "yield",
    "beginning stocks": "beginning_stocks",
    "production": "production",
    "imports": "imports",
    "supply, total": "supply_total",  # extra lines for the WASDE identity check (tests/test_validation.py)
    "exports": "exports",
    "use, total": "total_use",
    "ending stocks": "ending_stocks",
}
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _num(txt: str | None) -> float | None:
    s = (txt or "").replace(",", "").replace("*", "").strip()
    return float(s) if NUM_RE.match(s) else None


def wasde_number(xml_bytes: bytes) -> int:
    """WASDE issue number from the first sub-report's page_title (`WASDE - 584 - 8`)."""
    m = re.search(rb'page_title="\s*WASDE\s*-\s*(\d+)', xml_bytes)
    if not m:
        raise ValueError("no WASDE number in XML page_title")
    return int(m.group(1))


def parse_corn(xml_bytes: bytes, prev: bool = False) -> list[dict]:
    """Return [{series, reference_period, value}] for the current-month column of every market year."""
    root = ET.fromstring(xml_bytes)
    for e in root.iter():  # Jul-Sep 2010 files carry a default namespace
        e.tag = e.tag.rsplit("}", 1)[-1]
    rep = root.find("sr12/Report")
    if rep is None or "corn" not in rep.get("sub_report_title", "").lower():
        raise ValueError("sr12 is not the corn table: %r" % (rep.get("sub_report_title") if rep is not None else None))
    rep_month = rep.get("Report_Month", "").strip().lower()
    out, seen = [], set()
    for grp in rep.find("matrix2/m2_attribute_group_Collection"):
        att = grp.find("attribute2")
        if att is None:
            continue
        key = LABELS.get(" ".join(att.get("attribute2", "").lower().split()))
        if key is None or key in seen:
            continue
        seen.add(key)
        last: dict[str, tuple[float | None, str]] = {}
        cols: dict[str, list[float | None]] = {}
        for yg in att.iter("m2_year_group"):
            cell = yg.find(".//Cell")
            mg = yg.find(".//m2_month_group")
            mo = (mg.get("forecast_month2") if mg is not None else "") or ""
            ref = yg.get("market_year2", "").split()[0]
            if cell is not None:  # columns are in order; the last one of a year is the current month
                last[ref] = (_num(cell.get("cell_value2")), mo)
                cols.setdefault(ref, []).append(last[ref][0])
        if prev:  # cross-vintage check only: the previous-month column of years that carry one
            out += [{"series": f"wasde.us_corn.{key}", "reference_period": ref, "value": c[-2]}
                    for ref, c in cols.items() if len(c) >= 2 and c[-2] is not None]
            continue
        for ref, (v, mo) in last.items():
            if mo and not rep_month.startswith(mo[:3].lower()):
                raise ValueError(f"column order: last column month {mo!r} != report month {rep_month!r}")
            if v is not None:
                out.append({"series": f"wasde.us_corn.{key}", "reference_period": ref, "value": v})
    if seen != set(LABELS.values()):
        raise ValueError(f"missing corn lines: {set(LABELS.values()) - seen}")
    return out


def parse_corn_xls(path: str) -> list[dict]:
    """ORIG .xls (2013-era layout): sheet with a 'CORN' header cell followed by market-year cells.

    The last column of a market year is the current month; checked against the sheet's month label.
    """
    import xlrd
    book = xlrd.open_workbook(path)
    for sh in book.sheets():
        for hdr in range(sh.nrows):
            for lc in range(sh.ncols):
                if str(sh.cell_value(hdr, lc)).strip() == "CORN" and lc + 1 < sh.ncols \
                        and re.match(r"^\d{4}/\d{2}", str(sh.cell_value(hdr, lc + 1))):
                    return _xls_table(sh, hdr, lc)
    raise ValueError("no CORN table in xls")


def _xls_table(sh, hdr: int, lc: int) -> list[dict]:
    last_col = {str(v).split()[0]: c for c, v in enumerate(sh.row_values(hdr))
                if c > lc and re.match(r"^\d{4}/\d{2}", str(v))}  # later column of a year overwrites earlier
    rep_month = " ".join(str(v) for v in sh.row_values(1) if v != "").strip().lower()  # e.g. "july 2013"
    cur = [str(v).strip() for v in sh.row_values(hdr + 1) if str(v).strip()][-1]
    if not rep_month.startswith(cur[:3].lower()):
        raise ValueError(f"column order: last month {cur!r} != report month {rep_month!r}")
    out, seen = [], set()
    for r in range(hdr + 2, sh.nrows):
        key = LABELS.get(" ".join(str(sh.cell_value(r, lc)).lower().split()))
        if key is None or key in seen:
            continue
        seen.add(key)
        for ref, c in last_col.items():
            v = _num(str(sh.cell_value(r, c)))
            if v is not None:
                out.append({"series": f"wasde.us_corn.{key}", "reference_period": ref, "value": v})
    if seen != set(LABELS.values()):
        raise ValueError(f"missing corn lines: {set(LABELS.values()) - seen}")
    return out
