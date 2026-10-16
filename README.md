# How wide is USDA's August corn number?

Each August, USDA prints its first survey-based US corn yield, and grains desks rebuild their balance sheets around it. This repository puts an 80% band around that print, built only from USDA's own past August misses against the January final. It then scores the band on every season from 2004 to 2025, using only what was public on each release morning.

**Headline.** The 80% August band covered 21 of 22 seasons (95.5%; 90% Clopper-Pearson interval 80.2% to 99.8%). The pre-registered verdict is "too wide". The estimator is conservative by construction: at these sample sizes it is expected to cover 84.2% of seasons, so about 4 points of the excess coverage come from the estimator itself. Adding crop condition (Good/Excellent share) is not distinguishable from the plain band at August (one-sided p = 0.888, 22 seasons). None of the 33 exploratory tests falls below 0.05 after Holm adjustment.

![USDA's August yield, the 80% band and the January final, 2004–2025](out/memo/fig_aug_band.png)

The memo (`out/memo.pdf`) and the longer write-up (`out/writeup.md`) give the August 2025 answer, the stocks-to-use decision line, where the band breaks, and an exploratory port to Russian wheat. No traded edge is claimed.

## What makes the score honest

- **As-of store.** Every value carries the timestamp of the release that printed it, rebuilt from the raw USDA ESMIS archive (WASDE, Crop Production, Crop Progress). A guard refuses any value released after the forecast origin, and leakage tests run before every scored run.
- **Corrections as records.** Releases that survive only as corrected copies are flagged, not silently used. A sensitivity run drops the affected origins.
- **Rules first.** The acceptance rule, wording, sample and test family were frozen in `PREREG.md` (hash in `PREREG.sha256`) before any score existed. Later changes are logged with reasons in `PREREG-AMENDMENTS.md`. The evaluation ran once.
- **Every number traced.** `make memo` fails if any number in the memo, the write-up or this README does not appear in a generated output file or the pre-registration.

## Reproduce

Requires Python `>=3.12` and [uv](https://docs.astral.sh/uv/). Raw files are not committed. `make store` downloads them from USDA ESMIS (keyless, cached, logged with SHA-256 in `data/manifest.csv`) and rebuilds the store.

```sh
make store          # fetch raw USDA files, rebuild the as-of store
make test-leakage   # look-ahead guards
make forecasts      # forecast layer (no truth column)
make memo           # tables, figure, memo PDF, write-up, number trace
```

`make evaluate` is the single pre-registered scoring run. It refuses to run if the pre-registration hash, a leakage test or a count check fails. Its outputs are already in `out/eval/`.

A clean rebuild is `make store forecasts quickstats memo` (or `make all`); `make evaluate` and `make russia` are one-shot scoring runs whose outputs ship in `out/`.

## Layout

| Path | Contents |
|---|---|
| `src/cfstore/` | store, as-of guard, models, evaluation, memo rendering, number trace |
| `data/` | release tables, selection logs, manifest (raw files under `data/raw/` are fetched, not committed) |
| `out/eval/` | scored results: coverage, test family with Holm-adjusted p, stress years, sensitivities |
| `out/memo/` | memo tables, figure and headline |
| `out/russia/` | exploratory Russian wheat port |
| `docs/` | memo and write-up sources |
| `tests/` | leakage, validation, model and rendering tests |

Data: USDA public releases (WASDE, NASS Crop Production and Crop Progress, NASS Quick Stats).
