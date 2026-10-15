PY = uv run python -I -m cfstore
.PHONY: store test-leakage skeleton
store:
	PYTHONPATH=src $(PY) store
test-leakage:
	uv run pytest tests/test_leakage.py tests/test_conformal.py -q
skeleton:
	PYTHONPATH=src $(PY) skeleton
