SMOKE_IMAGE_URL ?= https://dl.rockylinux.org/pub/rocky/9.8/images/x86_64/Rocky-9-GenericCloud-Base-9.8-20260525.0.x86_64.qcow2
SMOKE_IMAGE_SHA256 ?= 92c206cc6f790c61583247eefe87890f8828420662c17cacf247cec78ab4eec8
SMOKE_CACHE ?= $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/jatszoter/smoke

smoke:
	ROOT=$(ROOT) VENV=$(VENV) TEMP_DIR=$(TEMP_DIR) MAKE=$(MAKE) \
		SMOKE_IMAGE_URL='$(SMOKE_IMAGE_URL)' SMOKE_IMAGE_SHA256='$(SMOKE_IMAGE_SHA256)' \
		SMOKE_CACHE='$(SMOKE_CACHE)' bash smoke.sh
