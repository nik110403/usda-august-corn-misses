"""F7b number trace: every numeric token in out/memo.md, out/writeup.md and README.md must appear (after normalisation) in a
generated file (out/eval, out/russia, out/memo, two out/*.txt) or in PREREG*.md, or be allowed (1-10, years 1990-2030,
docs/trace_allow.txt). Table rows ('|') of the rendered out/*.md are skipped: they are included verbatim from generated files.
"""
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

from .fetch import ROOT

NUM_TS = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")  # thousands separators
NUM_PLAIN = re.compile(r"\d+(?:\.\d+)?")
HEX = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b")
CODE = re.compile(r"`[^`]*`")
LINK = re.compile(r"\]\([^)]*\)")
EXTRA = ["out/quickstats_replication.txt", "out/crop_progress_overlap_count.txt", "PREREG.md", "PREREG-AMENDMENTS.md"]
SRC_DIRS = ["out/eval", "out/russia", "out/memo"]


def clean(line: str) -> str:
    return LINK.sub("]", CODE.sub(" ", HEX.sub(" ", line)))


def dec(tok: str) -> Decimal:
    return Decimal(tok.replace(",", ""))


def variants(v: Decimal) -> set[Decimal]:
    out = set()
    vals = [v] + ([v * 100] if 0 <= v <= 1 else [])
    for x in vals:
        for nd in (0, 1, 2, 3):  # 3 dp: p-values
            q = Decimal(1).scaleb(-nd)
            for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN):
                out.add(x.quantize(q, rounding=mode))
        out.add(x)
    return out


def source_numbers(root: Path) -> set[Decimal]:
    files = [root / e for e in EXTRA]
    for d in SRC_DIRS:
        files += [p for p in sorted((root / d).rglob("*")) if p.suffix in (".csv", ".md", ".txt")] if (root / d).is_dir() else []
    nums: set[Decimal] = set()
    for f in files:
        if not f.is_file():
            continue
        for ln in f.read_text().splitlines():
            ln = clean(ln)
            for rx in (NUM_TS, NUM_PLAIN):
                for t in rx.findall(ln):
                    nums |= variants(dec(t))
    return nums


def allowlist(root: Path) -> set[Decimal]:
    p = root / "docs" / "trace_allow.txt"
    res = set()
    if p.is_file():
        for ln in p.read_text().splitlines():
            tok = ln.split("#")[0].strip()
            if tok:
                res.add(dec(tok.lstrip("+-−")))
    return res


def trace_file(path: Path, ok: set[Decimal], skip_tables: bool) -> tuple[int, list[tuple[int, str]]]:
    n, bad, fenced = 0, [], False
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or (skip_tables and ln.startswith("|")):
            continue
        for t in NUM_TS.findall(clean(ln)):
            n += 1
            v = dec(t)
            auto = "." not in t and "," not in t and (1 <= v <= 10 or 1990 <= v <= 2030)
            if not auto and v not in ok:
                bad.append((i, t))
    return n, bad


def run(root: Path = ROOT) -> int:
    targets = [("out/memo.md", True, True), ("out/writeup.md", True, False), ("README.md", False, False)]
    ok = source_numbers(root) | allowlist(root)
    total, bad_n, used = 0, 0, []
    for rel, skip, mandatory in targets:
        p = root / rel
        if not p.is_file():
            if mandatory:
                print(f"trace: mandatory {rel} missing")
                return 1
            continue
        n, bad = trace_file(p, ok, skip)
        total += n
        used.append(rel)
        for i, t in bad:
            print(f"{rel}:{i}: {t}")
        bad_n += len(bad)
    if bad_n:
        print(f"TRACE FAILED {bad_n} untraced numbers ({total} checked)")
        return 1
    print(f"TRACE OK {total} numbers in {', '.join(used)}")
    return 0
