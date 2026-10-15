PY = uv run python -I -m cfstore
.PHONY: store test-leakage test-validation skeleton
# rebuilds data/{listing,store,wasde_releases,txt_selection}.csv (both eras) from the manifest-backed raw files
store:
	$(PY) store
test-leakage:
	uv run pytest tests/test_leakage.py tests/test_conformal.py tests/test_skeleton.py -q
test-validation:
	uv run pytest tests/test_validation.py -q -s
skeleton:
	$(PY) skeleton
