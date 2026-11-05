# Developer entry points for jatszoter.

PROJECT := jatszoter
VENV    := .venv
PIP     := $(VENV)/bin/pip

IMAGE_PREFIX ?= $(PROJECT)
IMAGE_TAG    ?= dev

# lint reads NUL-separated names with `read -d`, which dash's read lacks.
SHELL := /bin/bash

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

.DEFAULT_GOAL := shell
.PHONY: install lock shell test test-api test-web test-agent smoke lint lint-all run-api run-web vendor images


# common targets
# The last line puts the terminal back: the installers draw progress bars and
# leave it without echo or cursor when they are done.
install:
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/pre-commit install
	@stty sane 2> /dev/null; tput cnorm 2> /dev/null; true

# Rewrites what the images install, under the Python of the images. LOCK_ARGS=--upgrade moves versions.
lock:
	@for dir in packages/api packages/web; do for name in requirements requirements-build; do \
		(cd $$dir && uvx --python 3.15 --from pip-tools==7.6.2 pip-compile --quiet $(LOCK_ARGS) \
			--generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
			--output-file=$$name.txt $$name.in) || exit 1; \
	done; done


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


# vendored browser code and fonts, versions from packages/web/vendor/package-lock.json
vendor:
	cd packages/web/vendor && npm ci --ignore-scripts --no-audit --no-fund && python3 sync.py


# images, as CI builds them; the revision label gets -dirty when the tree differs from HEAD.
images:
	@rev=$$(git rev-parse HEAD); git diff --quiet HEAD || rev=$$rev-dirty; \
	for pkg in api web; do \
		docker build -f docker/$$pkg/Dockerfile -t $(IMAGE_PREFIX)-$$pkg:$(IMAGE_TAG) \
			--label org.opencontainers.image.revision=$$rev . || exit 1; \
	done


# defaults
shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i || true
