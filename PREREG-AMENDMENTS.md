# PREREG amendments (code-side interpretations, recorded before any score)

PREREG.md is not edited (hash in PREREG.sha256 unchanged). Entries here only state how an ambiguous clause was implemented.

## 2026-10-15 - corrected-only NASS releases (PREREG section 1, "Corrections")

Basis: PREREG section 1: "Releases where ESMIS holds only a corrected file are flagged. The primary analysis uses them as archived."

Rule implemented in the as-of store (`revision_flag = v1_corrected_only` throughout):
1. If the date the corrected content became public is documented, the row is stamped at that date, and the ordinary date rule keeps it out of earlier information sets. The original ESMIS listing date is kept in `data/nass_selection.csv`.
   - Crop Production 2008-10-10 `_revision` -> release_date 2008-10-28 (text: "reissued on October 28"; checks/items-C.md section 14).
   - Crop Progress 2015-08-03 `_correction` -> 2015-08-04 (text: "Update Alert - August 4, 2015"; national estimates unchanged).
2. Without a documented reissue date the row keeps its archived date and is admitted as archived, including as the origin's joint Crop Production release: Crop Production 2008-08-12 (correction: Minnesota/US sugarbeet acres; corn untouched), Crop Production 2010-01-12, Crop Progress 1997-06-24 (updated tables released 09:00 Tuesday, stamped 16:00, conservative), Crop Progress 2008-07-28, 2015-11-23, Annual Summary 2015-01-12.
3. Crop Production 2003-08-12 is flagged `v1_corrected_only` (in-text narrative correction, CropProd-08-12-2003.txt lines 27-30), admitted as archived.
There is no flag-based exclusion in `asof.py`; the guard stays independent and still requires the table time.

Recorded before any score, coverage or interval is computed: no scoring code exists on this branch. Commit: the one that adds this file on branch f4c-nass (parent 23e97c9); see `git log -- PREREG-AMENDMENTS.md`.
