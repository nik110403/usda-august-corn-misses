PY = uv run python -I -m cfstore
.PHONY: store test-leakage test-validation test-baselines test-models replication skeleton forecasts quickstats evaluate
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
# NASS August corn production reliability replication (2015, 2025 issues) + Good-Irwin check; a data-validation gate, not a PREREG score
replication:
	$(PY) replication
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
# PREREG 4/5/10 in ONE run. DO NOT RUN before checkpoint 2 (scope cuts are logged first; fill scope_cuts.csv); refuses if PREREG.sha256 fails.
evaluate:
	$(PY) evaluate
