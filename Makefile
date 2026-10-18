# Developer entry points for jatszoter.

PROJECT := jatszoter
VENV    := .venv
PIP     := $(VENV)/bin/pip

# lint reads NUL-separated names with `read -d`, which dash's read lacks.
SHELL := /bin/bash

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

.DEFAULT_GOAL := shell
.PHONY: install shell test test-api test-web test-agent smoke lint lint-all run-api run-web \
	stand-up stand-down stand-logs


# common targets
# The last line puts the terminal back: the installers draw progress bars and
# leave it without echo or cursor when they are done.
install:
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/pre-commit install
	@stty sane 2> /dev/null; tput cnorm 2> /dev/null; true


# tests
test test-api test-web test-agent smoke:
	$(MAKE) -C make/tests $@


# linters
# lint checks what differs from HEAD plus untracked files; lint-all checks the tree.
# Deleted files are dropped, hooks cannot open them.
lint:
	@{ git diff -z --name-only --diff-filter=d HEAD; git ls-files -z --others --exclude-standard; } | \
	xargs -0 -r $(VENV)/bin/pre-commit run --files

lint-all:
	@git ls-files -z --cached --others --exclude-standard | \
	while IFS= read -r -d '' f; do [ -e "$$f" ] && printf '%s\0' "$$f"; done | \
	xargs -0 $(VENV)/bin/pre-commit run --files

# runs
run-api:
	$(VENV)/bin/uvicorn --factory jatszoter_api.app:create_app --host 127.0.0.1 --port 8080

run-web:
	$(VENV)/bin/uvicorn --factory jatszoter_web.app:create_app --host 127.0.0.1 --port 8000

stand-up stand-down stand-logs:
	$(MAKE) -C dev/stack $(@:stand-%=%)


# defaults
shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i || true
