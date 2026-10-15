"""WASDE XML (Jul 2010+) -> US corn rows. Corn = sr12/matrix2 by position (checks/items-A.md 6)."""
import re
import xml.etree.ElementTree as ET

LABELS = {  # normalised attribute2 text -> series suffix
    "area harvested": "area_harvested",
    "yield per harvested acre": "yield",
    "beginning stocks": "beginning_stocks",
    "production": "production",
    "imports": "imports",
    "use, total": "total_use",
    "ending stocks": "ending_stocks",
}
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _num(txt: str | None) -> float | None:
    s = (txt or "").replace(",", "").replace("*", "").strip()
    return float(s) if NUM_RE.match(s) else None


def parse_corn(xml_bytes: bytes) -> list[dict]:
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
        for yg in att.iter("m2_year_group"):
            cell = yg.find(".//Cell")
            mg = yg.find(".//m2_month_group")
            mo = (mg.get("forecast_month2") if mg is not None else "") or ""
            ref = yg.get("market_year2", "").split()[0]
            if cell is not None:  # columns are in order; the last one of a year is the current month
                last[ref] = (_num(cell.get("cell_value2")), mo)
        for ref, (v, mo) in last.items():
            if mo and not rep_month.startswith(mo[:3].lower()):
                raise ValueError(f"column order: last column month {mo!r} != report month {rep_month!r}")
            if v is not None:
                out.append({"series": f"wasde.us_corn.{key}", "reference_period": ref, "value": v})
    if seen != set(LABELS.values()):
        raise ValueError(f"missing corn lines: {set(LABELS.values()) - seen}")
    return out
