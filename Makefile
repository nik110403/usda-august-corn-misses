PY = uv run python -I -m cfstore
.PHONY: store test-leakage skeleton
store:
	$(PY) store
test-leakage:
	uv run pytest tests/test_leakage.py tests/test_conformal.py tests/test_skeleton.py -q
skeleton:
	$(PY) skeleton
