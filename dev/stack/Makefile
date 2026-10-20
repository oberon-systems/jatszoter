# Disposable local stand: api, web, an agent and a file server for images and bundles.

.PHONY: up down logs

# false makes the stand register agents disabled, as a production API does.
AUTO_REGISTER ?= true


up down logs:
	ROOT=$(abspath ../..) VENV=$(abspath ../../.venv) MAKE=$(MAKE) AUTO_REGISTER=$(AUTO_REGISTER) bash stand.sh $@
