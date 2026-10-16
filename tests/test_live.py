"""F11 live 2026 band: rows equal Run.m1/Run.prod, only season-2026 origins, writes only under out/live/. Needs data/store.csv."""
import hashlib
from fractions import Fraction

import pytest

from cfstore import live
from cfstore.baselines import load, season_of
from cfstore.fetch import ROOT
from cfstore.models import LEVELS, Run

IDENT = {"build_commit": "c", "code_tree": "t", "store_sha256": "s"}


@pytest.fixture(scope="module")
def run():
    store, rel = load()
    return Run(store, rel)


def test_live_rows_equal_run_rows(run):
    origins = live.live_origins(run)
    assert [o["release_date"] for o in origins] == ["2026-08-12", "2026-09-11", "2026-10-09"]
    rows = live.live_rows(run)
    assert len(rows) == len(origins) * 2 * len(LEVELS)
    for o in origins:
        want = [r for r in run.m1(o) + run.prod(o)[0] if r["kind"] == "level"]
        assert [r for r in rows if r["origin_date"] == o["release_date"]] == want
    d = live.display(rows, IDENT)
    r0, d0 = rows[1], d[1]  # M1 Aug 80%
    assert d0["val_lo"] == f"{float(Fraction(r0['val_lo'])):.1f}" and d0["level"] == "0.80"


def test_only_season_2026(run):
    assert {r["season"] for r in live.live_rows(run)} == {"2026"}
    assert all(season_of(o) == 2026 for o in live.live_origins(run))


def _snap(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / "out").rglob("*")) if p.is_file()}


def test_run_writes_only_under_out_live(tmp_path, monkeypatch):
    before = _snap(ROOT)
    monkeypatch.setattr(live, "build_identity", lambda root: IDENT)
    assert live.run(tmp_path) == 0
    written = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert written == ["out/live/bands_2026.csv", "out/live/bands_2026.md"]
    assert _snap(ROOT) == before  # the real out/ is untouched
