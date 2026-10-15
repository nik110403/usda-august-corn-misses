PY = uv run python -I -m cfstore
.PHONY: store test-leakage test-validation test-baselines replication skeleton
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
