# Video 1 - Install

The first video installs the stack and an agent on a bare Ubuntu 26.04
server, with the commands a user runs after the release. The part before the
recording runs on the host, from the root of the checkout; the recorded part
runs in the VM.

---

## Before recording

```shell
# Stop the dev stand: the ssh tunnel to the VM needs ports 8000 and 8080.
make -C dev/stack down

# Build release 1.0.0 from this tree and serve it as github.com and ghcr.io.
make -C dev/vm release

# Start a bare Ubuntu 26.04 VM, wiping the one of the last take.
make -C dev/vm reset up

# Log in to the VM; ports 8000 and 8080 come through to this host.
make -C dev/vm ssh

# Start the take from an empty screen.
clear
```

---

## Recording

**Start recording here.**

```shell
# A bare Ubuntu 26.04 server: refresh its package lists first.
sudo apt-get update

# Docker for the stack; QEMU, OVMF, xorriso for the agent; git, gcc, make for passt.
sudo apt-get install -y docker.io docker-compose-v2 qemu-system-x86 qemu-utils xorriso ovmf git gcc make

# Let this user run Docker and KVM.
sudo usermod -aG docker,kvm "$USER"

# Start a new login shell, so the new groups apply.
exec sudo -iu "$USER"

# The groups now include docker and kvm.
id

# Install the stack into /opt: answer system, then Enter for the rest.
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh

# The stack runs: Postgres, Valkey, SeaweedFS, api and web.
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Install the agent into /opt: answer system, then Enter for the rest.
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --api-url http://127.0.0.1:8080

# The agent runs as a system unit and waits for an operator to approve it.
systemctl status jatszoter-agent --no-pager
```

**Stop recording here.**
