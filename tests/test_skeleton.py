"""Truth timing: a season's first-final is invisible at an origin that precedes its release."""
import csv
from fractions import Fraction

from cfstore.asof import info_set, origins
from cfstore.fetch import DATA
from cfstore.skeleton import august_errors, truth_rows
from cfstore.store import read_store


def test_2025_first_final_invisible_at_aug_2025_origin():
    store = read_store()
    with (DATA / "wasde_releases.csv").open(newline="") as f:
        releases = list(csv.DictReader(f))
    o = next(o for o in origins(releases) if o["release_date"] == "2025-08-12")
    final = truth_rows(store, 2025)
    assert final["release_date"] == "2026-01-12" and Fraction(final["value"]) == Fraction("186.5")
    assert truth_rows(info_set(store, o), 2025) is None  # decided by info_set alone
    assert len(august_errors(store, releases, 2025)) == 15
