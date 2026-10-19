# jatszoter-agent

The host daemon of jatszoter, a self-hosted training and exam stand for
Linux troubleshooting. It leases deployments from the API, runs each one in
a [QEMU](https://www.qemu.org/) VM bootstrapped with
[cloud-init](https://cloud-init.io/), and ships the VM consoles back.

## Install

The host needs QEMU and KVM.

```bash
cargo install jatszoter-agent
```

A prebuilt `x86_64-linux-gnu` binary and its `SHA256SUMS` are attached to
every `agent-X.Y.Z` release on GitHub.

## Run

```bash
export JATSZOTER_AGENT_API_URL="http://192.0.2.10:8080"
export JATSZOTER_AGENT_NAME=alpha
export JATSZOTER_AGENT_STATE_DIR=/var/lib/jatszoter-agent
jatszoter-agent
```

The architecture and every setting are described in `docs/project/` of the
repository.
