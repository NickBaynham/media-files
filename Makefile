# Run from the repository root. Uses .venv/bin/python when present, else python3.
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
CLI := $(PY) script/cli.py

.PHONY: upload list delete

upload:
	$(CLI) upload

list:
	$(CLI) list-local

delete:
	$(CLI) delete-objects
