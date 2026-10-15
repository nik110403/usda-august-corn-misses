# Pre-registration: US corn yield, release-aware calibration audit

Frozen 2026-10-15, before any score is computed. Source design: STRATEGY.md §5 (option A1, Nik's F2 pick, decision log 2026-10-15), adjusted for checks/RESULTS.md and for the pre-hash review in `tribunals/2026-10-15-prereg/review.md`. The hash is in `PREREG.sha256` and in the first commit of `code/`. This file is never edited after the hash. Any later change goes into `PREREG-AMENDMENTS.md` as a dated entry that says whether scores already existed when it was made. An amendment made after scores existed is labelled post hoc in every table it touches.

## 1. Target, truth, origins

- **Target.** US national corn-for-grain yield, bu per harvested acre, crop season t.
- **Primary truth.** The January first-final, the US corn yield and production in the first WASDE released in calendar year t+1 that carries the Annual Summary numbers. For the 2018 crop this is the 8 February 2019 WASDE (shutdown; there was no January 2019 WASDE). F1 item 11 found WASDE equal to the Annual Summary in 24 of 24 yield cells.
- **Sensitivity truth.** The latest revised value in NASS Quick Stats, labelled a pseudo-vintage. Reported beside the primary truth, never in a headline, never in a test.
- **Origins.** The regular monthly WASDE of May, June, July, August, September, October and November. Reissues (for example the 28 October 2008 reissue) are store releases, never origins. A missing origin is not imputed: that season is absent from that horizon. There was no October WASDE in 2013 or 2025 (shutdowns), so October has 20 scored seasons and every other horizon has 22. Origin time `origin_ts` is that WASDE's documented release time: 08:30 ET through December 2012, 12:00 ET from January 2013 (F1 item 4). The ESMIS 12:00 listing time is a placeholder and is never used.
- **Information set at origin o.** Every store row with `release_ts < origin_ts`, plus the origin's own WASDE and the Crop Production report released with it. Any other series released on the origin's date counts as released the next day. In particular, a Monday or holiday-Tuesday WASDE origin uses the previous week's Crop Progress, never the same day's 16:00 release. This affects 29 of the 231 May–November WASDE listing rows (F1 item 5b); the build recounts on the deduplicated origins.
- **Truth timing.** Season s's error enters the training set only at origins after its first-final WASDE has been released.
- **Corrections.** The first archived file of a release date is used. `v2` and later re-posts are logged and used only in a sensitivity run. Releases where ESMIS holds only a corrected file are flagged. The primary analysis uses them as archived. The sensitivity run drops every origin whose own WASDE or whose M3 regressor comes from a flagged release; training errors from flagged releases are kept. The memo states the count.

## 2. Errors and the quantile estimator

- **Error definition.** Percent error at horizon h for season s: e = 100 × (final − USDA_h) / USDA_h, where USDA_h is the yield in the origin's WASDE. Bands are mapped back to bu/ac as USDA_h × (1 + q/100). Production errors use the same percent definition. (Declared departure, §9.)
- **Training set.** At an origin of season t, horizon h: the errors at horizon h of every season whose first-final was released before `origin_ts`. Same-horizon only. n = 9 at season 2004, rising by one per season to n = 30 at season 2025. October is one lower from 2014 on (no 2013 origin): n runs 9–28.
- **Split-conformal ranks.** Sort the n past errors e(1) ≤ … ≤ e(n). A central band at level 1 − α uses k_lo = ⌊(n+1)α/2⌋ and k_hi = ⌈(n+1)(1 − α/2)⌉. Single quantiles at level τ use rank ⌊(n+1)τ⌋ for τ < 0.5 and ⌈(n+1)τ⌉ for τ ≥ 0.5, so the 10/90 quantiles are the 80% band and the 25/75 quantiles are the 50% band. Ranks are computed in exact rational arithmetic, never floating point. In floating point, α = 1 − 0.8 is 0.19999…, so (n+1)α/2 falls just below an integer and its floor drops by one; that wrongly unscores n = 9 and shifts the band at n = 19 and 29 at 80%, and at n = 19 at 90%.
- **Coverage.** A season is covered if and only if e(k_lo) ≤ e_s ≤ e(k_hi), compared in percent-error space with exact rationals. Rounding to bu/ac is for display only.
- **When a level is scored.** A level is scored at an origin only if 1 ≤ k_lo and k_hi ≤ n. That holds from n = 9 at 50% and 80%, and from n = 19 at 90%.
- **Expected coverage under exchangeability** is (k_hi − k_lo)/(n+1), recorded beside the nominal level at every origin. Over the scored seasons it averages:

  | Level | Horizons | Scored seasons | n range | Mean expected coverage |
  |---|---|---|---|---|
  | 50% | all but October | 22 (2004–2025) | 9–30 | 54.4% |
  | 80% | all but October | 22 (2004–2025) | 9–30 | 84.2% |
  | 90% | all but October | 12 (2014–2025) | 19–30 | 92.0% |
  | 50% | October | 20 (2004–2024, no 2013) | 9–28 | 54.4% |
  | 80% | October | 20 | 9–28 | 84.5% |
  | 90% | October | 10 (2015–2024) | 19–28 | 91.7% |

  The estimator is conservative by design (it covers at least the nominal level). For example, at n = 22 the 80% band uses ranks 2 and 21 and expects 82.6%. The acceptance rule (§4) still tests against the nominal level, as STRATEGY declared. Whenever a band is called "too wide", the memo states that about 4 points of the excess at 80% are expected from the estimator itself.

## 3. Models

- **B0 (exploratory).** Linear OLS trend on season year, fitted to the primary-truth first-finals of crops from 1995 on that were released before the origin (at least 5 required). Its error is e = 100 × (final − B0)/B0. The band uses the §2 ranks on B0's own out-of-sample errors from earlier seasons, so it is scored once those number at least 9: seasons 2009–2025, 17 seasons. The B0-vs-M1 test (§5) maps both models' quantiles to bu/ac (B0 × (1 + q/100) and USDA_h × (1 + q/100)) and computes the §4 pinball loss on 100 × (final − quantile)/USDA_h, the percent base of M1. It uses the §4 statistic, two-sided, over those 17 seasons.
- **B1 (USDA's own band, descriptive).** August–November only. NASS states its 90% reliability for **production** (F1 item 3) as a deviation in percent of the final, so the band is [P/(1 + r/100), P/(1 − r/100)], where P is USDA's production at the origin and r the percentage printed in the same-day Crop Production. It is scored at 90% against both truths over 2004–2025 (22 seasons; 20 at October, §1) and is descriptive by declaration: no acceptance wording, no test.
- **B2.** Carry-forward of the previous origin's USDA yield, used only in revision diagnostics.
- **M1 (primary calibration object).** USDA's point plus the §2 conformal quantiles at levels 0.10, 0.25, 0.50, 0.75, 0.90, plus 0.05 and 0.95 once n ≥ 19.
- **M2 (exploratory).** A revision-bias shift on M1. The revision is r = 100 × (USDA_h − USDA_{h−1}) / USDA_{h−1}, where USDA_{h−1} is the yield at the previous existing origin of the same season (so November 2013 uses September 2013). Fit OLS e = a + b·r on the training seasons at that horizon. M2's quantile at each level is â + b̂·r_t plus the §2-rank order statistic of the OLS residuals. If the training r has fewer than 3 distinct values, b̂ = 0 and M2 = M1 (d_s = 0). At May no revision exists, so M2 = M1 and May is outside the M2 tests.
- **M3 (primary comparison model).** Linear quantile regression of past percent errors on one regressor, at each τ of the fixed set {0.10, 0.25, 0.50, 0.75, 0.90}: an intercept and a slope per τ, 10 parameters per horizon.
  - *Regressor.* x_{s,h} = the national corn Good + Excellent share (G + EX) in the last Crop Progress of season s that carries a national corn condition line and was released before season s's own h-origin `origin_ts` (Monday-origin seasons use the previous week, §1). The national line is the printed multi-state aggregate (17–18 states; F1 item 5a).
  - *Detrending.* At the origin of season t, one OLS trend on season year is fitted to x_{s,h} over the training seasons s < t, and subtracted from both the training values and the target season's x.
  - *Missing condition.* A training season without such a line is dropped from that horizon's fit. If the target season lacks one, M3 = M1 and d_s = 0. No condition line exists before the May origin, so May is outside the M3 tests. If national condition data are missing for 1995, M3 trains from 1996 and the scored seasons stay 2004–2025.
  - *Timing differs by era (checked 2026-10-15).* National corn condition ran into November in 2007–2008 (for example the week ending 2 November 2008, 18 states) but stops in mid-October in 2023–2024 (last line: week ending 15 October 2023, 13 October 2024). In 2025 the last report before the shutdown is 29 September. For the October and November origins the regressor is therefore the latest available reading, whose date varies across seasons. This is reported, not corrected.
  - *Estimation.* The exact quantile-regression linear program, solved with `scipy.optimize.linprog(method="highs")` at a version pinned in the lockfile; statsmodels `QuantReg` is not used. Where the solution is not unique (nτ is an integer), the solver's returned vertex is used. Crossing quantiles are sorted (rearrangement).
- **M4 (weather).** Dropped (STRATEGY §5.2).

Every model and feature tried is counted in the write-up, including any tried and dropped.

## 4. Primary analysis (one calibration object, one test)

1. **Primary calibration object.** M1's 80% band at the August origin, 22 scored seasons (2004–2025), primary truth.
2. **Acceptance rule.** Let x be the count of seasons whose primary truth lies inside the band (inclusive, §2). Compute the two-sided 90% Clopper–Pearson interval for x/22. Report the first of these wordings whose condition holds, in this order:
   - interval inside [65%, 95%]: "consistent with nominal 80% within ±15 points";
   - interval entirely below 80%: "too narrow";
   - interval entirely above 80%: "too wide";
   - otherwise: "not distinguishable from 80% at this sample size, and not shown equivalent".

   The first wording cannot be reached at n = 22 for any 80% band (the first n where a count can reach it is 24, with x = 20), nor for the production band at 80%, nor for any 50% band at any n ≤ 30. That is the declared design, and the margin is not widened later. For scale: 18 of 22 gives 63–94%, 19 of 22 gives 68–96%.
3. **Why a 90% Clopper–Pearson interval and not the 95% Wilson interval.** The Wilson 95% half-width (about ±16 pp at n = 22, STRATEGY §5.4) only illustrates precision. The decision rule is an equivalence test: two one-sided exact tests at 5% each (TOST at α = 0.05), which is the same as checking whether the two-sided 90% exact interval lies inside the margin. Clopper–Pearson is exact and conservative at small n.
4. **Primary model comparison.** M3 against M1 at the August origin, 22 seasons, primary truth. Per season, the loss is the mean pinball loss over τ ∈ {0.10, 0.25, 0.50, 0.75, 0.90}, computed on percent errors. The loss differential is d_s = L(M1) − L(M3). Test: Diebold–Mariano with the Harvey–Leybourne–Newbold correction for horizon 1 (one forecast per season). The long-run variance is γ̂0 = (1/n) Σ (d_s − d̄)², and the statistic is DM × √((n−1)/n). That equals the paired t statistic d̄ / (s_d/√n), with s_d on n − 1 degrees of freedom, compared with Student t on n − 1 = 21 df. One-sided H1: E[d] > 0. α = 0.05.
   - Rejects: "M3 improves on M1 at August (one-sided HLN-DM, p = …, 22 seasons)".
   - Does not reject: "M3 and M1 are not distinguishable at August with 22 seasons." No affirmative claim follows from a non-rejection.
   - M3 cut under §8 before any of its scores exist: "the pre-registered comparison was not run", never a null.

## 5. Exploratory family (Holm)

The family is fixed now at 33 tests. Every computed test keeps its Holm-adjusted p-value. A test that was never computed, because a §8 scope cut removed it before any of its scores existed, enters the family with p = 1, so cutting never makes the adjustment less strict.

| Group | Tests | Count |
|---|---|---|
| M3 vs M1, same HLN-DM as §4 | Jun, Jul, Sep, Oct, Nov | 5 |
| M2 vs M1, same HLN-DM | Jun, Jul, Aug, Sep, Oct, Nov | 6 |
| M1 coverage, two-sided exact binomial vs nominal | 50% at 7 horizons; 80% at the 6 non-August horizons | 13 |
| Production band (§6), two-sided exact binomial vs 80% | 7 horizons | 7 |
| B0 vs M1, HLN-DM two-sided | Aug, Nov | 2 |
| **Total** | | **33** |

The two-sided exact binomial p-value is p = min(1, 2 × min(P(X ≤ x), P(X ≥ x))) under the nominal level. October tests use its 20 seasons. The exploratory bands also get the §4 acceptance wording and order, with margins of ±15 pp around the nominal level (50%: [35%, 65%]; 80%: [65%, 95%]), labelled exploratory in every table. The 90% level and B1 are descriptive (fewer than 15 seasons at 90%, B1 by declaration): coverage, n and the exact interval are reported, without acceptance wording and without tests. The sensitivity truth, the corrected-release sensitivity run, stress years (2012, 2019, 2020) and the PIT and split-half checks are descriptive.

## 6. Production band and stocks-to-use step

- **Production band (exploratory, in §5).** USDA's production at the origin plus §2 conformal quantiles of past percent production errors against the January first-final production, at the same horizon, so area error is inside the band. It is scored at 80% with the acceptance rule and called calibrated only if it passes.
- **Stocks-to-use step (not scored).** At each origin, the 10/50/90 production quantiles go through USDA's balance sheet as published at that origin, holding beginning stocks, imports and total use fixed. The step shows the carry-in (beginning-stocks) revision between origins beside the production band, because in 2019 it equalled the production revision (F1 item 13). It is labelled as conditional on USDA's use projections and is never called calibrated.

## 7. Sample, minimums, fallback, kill

- Seasons 1995–2025. The first 9 (1995–2003) only train. Calibration wording needs at least 15 scored seasons at that level and horizon.
- **Precision carried from STRATEGY §5.3–5.4.** The default type-7 sample quantile would give an 80% band covering about 72% in expectation at n = 20, which is why §2 uses conformal ranks. The 95% Wilson half-width on an observed 80% coverage is about ±16 pp at n = 22, ±14 at n = 30 and ±18 at n = 16. One miss moves coverage by 4.5 points at n = 22.
- **2010+ fallback** (not expected: F1 items 1, 2 and 11 passed): 16 seasons, 7 scored after burn-in. If F1 item 1 fails on the full build and cannot be fixed within 6 h (hand-keying or a sourced repair), all calibration wording and tests are dropped, the NASS replication gate is dropped, and the piece becomes a descriptive information-resolution study, 2010–2025.
- **Kill.** If F1 item 2, 4 or 11 fails on the full build, in any era, and cannot be fixed within 6 h, modelling stops and no score is computed; the piece becomes a data-and-replication note or is dropped. Item 4 is listed because without documented release times the information set in §1 cannot be defined, and item 2 and 11 because without them the origin or the truth is undefined.

## 8. Scope cuts and their timing

The ordered scope-cut list is STRATEGY §10. A scope cut applies only to analyses with no computed score. It is logged in TRACKER with a timestamp and the code commit before any score of that item exists. Once the §4 primary analysis has been computed, it is reported in its §4 wording and cannot be cut. Checkpoint 2's cut decision is taken before the first `make evaluate`. The first `make evaluate` computes every §4 and §5 score in one run; once any §4 score exists, neither §4 item can be cut.

## 9. Declared departures from STRATEGY

1. Percent errors instead of level errors (STRATEGY §5.2 wrote final − USDA). Corn yield rose by roughly a third over 1995–2025, so level errors are less plausibly exchangeable than relative errors, and NASS states its own reliability in percent.
2. Single quantiles below the median use the floor rank ⌊(n+1)τ⌋ (STRATEGY §5.3 used the ceiling for every τ), so the 10/90 and 25/75 pairs equal the central bands.
3. M3 has 10 parameters per horizon (2 per τ at 5 levels), where STRATEGY and SPEC F6 spoke of two per horizon.
4. The same-day early-time exception of STRATEGY §5.1 is removed: every other same-day series counts as next-day.
5. No test uses the sensitivity truth (STRATEGY §5.5 listed it in the exploratory family); it is descriptive.
6. Clark–West (STRATEGY §5.5) is replaced by HLN-DM for nested pairs. HLN-DM is conservative under nesting, so this can cost power but does not inflate size.
7. B1 is scored over all 22 seasons (2004–2025) and stays descriptive by declaration, although 22 exceeds the 15-season minimum of §7. It is USDA's own object at 90% (STRATEGY §5.2), not a calibration claim of this project.

## 10. Reported scores (all by horizon)

Pinball loss at each τ in use. Their mean is labelled a *truncated quantile score*, not CRPS. Coverage at 50/80/90% with n, x and the exact interval. Expected coverage (§2) beside the nominal level. PIT histogram, sharpness (mean band width in bu/ac), MAE and RMSE as skill relative to B1's point, Mincer–Zarnowitz, and the four leakage-test results with the overlap-origin count.

## 11. Claims that may never appear

Traded edge or P&L. "Beats USDA" without a rejecting primary test or a Holm-adjusted exploratory test (stated with horizon and p-value). "Calibrated" without the acceptance rule passing. Any affirmative claim from a non-rejection. A causal weather effect. Novelty beyond "to our knowledge, no prior coverage test of the NASS band" (F1 item 8). Precision beyond the scored season count.
