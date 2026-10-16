PY = uv run python -I -m cfstore
.PHONY: all store test-leakage test-validation test-baselines test-models test-russia replication skeleton forecasts quickstats preflight evaluate russia-store russia-forecasts russia-preflight russia memo-assets memo live
# clean rebuild (F8): store -> forecasts -> quickstats -> replication -> memo; never re-scores (evaluate and russia are one-shot, outputs ship in out/)
all: store forecasts quickstats replication memo

# rebuilds data/{listing,store,wasde_releases,txt_selection}.csv (both WASDE eras) and data/nass_{listing,releases,selection}.csv
# (Crop Progress, Crop Production, Annual Summary) from the manifest-backed raw files, then out/crop_progress_overlap*.
store:
	$(PY) store
	$(PY) overlap
test-leakage:
	uv run pytest tests/test_leakage.py tests/test_conformal.py tests/test_skeleton.py -q
test-validation:
	uv run pytest tests/test_validation.py -q -s
# F5 baselines B0/B1/B2 + replication helpers: shapes, as-of correctness, formulas; computes no score (separate from test-leakage)
test-baselines:
	uv run pytest tests/test_baselines.py -q
# NASS August corn production reliability replication (2015, 2025 issues) + Good-Irwin check; a data-validation gate, not a PREREG score; output kept in out/replication.txt
replication:
	$(PY) replication | tee out/replication.txt
skeleton:
	$(PY) skeleton
# F6a forecast layer + evaluation-harness tests (synthetic data only; no score on real seasons)
test-models:
	uv run pytest tests/test_models.py tests/test_evaluate.py -q
# out/forecasts.csv (+ forecasts_v2.csv, stocks_to_use.csv): one row per model x horizon x season x tau/level, NO truth column
forecasts:
	$(PY) forecasts
# Sensitivity truth: NASS Quick Stats latest national corn yield/production (bulk file, manifest-backed) + F5 replication check
quickstats:
	$(PY) quickstats
# availability counts only (scored forecast row + truth value per season, both forecast sets); no coverage, no score. Safe to run.
preflight:
	$(PY) preflight
# PREREG 4/5/10 in ONE run. DO NOT RUN before checkpoint 2 (scope cuts are logged first; fill scope_cuts.csv); refuses if PREREG.sha256 fails, a leakage test fails, forecasts are stale, or a PREREG count fails.
evaluate: forecasts
	$(PY) evaluate
# F7a: memo tables, figure and headline into out/memo/ from the finished out/eval, forecasts and stocks-to-use (read-only; no re-evaluation)
memo-assets:
	$(PY) memo
# F7b: memo assets -> render (includes, out/memo.md, out/writeup.md, out/memo.pdf) -> number trace
memo: memo-assets
	$(PY) memo-render
	$(PY) trace

# F10 optional slot (Russia wheat, PREREG-AMENDMENTS 2026-10-16). Parses the Russia row of every archived WASDE world wheat table
# into data/russia_store.csv (separate from store.csv; same release-time table via wasde_releases.csv).
russia-store:
	$(PY) russia-store
test-russia:
	uv run pytest tests/test_russia.py -q
# out/russia/forecasts.csv: M1 on the Russia print, one row per horizon x season x tau/level, NO truth column
russia-forecasts:
	$(PY) russia-forecasts
# availability counts only (origins, scored seasons per horizon, seasons with truth); no coverage, no score. Safe to run.
russia-preflight:
	$(PY) russia-preflight
# ONE run (PREREG-AMENDMENTS F10 item 7); its outputs ship in out/russia/, do not re-run. Refuses if PREREG.sha256 fails, a leakage test fails
# or out/russia/forecasts.csv is stale; prints the scored-season counts BEFORE any coverage; then writes out/russia/.
russia: russia-forecasts
	$(PY) russia

# F11: forecast-only 2026 M1 yield/production bands into out/live/ (no truth, no score; settles against the Jan 2027 Annual Summary). Safe to run.
live:
	$(PY) live
