# How wide is USDA's August corn number?

Every August, the USDA's WASDE and Crop Production reports release the first survey-based US corn yield estimate, prompting grains desks to rapidly rebuild their balance sheets. This initial point estimate provides a direction, but to inform positioning, desks need to understand how far the January final can land from it. To assess this, I test whether a band built from the USDA's past August misses could maintain its stated coverage over 22 seasons.

The results show that an 80% band, constructed over 22 seasons, achieved coverage in 21 of those seasons, exceeding its nominal coverage. The pre-registered verdict is "too wide", and the estimator's own share of the excess coverage is about 4 points.

## Rebuilding what was known on release morning

Backtests that use today's revised numbers carry look-ahead bias, and it flatters their results. Every value is rebuilt from public USDA releases (WASDE, Crop Production, Crop Progress) with its release timestamp. Each row of data is timestamped, and a guard is in place to prevent the use of any value released after the forecast origin. Additionally, any series released on the origin date is treated as if it were released the next day, and leakage tests are run before every scored run.

Corrections are handled as separate records. Files that have been corrected are flagged, and re-posted files are logged. A sensitivity run is also conducted, dropping the affected origins to assess the impact of the corrections. The rules for the backtest were established beforehand, including the acceptance rule, wording, sample, and 33 exploratory tests with Holm adjustment.

One disclosure: replicating the NASS reliability table was an earlier look at the USDA's August point against finals, so the RMSE comparison is not blind to that look. The replication gate was passed by reproducing NASS's printed August production RMSE for the 2015 issue, which was 4.33 compared to the printed value of 4.3. A weather model was dropped before scoring. Timestamps and frozen rules close the look-ahead route; they do not settle the quality of the score.

## What the score says

The USDA's August yield forecast is accompanied by our own 80% band, constructed from past percent misses. This band has covered 21 of 22 seasons, resulting in a 95.5% coverage rate, with a 90% Clopper-Pearson interval of 80.2% to 99.8%. As pre-registered, this is considered "too wide". The estimator is conservative by construction, with an expected coverage of 84.2% for these seasons, accounting for approximately 4 points of the 15-point excess coverage.

Exploratory analysis at other horizons, from May to November, shows coverage rates ranging from 77.3% to 95.5%, mostly indistinguishable from the nominal 80% rate at this sample size. The September band is also deemed "too wide". Adding crop condition (M3) to the August band is not distinguishable from the plain band (p = 0.888, 22 seasons); revision momentum (M2) gets the same verdict.

A family of 33 exploratory tests was conducted, with none yielding a p-value below 0.05 after applying the Holm correction. One raw test result showed a p-value of 0.009, but this became 0.295 after correction.

![USDA's August yield, the 80% band and the January final, 2004–2025](memo/fig_aug_band.png)

## From yield band to stocks-to-use

Our 80% band around USDA's August 2025 print of 188.8 bu/ac ran from 178.6 to 200.6 bu/ac. The final yield in January came in at 186.5 bu/ac, which is inside our band. For production, we estimated a range of 15,592 to 17,893 Mbu, while the USDA estimated 16,742 Mbu. The January final production was 17,021 Mbu, also within our band.

The production quantiles go through USDA's August balance sheet with beginning stocks, imports and total use held fixed. Our production band is then used to estimate stocks-to-use ratios. The USDA's August balance sheet shows a stocks-to-use ratio of 13.3%, while our own estimates suggest a wider range, with the 10th percentile at 6.1%.

The relationship between stocks-to-use and price exhibits convexity, as described by Wright in 2011. This means that prices tend to rise more sharply as stocks approach a floor. So the price effect of the downside stocks-to-use tail outweighs that of the equally sized upside. Our approach focuses on the yield tail and its implications for stocks-to-use. The August 2025 stocks-to-use quantiles show a median of 13.4% and a 90th percentile of 20.5%. These quantiles are conditional on USDA's use projections and are not scored.

## Where the band breaks

The band fails in drought springs and on late-season surprises. In 2012, a drought year, the January final fell beyond the 50% and 80% bands for May, June, and July origins, while August origins were covered. Late in the season the band covers less: at November the 80% band covers 17 of 22 seasons (77.3%) and the 50% band 8 of 22 (36.4%). In 2020 the final fell beyond the 80% band for October and November origins.

The sample consists of 22 seasons, with each season equivalent to approximately five coverage points. The supported verdict is "too wide": the August band covers 95.5% of seasons against a nominal 80%. About 4 points of this excess coverage can be attributed to the estimator itself. More seasons would be required to support finer claims.

In 2019, a year with late planting, the final fell within the 80% band for all origins from May to November. On the records side, the v2 sensitivity run is null by construction, because the re-posted files carry identical values, and five NASS primaries with update alerts about other crops stay unflagged. No traded edge is claimed.

## Beyond US corn

Our research code, unchanged, has been applied to Russian wheat production data from the USDA's World Agricultural Supply and Demand Estimates (WASDE) table. The focus is on production, not yield. The question at hand is how well the initial August print by the USDA matches the eventual settled figure, which is considered the truth source. The object of measurement is the USDA's revisions, not the actual harvest or crop tracking.

The results show that the 80% band, derived from our code, covered 18 out of 18 seasons from 2006 to 2023. The Clopper-Pearson interval for this coverage is between 84.7% and 100%. The verdict on the band is "too wide", with the estimator alone expecting 84.4% coverage, accounting for approximately 4 points of excess coverage.

This analysis is exploratory and outside the standard test family. The 18-season timeframe is due to the USDA's practice of settling Russian crop data in the April report, almost three years after harvest. The settled figures for the 2022/23 and 2023/24 seasons come from corrected-only files. The same store, rulebook and coverage score apply to other origins with dated vintages, such as EU rapeseed via MARS or Brazil safrinha via CONAB, given sufficient seasons. The Russia line shows the harness runs on a second series; it makes no claim about effectiveness.
