"""explore.py on a tiny synthetic store (tmp dir): Annual Summary dated February is picked, v2_repost skipped, parts sum."""
import csv

from cfstore import explore

HDR = ["series", "reference_period", "release_date", "release_ts", "time_source", "source_file", "file_hash", "value", "revision_flag"]


def _store(tmp_path, rows):
    p = tmp_path / "store.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        for s, ref, d, v, flag in rows:
            w.writerow([s, ref, d, f"{d}T08:30:00-04:00", "", "", "", v, flag])
    with p.open(newline="") as f:
        return explore.index(list(csv.DictReader(f)))


def _rows(y=2014):
    cp, cs, w = explore.CP, explore.CS, explore.W
    ref = f"{y}/{str(y + 1)[2:]}"
    r = []
    for m, yl in ((8, 160.0), (9, 162.0), (10, 163.0), (11, 164.0)):
        d = f"{y}-{m:02d}-12"
        r += [(cp + "yield", ref, d, yl, "v1"), (cp + "area_harvested_kac", ref, d, 80000.0, "v1"),
              (cp + "production_kbu", ref, d, yl * 80000.0, "v1")]
    r += [(cs + "yield", ref, f"{y + 1}-02-05", 170.0, "v1"), (cs + "production_kbu", ref, f"{y + 1}-02-05", 13700000.0, "v1"),
          (cs + "yield", ref, f"{y + 1}-02-05", 999.0, "v2_repost"),
          (cs + "yield", ref, f"{y + 1}-01-12", 888.0, "v2_repost"),
          (w + "ending_stocks", ref, f"{y}-08-12", 1000.0, "v1"), (w + "ending_stocks", ref, f"{y + 1}-01-12", 1111.0, "v1"),
          (w + "ending_stocks", ref, f"{y + 1}-02-10", 1200.0, "v1")]
    return r


def test_february_summary_picked_and_v2_repost_skipped(tmp_path):
    d = _store(tmp_path, _rows())
    assert explore.final_print(d, 2014) == ("2015-02-05", 170.0, 13700000.0)
    r = explore.season_row(d, 2014)
    assert r["final_yield"] == 170.0 and r["final_release_date"] == "2015-02-05"
    # first WASDE on/after the summary date, not the January one
    assert r["final_wasde_date"] == "2015-02-10" and r["final_es_mbu"] == 1200.0


def test_decomposition_sums_to_prod_rev(tmp_path):
    r = explore.season_row(_store(tmp_path, _rows()), 2014)
    assert abs(r["yield_part_mbu"] - (170.0 - 160.0) * 80.0) < 1e-9
    assert abs(r["yield_part_mbu"] + r["area_part_mbu"] - r["prod_rev_mbu"]) < 1e-9
    assert abs(r["prod_rev_mbu"] - (13700.0 - 160.0 * 80.0)) < 1e-9


def test_wasde_fallback_recorded(tmp_path):
    rows = [x for x in _rows() if not (x[0].startswith(explore.CP) and x[2] == "2014-10-12")]
    rows += [(explore.W + "yield", "2014/15", "2014-10-12", 163.0, "v1")]
    r = explore.season_row(_store(tmp_path, rows), 2014)
    assert r["oct_yield"] == 163.0 and r["yield_source"] == "CP+WASDE"
