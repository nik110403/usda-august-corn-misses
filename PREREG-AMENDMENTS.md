# PREREG amendments (code-side interpretations, recorded before any score)

PREREG.md is not edited (hash in PREREG.sha256 unchanged). Entries here only state how an ambiguous clause was implemented.

## 2026-10-15 - corrected-only NASS releases (PREREG section 1, "Corrections")

Basis: PREREG section 1: "Releases where ESMIS holds only a corrected file are flagged. The primary analysis uses them as archived."

Rule implemented in the as-of store (`revision_flag = v1_corrected_only` throughout):
1. If the date the corrected content became public is documented, the row is stamped at that date, and the ordinary date rule keeps it out of earlier information sets. The original ESMIS listing date is kept in `data/nass_selection.csv`.
   - Crop Production 2008-10-10 `_revision` -> release_date 2008-10-28 (text: "reissued on October 28"; checks/items-C.md section 14).
   - Crop Progress 2015-08-03 `_correction` -> 2015-08-04 (text: "Update Alert - August 4, 2015"; national estimates unchanged).
   - Crop Progress 2015-11-23 `_correction` -> 2015-11-24 (text: "Update Alert - November 24, 2015 ... is being reissued"; peanuts table; no corn condition row in the store).
   - Crop Production Annual Summary 2015-01-12 `_revision` -> 2015-02-05 (text: "Update Alert - February 5, 2015 ... is being reissued"; forage corrections).
   The code reads the date from the file's printed "Update Alert - <date> ... reissued" line for every corrected-only NASS file and raises if it disagrees with the `REISSUE_DATES` cross-check; `REISSUE_DATES` is the fallback only for 2008-10-10 (no dated alert line).
2. Without a printed reissue date the row keeps its archived date and is admitted as archived, including as the origin's joint Crop Production release: Crop Production 2008-08-12 (correction: Minnesota/US sugarbeet acres; corn untouched), Crop Production 2010-01-12 (West Virginia hay stocks), Crop Progress 1997-06-24 (updated tables released 09:00 Tuesday, stamped 16:00, conservative), Crop Progress 2008-07-28 (revision, no alert line).
3. Crop Production 2003-08-12 is flagged `v1_corrected_only` (in-text narrative correction, CropProd-08-12-2003.txt lines 27-30), admitted as archived.
There is no flag-based exclusion in `asof.py`; the guard stays independent and still requires the table time.

Recorded before any score, coverage or interval is computed: no scoring code exists on this branch. Commit: the one that adds this file on branch f4c-nass (parent 23e97c9); see `git log -- PREREG-AMENDMENTS.md`.

Fix round 2 (2026-10-15, still before any score): the first version of this entry listed Crop Progress 2015-11-23 and the Annual Summary 2015-01-12 as having no documented reissue date; both print one (Nov 24 and Feb 5, 2015). They are now stamped at those dates, as listed in item 1.

## 2026-10-16 - F5 disclosures and readings (before checkpoint 2; no PREREG score computed)

1. **Known before checkpoint 2.** Good-Irwin RMSE table (production and yield x final (a)/(b) x 3 windows, 1995-2024), commit 61f8f34. These are RMSE of USDA's own Aug-Nov point (the point used by B1 production / M1 / B2 yield) against final, over overlapping 20-21 scored seasons; not a section 4/5 score; disclosed so F6's section 10 RMSE rows carry this note. `src/cfstore/replication.py` docstring corrected accordingly.
2. **B1 availability.** B1 is unavailable at the Oct 2008 origin: only the 2008-10-28 reissue of that Crop Production survives (item 1 of the 2026-10-15 entry), so no same-day Crop Production is in the information set. Counts of Aug-Nov origins with a B1 band, seasons 2004-2025, computed from `b1_band` (availability only, no score): August 22, September 22, October 19, November 22. October therefore has 19 B1 seasons, not 20; F6 treats Oct 2008 as missing.
3. **"Against both truths" (PREREG section 3, B1).** Both truths = the primary truth and the sensitivity truth of PREREG section 1. The production band is scored against production truth. The yield band (production band divided by USDA harvested area at the origin) is a derived/added object not defined by PREREG section 3; it is labelled so in `baselines.py` and any yield-band result is reported as such.
