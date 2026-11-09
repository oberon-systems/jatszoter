# A bare Ubuntu VM for the demo videos, and a mock of the GitHub release it installs from.

.PHONY: release release-down up down ssh reset


release release-down up down ssh reset:
	ROOT=$(abspath ../..) MAKE=$(MAKE) bash vm.sh $@
