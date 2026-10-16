"""WASDE `World Wheat Supply and Use` -> the `Russia` row's production column (million metric tons), every marketing year printed.

Stdlib (+ xlrd for the one ORIG .xls origin). Three layouts, all addressed by label, never by position of Russia in the table:
TXT (Jan 1995 - Jun 2010): per marketing year a block headed `: YYYY/YY [(Estimated)|(Projected)]`; the Russia row is
`Russia  :` (single value row) or, in the projection block, `Russia  Mon :` followed by `Mon :` continuation rows (one per
forecast month; the LAST row is the current month). XML (Jul 2010 on): sr18 (matrix1 = prior year, matrix2 = estimate year)
and sr19 (projection, one m1_month_group per month; the last is the current month). XLS (2013-07 ORIG): same pages as XML.
The last value row of a group must carry the report month or the parse raises (column-order guard, as parse.py / parse_txt.py).
Production is the second of the 7 value columns (beginning stocks, production, imports, feed, total use, exports, ending stocks).
"""
import re
import xml.etree.ElementTree as ET

from .parse_txt import MONTHS, NUM_RE

MY_RE = re.compile(r"(\d{4})/(\d{2})")
TXT_HDR_RE = re.compile(r"^\s*:\s+(\d{4}/\d{2})(?:\s*\(.*\))?\s*$")
WHEAT_TITLE_RE = re.compile(r"World Wheat Supply (?:and|&) Use(?!\s*\.)")
OTHER_TITLE_RE = re.compile(r"^\s*World (?!Wheat)[A-Za-z ,]*Supply (?:and|&) Use")
ROW_RE = re.compile(r"^\s*Russia\b[^:]*:(.*)$")
MON_ROW_RE = re.compile(r"^\s*([A-Z][a-z]+)\.?\s*:(.*)$")


def _mon_ok(tag: str, report_month: int) -> bool:
    return MONTHS[report_month - 1].lower().startswith(tag[:3].lower())


def _num(tok: str) -> float:
    s = tok.replace(",", "").replace("*", "").strip()
    if not NUM_RE.match(s):
        raise ValueError(f"unparseable value {tok!r}")
    return float(s)


def _production(rest: str) -> float:
    toks = rest.replace("*", " ").split()
    if len(toks) != 7:
        raise ValueError(f"Russia row has {len(toks)} value tokens, expected 7: {rest.strip()!r}")
    return _num(toks[1])


def parse_russia_txt(text: str, report_month: int) -> list[dict]:
    """[{reference_period, value, line}] for every marketing year with a Russia row in the wheat table ([] if the file has no
    wheat table, e.g. the abbreviated Oct 2008 reissue). `line` is the 1-based line number of the value row used."""
    lines = text.replace("\x0c", "\n").splitlines()
    start = [i for i, ln in enumerate(lines) if WHEAT_TITLE_RE.search(ln) and not re.search(r"\.\s*\.\s*\.", ln)]
    if not start:
        return []
    out, my, i = [], None, start[0]
    while i < len(lines):
        ln = lines[i]
        if i > start[-1] and OTHER_TITLE_RE.match(ln):
            break
        if i > start[0] and OTHER_TITLE_RE.match(ln) and not WHEAT_TITLE_RE.search(ln):
            break
        m = TXT_HDR_RE.match(ln)
        if m:
            my = m.group(1)
        m = ROW_RE.match(ln)
        if m and ln.lstrip().startswith("Russia"):
            if my is None:
                raise ValueError("Russia row before any marketing-year header")
            lab = ln.split(":", 1)[0].split()
            tag = lab[1] if len(lab) > 1 and lab[1][0].isalpha() and not re.fullmatch(r"\d+/", lab[1]) else None
            row_ln, rest = i, m.group(1)
            while tag is not None:  # month sub-rows: take the last one
                nxt = MON_ROW_RE.match(lines[row_ln + 1]) if row_ln + 1 < len(lines) else None
                if not nxt or not nxt.group(2).split():
                    break
                row_ln, rest, tag = row_ln + 1, nxt.group(2), nxt.group(1)
            if tag is not None and not _mon_ok(tag, report_month):
                raise ValueError(f"column order: last Russia row month {tag!r} != report month {MONTHS[report_month - 1]!r}")
            out.append({"reference_period": my, "value": _production(rest), "line": row_ln + 1})
            i = row_ln
        i += 1
    return out


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def _num_or_none(txt: str | None) -> float | None:
    s = (txt or "").replace(",", "").replace("*", "").strip()
    return float(s) if NUM_RE.match(s) else None


