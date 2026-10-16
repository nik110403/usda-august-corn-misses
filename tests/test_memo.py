"""F7a memo assets reproduce the numbers already in out/eval/ (no score is recomputed)."""
import csv
import re
from pathlib import Path

import pytest

OUT = Path(__file__).resolve().parents[1] / "out"
pytestmark = pytest.mark.skipif(not (OUT / "memo" / "coverage_table.csv").exists(), reason="run `make memo-assets` first")


def rd(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def test_coverage_table_matches_eval():
    cov = {(r["model"], r["horizon"], r["level"]): r for r in rd(OUT / "eval" / "coverage.csv") if r["truth"] == "primary"}
    rows = rd(OUT / "memo" / "coverage_table.csv")
    assert (rows[0]["model"], rows[0]["horizon"], rows[0]["level"], rows[0]["label"]) == ("M1", "Aug", "80%", "primary")
    lv = {"50%": "1/2", "80%": "4/5", "90%": "9/10"}
    for r in rows:
        c = cov[(r["model"], r["horizon"], lv[r["level"]])]
        for k in ("n", "x", "coverage", "cp90_lo", "cp90_hi", "expected_coverage"):
            assert r[k] == c[k], (r["model"], r["horizon"], k)
        assert r["label"] == c["status"] and r["wording"] == c["wording"]
    assert {r["model"] for r in rows} == {"M1", "PROD", "B1"}
    assert all(r["label"] == "descriptive" for r in rows if r["model"] == "B1")


def test_headline_matches_primary_txt():
    prim = (OUT / "eval" / "primary.txt").read_text()
    h = (OUT / "memo" / "headline.md").read_text()
    x, n = re.search(r"x = (\d+) of n = (\d+)", prim).groups()
    assert f"covered {x} of {n} seasons" in h
    for w in re.findall(r'Wording: "([^"]+)"', prim):
        assert w in h
    assert "byte-identical" in h and (OUT / "forecasts.csv").read_bytes() == (OUT / "forecasts_v2.csv").read_bytes()


def test_stocks_flags_negative_q10_and_decision_line():
    rows = rd(OUT / "memo" / "stocks_to_use_aug.csv")
    assert {r["season"] for r in rows if r["flag"]} == {"2011", "2012"}
    assert all(r["stu_q10_pct"] == "" for r in rows if r["flag"])
    assert not any(r["stu_q10_pct"].startswith("-") for r in rows)
    last = rows[-1]
    md = (OUT / "memo" / "stocks_to_use_aug.md").read_text()
    assert f"falls from {float(last['stu_usda_pct']):.1f}% to {float(last['stu_q10_pct']):.1f}%" in md


def test_stress_years_match_eval():
    ev = {(r["model"], r["season"], r["horizon"]): r for r in rd(OUT / "eval" / "stress_years.csv")}
    md = (OUT / "memo" / "stress_years.md").read_text()
    for (m, s, h), r in ev.items():
        if m in ("M1", "PROD"):
            assert f"{r['tqs']} ({r['covered_0.5']}/{r['covered_0.8']})" in md


def test_outputs_exist_and_record_inputs():
    for n in ("coverage_table.csv", "august_answer.csv", "stocks_to_use_aug.csv"):
        assert all(len(r["inputs_sha256"]) == 64 for r in rd(OUT / "memo" / n))
    for n in ("fig_aug_band.png", "fig_aug_band.svg", "august_answer.md", "stress_years.md"):
        assert (OUT / "memo" / n).stat().st_size > 0
