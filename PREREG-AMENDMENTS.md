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

1. **Known before checkpoint 2.** Good-Irwin RMSE table (production and yield x final (a)/(b) x 3 windows, 1995-2024), commit 61f8f34. These are RMSE of USDA's own Aug-Nov point (the point used by B1 production / M1 / B2 yield) against final, overlapping the scored seasons 2004-2025 in 10 / 19 / 20 crop years for the windows 1995-2014 / 2005-2024 / 1995-2024 (2013 dropped: no Oct 2013 report); the metric is % of final, not PREREG section 2's % of USDA_h; not a section 4/5 score; disclosed so F6's section 10 RMSE rows carry this note. `src/cfstore/replication.py` docstring corrected accordingly.
2. **B1 availability.** B1 is unavailable at the Oct 2008 origin: only the 2008-10-28 reissue of that Crop Production survives (item 1 of the 2026-10-15 entry), so no same-day Crop Production is in the information set. Counts of Aug-Nov origins with a B1 band, seasons 2004-2025, computed from `b1_band` (availability only, no score): August 22, September 22, October 19, November 22. October therefore has 19 B1 seasons, not 20; F6 treats Oct 2008 as missing.
3. **"Against both truths" (PREREG section 3, B1).** Both truths = the primary truth and the sensitivity truth of PREREG section 1. The production band is scored against production truth. The yield band (production band divided by USDA harvested area at the origin) is a derived/added object not defined by PREREG section 3; it is labelled so in `baselines.py` and any yield-band result is reported as such.

## 2026-10-16 - F6a implementation readings (before checkpoint 2; no section 4/5/10 score exists, `make evaluate` has not been run)

PREREG.md is unchanged. These entries state how the forecast layer and the unrun evaluation harness read ambiguous clauses.
1. **Sensitivity truth.** Source: NASS Quick Stats bulk file `qs.crops_20261015.txt.gz` (national, SURVEY, annual, `CORN, GRAIN - YIELD, MEASURED IN BU / ACRE` and `... PRODUCTION, MEASURED IN BU`), keyless, in the manifest with its sha256; extract `data/quickstats_truth.csv` (crop years 1995-2025). Pseudo-vintage label: file dated 2026-10-15. Data-validation number (not a PREREG score): the August 2025 NASS production reliability replicates with Quick Stats finals at RMSE 3.77 (printed 3.8), below/above 7/13 (printed 7/13); August 2015 gives 4.33 (printed 4.3), 10/10. See `out/quickstats_replication.txt`.
2. **Dependency.** scipy (and numpy) were not in `uv.lock` at F6a start; added (1.18.1). Needed by PREREG 3 (HiGHS LP) and for exact beta/t distributions.
3. **B1 inputs.** The forecast layer takes P from the WASDE production of the origin (allowlist: `crop_production.*` point values are cross-check only) and r = `rel_ci90_pct` from the same-day Crop Production. The derived yield band (labelled B1y) applies the production band's ratios to USDA's yield, i.e. production band divided by implied area. F5's `b1_band` (Crop Production values) stays as a cross-check in tests.
4. **M2.** Training r is built at each training season's own previous existing origin; with at least 3 distinct r the shift is a-hat + b-hat r_t and the order statistics are those of the OLS residuals; otherwise M2 = M1 exactly (a = b = 0). May rows are copies of M1 flagged `in_tests = 0`.
5. **M3.** x is read through `asof.info_set` + `assert_no_leak` at each training season's own h-origin. The first national condition line is the week ending 18 June 1995 (released 19 June), so 1995 lacks x only at the June origin (M3 trains from 1996 there). M3 = M1 with a recorded `m3_fallback` if the target season has no line or fewer than 3 training seasons have one (neither occurs in 2004-2025). LP status and the n-tau-is-integer non-uniqueness flag are recorded per fit; rearrangement is flagged.
6. **Scoring conventions.** Pinball loss uses u = 100 (final - quantile in bu/ac) / USDA_h for every yield model (PROD: its own production point); coverage uses exact percent errors against `point`. M3's 50% and 80% bands (descriptive) are its tau .25/.75 and .10/.90 quantiles.
7. **Sensitivity runs.** Corrected-release run: drops origins whose own WASDE (`origin_flag`) or M3 regressor (`m3_x_flag`) is `v1_corrected_only`; training errors are kept. v2 re-post run: `out/forecasts_v2.csv` is the forecast layer rebuilt with the logged v2 WASDE re-posts (2008-12-12, 2013-07-11, 2018-12-14) in place of the first archived files; only July 2013 is an origin.
8. **Stocks-to-use.** End stocks at a production quantile = published ending stocks + (production quantile - published production); beginning stocks, imports and total use held fixed. At small n the 10% quantile can imply negative ending stocks; the file is arithmetic conditional on USDA's use projections and is not scored.