def parse_russia_xml(xml_bytes: bytes, report_month: int) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    for e in root.iter():
        e.tag = e.tag.rsplit("}", 1)[-1]
    out = []
    for sr in ("sr18", "sr19"):
        rep = root.find(f"{sr}/Report")
        if rep is None or not _clean(rep.get("sub_report_title")).lower().startswith("world wheat supply"):
            raise ValueError(f"{sr} is not the world wheat table: {None if rep is None else rep.get('sub_report_title')!r}")
        for mx in rep:
            n = mx.tag[len("matrix"):]
            if not mx.tag.startswith("matrix") or not n.isdigit():
                continue
            my = MY_RE.search(mx.get(f"region_header{n}", ""))
            if not my:
                raise ValueError(f"{sr}/{mx.tag}: no marketing year in region_header{n}")
            for g in mx.iter(f"m{n}_region_group"):
                if not _clean(g.get(f"region{n}")).startswith("Russia"):
                    continue
                prod = [a for a in g.iter(f"m{n}_attribute_group") if _clean(a.get(f"attribute{n}")) == "Production"]
                mg = [m for m in g.iter(f"m{n}_month_group")]
                if mg:  # projection: one month group per forecast month, last = current month
                    last = mg[-1]
                    if not _mon_ok(last.get(f"forecast_month{n}", ""), report_month):
                        raise ValueError(f"column order: last Russia month {last.get(f'forecast_month{n}')!r} != report month {report_month}")
                    prod = [a for a in last.iter(f"m{n}_attribute_group") if _clean(a.get(f"attribute{n}")) == "Production"]
                if len(prod) != 1:
                    raise ValueError(f"{sr}/{mx.tag}: {len(prod)} Russia Production cells")
                cell = prod[0].find(".//Cell")
                v = _num_or_none(cell.get(f"cell_value{n}") if cell is not None else None)
                if v is not None:
                    out.append({"reference_period": f"{my.group(1)}/{my.group(2)}", "value": v, "line": ""})
    return out


def parse_russia_xls(path: str, report_month: int) -> list[dict]:
    import xlrd
    book = xlrd.open_workbook(path)
    out = []
    for name in ("Page 18", "Page 19"):
        sh = book.sheet_by_name(name)
        my, r = None, 0
        while r < sh.nrows:
            row = [str(v).strip() for v in sh.row_values(r)]
            nz = [v for v in row if v]
            if nz and MY_RE.match(nz[0]) and len(nz) > 1 and nz[1].startswith("Beginning"):
                my = MY_RE.match(nz[0]).group(0)
            elif nz and nz[0] == "Russia":
                if my is None:
                    raise ValueError("Russia row before any marketing-year header")
                vals, tag, last = nz[1:], None, r
                if vals and vals[0][0].isalpha():
                    tag, vals = vals[0], vals[1:]
                    while r + 1 < sh.nrows:  # month continuation rows: first cell is the month tag
                        nxt = [str(v).strip() for v in sh.row_values(r + 1) if str(v).strip()]
                        if len(nxt) == 8 and nxt[0][0].isalpha():
                            tag, vals, r = nxt[0], nxt[1:], r + 1
                        else:
                            break
                    if not _mon_ok(tag, report_month):
                        raise ValueError(f"column order: last Russia month {tag!r} != report month {report_month}")
                if len(vals) != 7:
                    raise ValueError(f"xls Russia row has {len(vals)} values")
                out.append({"reference_period": my, "value": float(vals[1]), "line": f"{name}!row{r + 1}"})
            r += 1
    return out
