# Run from the repository root. Uses .venv/bin/python when present, else python3.
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
CLI := $(PY) script/cli.py

.PHONY: upload upload-public list-local list-uploaded check-public delete remove-bucket

upload:
	$(CLI) upload

upload-public:
	$(CLI) upload --public

list-local:
	$(CLI) list-local

list-uploaded:
	$(CLI) list-uploaded

check-public:
	$(CLI) check-public

delete:
	$(CLI) delete-objects

remove-bucket:
	$(CLI) remove-bucket
