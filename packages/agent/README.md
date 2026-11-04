# jatszoter-agent

The host daemon of jatszoter, a self-hosted training and exam stand for
Linux troubleshooting. It leases deployments from the API, runs each one in
a [QEMU](https://www.qemu.org/) VM bootstrapped with
[cloud-init](https://cloud-init.io/), and ships the VM consoles back.

## Requirements

The agent runs as an unprivileged user and spawns these host tools:

| Tool                 | Used for                              | Setting                     |
| -------------------- | ------------------------------------- | --------------------------- |
| `qemu-system-x86_64` | the VMs, QEMU 7.2 or newer            | `JATSZOTER_AGENT_QEMU`      |
| `qemu-img`           | the overlay disk over the base image  | `JATSZOTER_AGENT_QEMU_IMG`  |
| `xorriso`            | the cloud-init seed ISO               | `JATSZOTER_AGENT_XORRISO`   |
| `passt`              | the guest network, `2026_10_02`+      | `JATSZOTER_AGENT_PASST`     |
| OVMF                 | UEFI firmware the VMs boot through    | `JATSZOTER_AGENT_OVMF_CODE` |

KVM is required: without a usable `/dev/kvm` the agent refuses to start a
VM. Give the agent's user access to `/dev/kvm`, usually through the `kvm`
group. On a dev stand without KVM, set `JATSZOTER_AGENT_ALLOW_TCG=true` to
run the VMs under TCG, many times slower and not a security boundary.

On Debian or Ubuntu:

```bash
sudo apt install qemu-system-x86 qemu-utils xorriso ovmf
```

On Rocky, Alma or RHEL 9:

```bash
sudo dnf install qemu-kvm qemu-img xorriso edk2-ovmf
```

The RHEL family ships QEMU as `/usr/libexec/qemu-kvm` and OVMF under
`/usr/share/edk2/ovmf/`, so point `JATSZOTER_AGENT_QEMU`,
`JATSZOTER_AGENT_OVMF_CODE` and `JATSZOTER_AGENT_OVMF_VARS` there.

### passt

[passt](https://passt.top/) gives each guest outbound NAT without root and
keeps it away from the host's loopback. The release in Ubuntu 24.04 is too
old and unsafe, so passt is installed by hand: follow
`docs/project/09-guest-network.md` of the repository. Then check the host
before the agent runs on it:

```bash
jatszoter-agent check-network
```

## Install

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
