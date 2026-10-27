POST-HOC EXPLORATORY — not pre-registered, not scored; descriptive only.

n seasons: 31 (1995-2025), yield from first August Crop Production print vs January Annual Summary.

## Mean absolute yield miss, |err_pct| (%)

- 1995-2009: 3.71 (n=15)
- 2010-2025: 2.21 (n=16)
- 2015-2025: 1.75 (n=11)
- 2004-2014: 3.42 (n=11)
- 2004-2014 vs 2015-2025: 3.42 vs 1.75
- OLS of abs_err_pct on season: slope -0.117 pp/yr, t = -2.54, n = 31

## Ending-stocks revision and production revision relative to August ending stocks

- |d_es_pct_of_aug_es| >= 25, 1995-2025: 9/31; seasons 1995, 1996, 2004, 2005, 2006, 2008, 2010, 2020, 2024
- |d_es_pct_of_aug_es| >= 25, 2015-2025: 2/11; seasons 2020, 2024
- median |d_es_pct_of_aug_es| (1995-2025): 11.21
- |usefixed_pct| >= 25, 1995-2025: 10/31; seasons 1995, 1996, 2004, 2005, 2006, 2010, 2011, 2020, 2021, 2022
- |usefixed_pct| >= 25, 2015-2025: 3/11; seasons 2020, 2021, 2022
- median |usefixed_pct| (1995-2025): 15.86

## Mean absolute production revision and its parts (Mbu)

| period | |yield_part| | |area_part| | |prod_rev| |
|---|---|---|---|
| 1995-2009 | 365 | 97 | 348 |
| 2010-2025 | 315 | 141 | 351 |
| 2015-2025 | 260 | 161 | 345 |

## September momentum

- sep_side_agree (sign(sep-aug)==sign(final-aug)): 22/31
- zero-momentum baseline 0.5+asin(rho)/pi, rho = sqrt(E[sep_move^2]/(E[sep_move^2]+E[post_sep_move^2])): rho = 0.454, baseline 0.650 (expected 20.1 of 31)
- continuation (sign(post_sep_move)==sign(sep_move)): 16/31

## Later-month yield misses, mean |err| (%)

| period | |oct_err_pct| | |nov_err_pct| |
|---|---|---|
| 2004-2014 | 1.38 | 0.83 |
| 2015-2025 | 1.60 | 0.92 |

## Stress seasons

- 2020: yield 181.8 -> 172.0; prod_rev -1095.7 Mbu (yield_part -823.4, area_part -272.3); ending stocks 2756 -> 1552 (d_es -1204 Mbu, -43.7%; final WASDE 2021-01-12)
- 2025: yield 188.8 -> 186.5; prod_rev 278.9 Mbu (yield_part -204.0, area_part 482.9); ending stocks 2117 -> 2227 (d_es 110 Mbu, 5.2%; final WASDE 2026-01-12)
- 2011: yield 153.0 -> 147.2; prod_rev -555.7 Mbu (yield_part -489.5, area_part -66.2); ending stocks 714 -> 846 (d_es 132 Mbu, 18.5%; final WASDE 2012-01-12)

## August reliability (as printed; refers to production over USDA's prior 20-year window)

- 1995: rel_rmse_pct 8.7, rel_ci90_pct 15.0
- 2004: rel_rmse_pct 6.0, rel_ci90_pct 10.4
- 2015: rel_rmse_pct 4.3, rel_ci90_pct 7.5
- 2025: rel_rmse_pct 3.8, rel_ci90_pct 6.5
- 2026: rel_rmse_pct 3.5, rel_ci90_pct 6.0
