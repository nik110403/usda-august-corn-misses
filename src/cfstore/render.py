"""F7b: expand `<!-- include: ... -->` lines of docs/{memo,writeup}.src.md into out/{memo,writeup}.md, then build out/memo.pdf.

Include lines are replaced by the first markdown table of the named generated file (never retyped); other one-line HTML
comments are stripped. PDF: pandoc + xelatex, A4, 10pt, Lato (DejaVu Sans fallback).
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .fetch import ROOT

INC = re.compile(r"^\s*<!--\s*include:\s*(\S+)\s*-->\s*$")
FIG = re.compile(r"^\s*<!--\s*include-figure:\s*([^|]+?)\s*\|\s*(.*?)\s*-->\s*$")
COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
HEADER = "\\usepackage{etoolbox}\n\\AtBeginEnvironment{longtable}{\\small}\n\\pagestyle{empty}\n"


class RenderError(Exception):
    pass


def first_table(path: Path, rel: str) -> str:
    if not path.is_file():
        raise RenderError(f"include file missing: {rel}")
    block, done = [], False
    for ln in path.read_text().splitlines():
        if ln.startswith("|"):
            block.append(ln)
        elif block:
            done = True
            break
    if not block:
        raise RenderError(f"include file has no markdown table: {rel}")
    return "\n".join(block)


def expand(text: str, root: Path, out_dir: Path) -> str:
    res = []
    for ln in text.splitlines():
        if m := INC.match(ln):
            res.append(first_table(root / m.group(1), m.group(1)))
        elif m := FIG.match(ln):
            f = root / m.group(1)
            if not f.is_file():
                raise RenderError(f"include-figure file missing: {m.group(1)}")
            res.append(f"![{m.group(2)}]({os.path.relpath(f, out_dir)})")
        elif COMMENT.match(ln):
            continue
        else:
            res.append(ln)
    return "\n".join(res) + "\n"


def font() -> str:
    fl = subprocess.run(["fc-list", "Lato"], capture_output=True, text=True).stdout
    return "Lato" if "Lato" in fl else "DejaVu Sans"


def pdf(md: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as t:
        h = Path(t) / "header.tex"
        h.write_text(HEADER)
        env = dict(os.environ, SOURCE_DATE_EPOCH="1760000000", FORCE_SOURCE_DATE="1")
        cmd = ["pandoc", md.name, "-o", out.name, "--pdf-engine=xelatex", "-V", "papersize=a4", "-V", "fontsize=10pt",
               "-V", "geometry:margin=1.7cm", "-V", f"mainfont={font()}", "-H", str(h), "--resource-path", str(md.parent)]
        r = subprocess.run(cmd, cwd=md.parent, env=env, capture_output=True, text=True)
        if r.returncode:
            raise RenderError(f"pandoc failed:\n{r.stderr}")


def run(root: Path = ROOT) -> int:
    out = root / "out"
    out.mkdir(exist_ok=True)
    try:
        for name, mandatory in (("memo", True), ("writeup", False)):
            src = root / "docs" / f"{name}.src.md"
            if not src.is_file():
                if mandatory:
                    raise RenderError(f"missing docs/{name}.src.md")
                print(f"docs/{name}.src.md absent, skipped")
                continue
            (out / f"{name}.md").write_text(expand(src.read_text(), root, out))
            print(f"wrote out/{name}.md")
        pdf(out / "memo.md", out / "memo.pdf")
        print("wrote out/memo.pdf")
    except RenderError as e:
        print(f"memo-render: {e}")
        return 1
    return 0
