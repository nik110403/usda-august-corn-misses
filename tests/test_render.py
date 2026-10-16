"""F7b render + number trace on tmp fixtures (no dependence on real out/)."""
import pytest

from cfstore import render, trace

TABLE = "| a | b |\n|---|---|\n| 1 | 15,592 |\n\nafter\n"


def mk(tmp_path):
    (tmp_path / "out" / "memo").mkdir(parents=True)
    (tmp_path / "out" / "eval").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "out" / "memo" / "t.md").write_text("intro 7\n\n" + TABLE + "\n| z |\n")
    (tmp_path / "out" / "eval" / "c.csv").write_text("coverage,n\n0.9545,15592\n")
    (tmp_path / "PREREG.md").write_text("prereg 2026\n")
    return tmp_path


def test_include_expansion(tmp_path):
    r = mk(tmp_path)
    src = "Text.\n<!-- include: out/memo/t.md -->\n<!-- a comment -->\n<!-- include-figure: out/memo/f.png | Cap -->\nEnd\n"
    (r / "out" / "memo" / "f.png").write_bytes(b"x")
    res = render.expand(src, r, r / "out")
    assert "| 1 | 15,592 |" in res and "| z |" not in res and "a comment" not in res
    assert "![Cap](memo/f.png)" in res and "include" not in res


def test_missing_include_fails(tmp_path):
    r = mk(tmp_path)
    (r / "docs" / "memo.src.md").write_text("<!-- include: out/memo/nope.md -->\n")
    assert render.run(r) == 1
    (r / "out" / "memo" / "nt.md").write_text("no table here\n")
    with pytest.raises(render.RenderError, match="nt.md"):
        render.expand("<!-- include: out/memo/nt.md -->", r, r / "out")


def test_trace_pass_and_fail(tmp_path):
    r = mk(tmp_path)
    memo = r / "out" / "memo.md"
    memo.write_text("Covered 95.5% (15,592 rows) in 2026, 3 of them.\n| 999.9 | 1 |\n")
    assert trace.run(r) == 0  # percent normalisation, thousands separator, table row skipped
    memo.write_text("Covered 95.5% and 77.7 more.\n")
    assert trace.run(r) == 1
    (r / "docs" / "trace_allow.txt").write_text("# header\n77.7  # fixture\n")
    assert trace.run(r) == 0


def test_trace_ignores_code_hex_and_readme_tables(tmp_path, capsys):
    r = mk(tmp_path)
    (r / "out" / "memo.md").write_text("commit deadbeef1234 and `4242` ok\n```\n5555\n```\n")
    (r / "README.md").write_text("| 31337 |\n")
    assert trace.run(r) == 1
    assert "README.md:1: 31337" in capsys.readouterr().out
