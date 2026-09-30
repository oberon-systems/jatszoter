ROOT     := $(abspath ../..)
VENV     := $(ROOT)/.venv
TEMP_DIR := $(shell mktemp -dut jatszoter-XXXXX$$(date +%s))

include *.mk

.DEFAULT_GOAL := test
.PHONY: test test-api test-web test-agent smoke

test:
	cd $(ROOT) && $(VENV)/bin/pytest -q
	cd $(ROOT) && cargo test --workspace

test-api:
	cd $(ROOT) && $(VENV)/bin/pytest -q packages/api

test-web:
	cd $(ROOT) && $(VENV)/bin/pytest -q packages/web

test-agent:
	cd $(ROOT) && cargo test -p jatszoter-agent
