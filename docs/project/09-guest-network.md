# 09 - Guest network

Every VM reaches the network through its own [passt](https://passt.top/)
process, which the agent starts before QEMU. passt keeps the guest away from
the agent host's loopback, which is why it replaced QEMU's user-mode
network; see [06](06-security.md#guest-and-host). This runbook installs a
passt that does so and checks a host before the agent runs on it.

- [Requirements](#requirements)
- [Check a host](#check-a-host)
- [Install on Ubuntu](#install-on-ubuntu)
- [Install on Rocky and RHEL](#install-on-rocky-and-rhel)
- [Uninstall](#uninstall)
- [Known issue: passt from Ubuntu 24.04](#known-issue-passt-from-ubuntu-2404)

## Requirements

- passt `2026_10_02` or newer. Older releases forward a guest packet sent
  to `127.0.0.1` to the host's loopback, which reopens what `--no-map-gw`
  closes.
- QEMU 7.2 or newer, for `-netdev stream`.
- On Ubuntu, an AppArmor profile with `flags=(attach_disconnected)` for the
  passt binary in use. Without it passt cannot accept QEMU's connection,
  and without any profile it cannot create its user namespace.
- The agent finds passt on `PATH`, or at `JATSZOTER_AGENT_PASST`.

## Check a host

Run the check as the agent's user, with the agent's env file, so it uses
the same passt and the same state directory:

```bash
JATSZOTER_AGENT_ENV_FILE=/path/to/agent.env jatszoter-agent check-network
```

It starts passt as the agent would, plays the guest and prints one line per
check. A healthy host prints the following and exits with 0:

```text
ok    passt is 2026_10_02 or newer
ok    passt starts and answers a guest
ok    DNS answers on 10.0.2.3
ok    10.0.2.2 does not reach the host loopback
ok    127.0.0.1 from the guest does not reach the host loopback
```

Any `FAIL` line exits with 1 and names what is wrong. Do not run VMs on
that host until the check passes.

## Install on Ubuntu

The Ubuntu 24.04 package is too old, see the
[known issue](#known-issue-passt-from-ubuntu-2404). Build the release from
upstream and put it in `/usr/local/bin`, out of dpkg's way and ahead of
`/usr/bin` on `PATH`.

Remove the old package, so nothing falls back to it:

```bash
sudo apt remove passt
```

Build and install the binaries:

```bash
sudo apt install git gcc make
git clone --depth 1 --branch 2026_10_02.cba3570 https://passt.top/passt
test "$(git -C passt rev-parse HEAD)" = cba357068dc586e7540971eb40bb56c5e62c4de1
make -C passt
sudo install -m 0755 passt/passt passt/passt.avx2 /usr/local/bin/
```

Install the upstream AppArmor profile, moved to `/usr/local/bin`, renamed so
it never collides with the package's files, and allowed to write in the
agent's state directories, `~/.cache/jatszoter` and
`/var/lib/jatszoter-agent`:

```bash
sed 's#/usr/bin/passt.avx2#/usr/local/bin/passt.avx2#' \
    passt/contrib/apparmor/abstractions/passt |
    sudo tee /etc/apparmor.d/abstractions/jatszoter-passt >/dev/null
sed -e 's#^profile passt /usr/bin/passt{,.avx2}#profile passt-local /usr/local/bin/passt{,.avx2}#' \
    -e 's#include <abstractions/passt>#include <abstractions/jatszoter-passt>#' \
    -e 's#^\( *\)/usr/bin/passt r,#\1/usr/local/bin/passt r,#' \
    -e 's#^}#  owner @{HOME}/.cache/jatszoter/** rw,\n  owner /var/lib/jatszoter-agent/** rw,\n}#' \
    passt/contrib/apparmor/usr.bin.passt |
    sudo tee /etc/apparmor.d/usr.local.bin.passt >/dev/null
```

Load the profile; AppArmor reloads it from `/etc/apparmor.d` at boot:

```bash
sudo apparmor_parser -r /etc/apparmor.d/usr.local.bin.passt
```

Test that it worked:

```bash
command -v passt
passt --version | head -1
sudo grep passt-local /sys/kernel/security/apparmor/profiles
```

Expected output:

```text
/usr/local/bin/passt
passt 2026_10_02.cba3570
passt-local (enforce)
```

Then run the [check](#check-a-host).

## Install on Rocky and RHEL

RHEL 9 and 10 ship passt in AppStream, confined by SELinux rather than
AppArmor. Use the package when it is new enough, and build from upstream
otherwise. Neither path has been verified on Rocky yet, so the
[check](#check-a-host) is what decides.

See which release the repositories offer; the date after `0^` is the
release:

```bash
dnf info passt | grep -E '^(Version|Release)'
```

When it is `20261002` or newer, install the package:

```bash
sudo dnf install passt
```

Otherwise build the release and install it to `/usr/local/bin`:

```bash
sudo dnf install git gcc make
git clone --depth 1 --branch 2026_10_02.cba3570 https://passt.top/passt
test "$(git -C passt rev-parse HEAD)" = cba357068dc586e7540971eb40bb56c5e62c4de1
make -C passt
sudo install -m 0755 passt/passt passt/passt.avx2 /usr/local/bin/
```

A binary in `/usr/local/bin` runs in the agent's own SELinux domain, not in
the domain the passt package defines. Test that it worked:

```bash
command -v passt
passt --version | head -1
```

Expected output, for the upstream build:

```text
/usr/local/bin/passt
passt 2026_10_02.cba3570
```

Then run the [check](#check-a-host).

## Uninstall

Stop every VM on the host first: each one runs its own passt.

On Ubuntu, unload the profile, remove it with its abstraction and the
binaries, then purge what `apt remove` left of the old package, its
AppArmor profile included:

```bash
sudo apparmor_parser -R /etc/apparmor.d/usr.local.bin.passt
sudo rm -f /etc/apparmor.d/usr.local.bin.passt /etc/apparmor.d/abstractions/jatszoter-passt
sudo rm -f /usr/local/bin/passt /usr/local/bin/passt.avx2
sudo apt purge passt
```

On Rocky and RHEL, remove the package or the upstream build, whichever you
installed:

```bash
sudo dnf remove passt
sudo rm -f /usr/local/bin/passt /usr/local/bin/passt.avx2
```

Test that it worked:

```bash
command -v passt || echo "passt is gone"
sudo grep passt-local /sys/kernel/security/apparmor/profiles || echo "profile is gone"
```

Expected output, on Ubuntu:

```text
passt is gone
profile is gone
```

On Rocky and RHEL, without AppArmor, the second line is `profile is gone`
as well, or an error that the file does not exist.

## Known issue: passt from Ubuntu 24.04

Symptom: the VM boots and its guest agent answers, but the guest has no
network. The bootstrap fails at the first step that needs a package, for
example `failed configure 1`. `check-network` prints
`FAIL  passt starts and answers a guest`.

Trigger: the Ubuntu 24.04 package `passt 0.0~git20240220.1e6f92b-1`,
which prints `passt unknown version`.

Blast radius:

- Its AppArmor profile lacks `flags=(attach_disconnected)`. passt binds its
  socket, then refuses QEMU's connection with `EACCES`, so no VM on the
  host has a network.
- With that flag added, the network comes up but is not safe. A guest
  packet to `127.0.0.1` reaches the host's loopback: a trainee with root
  enables `route_localnet` and reaches every loopback service of the host.
- With `--no-map-gw` it also drops a loopback resolver such as
  `127.0.0.53`, so the guest has no DNS.

Adding the flag to `/etc/apparmor.d/usr.bin.passt` is not a fix. Install a
release from upstream instead: [Install on Ubuntu](#install-on-ubuntu).
