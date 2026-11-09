# 11 - Install

A jatszoter release, tagged `X.Y.Z`, is the whole stack as one version: a
compose file with Postgres, Valkey, the SeaweedFS filer, `api` and `web`, the
two images pinned by digest, and two installers, `install-stack.sh` and
`install-agent.sh`, which know the files and the agent of that release. It is
made of the newest `api-`, `web-` and `agent-` releases of its major. This
page installs, upgrades, backs up and removes a stack and its agents.

- [Install the stack](#install-the-stack)
- [Install an agent](#install-an-agent)
- [Install from a checkout](#install-from-a-checkout)
- [Upgrade](#upgrade)
- [Back up](#back-up)
- [Uninstall](#uninstall)
- [Check a host](#check-a-host)

## Install the stack

The stack needs [Docker Engine](https://docs.docker.com/engine/install/) with
the compose plugin, `curl` and `openssl`. Run the installer as a user that
can use docker:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh
```

It asks for the layout, the address and ports to publish and whether new
agents need approval; a flag answers a question up front. The doctor then
checks docker, compose, openssl and the ports, one line each, and a single
`[FAIL]` stops the install. Next it downloads `compose.yaml` and
`env.example`, checks them against `SHA256SUMS`, writes `.env` with every
secret generated, and runs `docker compose up -d --wait`.

| Layout   | Directory, with `compose.yaml` and `.env` | Needs                         |
| -------- | ----------------------------------------- | ----------------------------- |
| `xdg`    | `~/.local/share/jatszoter/stack`          | nothing                       |
| `system` | `/opt/jatszoter/stack`, owned by you      | sudo once, to create the dir |

| Flag                                    | Meaning                                         |
| --------------------------------------- | ----------------------------------------------- |
| `--release X.Y.Z`                       | install another release than the latest         |
| `--layout xdg\|system`                  | where the stack lives                           |
| `--bind ADDR`                           | address both ports are published on, 127.0.0.1 |
| `--api-port N`, `--web-port N`          | the two ports, 8080 and 8000 by default         |
| `--auto-register`, `--no-auto-register` | take new agents in without approval; no        |
| `--yes`                                 | never ask: take the defaults                    |

Open `http://localhost:8000/login` on the stack host and log in as `admin`
with the password `admin`. The web UI asks for a new password at once.

- Keep `.env` safe: without its master keys no credential the database holds
  verifies again.
- Agents on other hosts and browsers elsewhere reach the stack through a
  reverse proxy with TLS, see [10](10-reverse-proxy.md). `web` sets its
  cookies `Secure`, so plain HTTP works on `localhost` only.
- The API counts login failures and agent registrations per client address.
  Without a proxy every browser reaches `web` from the gateway of the
  stack's network, so all of them count as one address.
- `JATSZOTER_SUBNET` in `.env` is the stack's own network. Change it, and
  `JATSZOTER_WEB_FORWARDED_ALLOW_IPS` with it, when the subnet is taken on
  the host.

## Install an agent

An agent host needs Linux on x86_64 with KVM, QEMU 7.2 or newer, `qemu-img`,
`xorriso`, OVMF and passt `2026_10_02` or newer with its AppArmor profile. The
passt of Debian and Ubuntu is older, so the installer offers to build that
release into `/usr/local/bin`, as [09](09-guest-network.md) does by hand; the
build needs `git`, `gcc` and `make`. On Debian or Ubuntu:

```bash
sudo apt install qemu-system-x86 qemu-utils xorriso ovmf git gcc make
```

Run the installer as the user the agent runs as, never as root:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --api-url https://api.example.com
```

It asks for what no flag gave. The doctor then checks the host, one line
each, and a single `[FAIL]` stops the install. Next it downloads the agent
binary, checks it against `SHA256SUMS`, writes `agent.env` and runs
`jatszoter-agent check-network`. A guest network that is not safe ends the
install there, with no unit installed. Otherwise it installs and starts the
systemd unit `jatszoter-agent.service`.

| Layout   | Binaries                                                     | `agent.env`                      | State                      | Unit                     |
| -------- | ------------------------------------------------------------ | -------------------------------- | -------------------------- | ------------------------ |
| `xdg`    | `~/.local/share/jatszoter/bin`                               | `~/.config/jatszoter/agent.env`  | `~/.cache/jatszoter/agent` | user unit                |
| `system` | `/opt/jatszoter/agent/bin`, `/usr/local/bin/jatszoter-agent` | `/opt/jatszoter/agent/agent.env` | `/var/lib/jatszoter-agent` | system unit, `User=` you |

| Flag                         | Meaning                                              |
| ---------------------------- | ---------------------------------------------------- |
| `--release X.Y.Z`            | install another release than the latest              |
| `--layout xdg\|system`       | where the agent lives; `system` needs sudo           |
| `--api-url URL`              | the API the agent leases from                        |
| `--name NAME`                | the agent's name, the host name by default           |
| `--capacity N`               | VMs at once, 1 by default                            |
| `--labels A,B`               | labels deployments can be pinned by                  |
| `--passt`, `--no-passt`      | build passt, asked when it is missing or older       |
| `--systemd`, `--no-systemd`  | install the unit, yes by default                     |
| `--linger`, `--no-linger`    | keep a user unit running after you log out           |
| `--yes`                      | never ask: a value it lacks is an error              |

Settings the installer does not manage, such as `JATSZOTER_AGENT_QEMU` or
`JATSZOTER_AGENT_OVMF_CODE` for the paths of another distribution, can be
added to `agent.env` by hand. The doctor checks those paths, and an upgrade
keeps them.

The new agent waits for an operator to approve it on the Agents page of the
web UI. A stack installed with `--auto-register` takes it in at once.

## Install from a checkout

Both installers take `--from-source` when they run from a clone of the
repository. `install-stack.sh` then builds the `api` and `web` images of the
checkout with docker, and `install-agent.sh` builds the agent with
`cargo build --release --locked`, which needs [Rust](https://rustup.rs).
Everything else is the same, the doctor, uninstall and upgrade included:

```bash
sh release/install-stack.sh --from-source
sh release/install-agent.sh --from-source --api-url http://127.0.0.1:8080
```

Pass `--from-source` to `doctor` and `uninstall` as well: without it, or
`--release`, an installer asks GitHub for the latest release first.

## Upgrade

Every release of a new major, `N.0.0`, says in its notes what it breaks; read
them first. Then run both install commands again. The stack installer finds
the layout, replaces `compose.yaml`, keeps `.env` and restarts the stack; the
API migrates the database on start. The agent installer finds the layout and
the settings, installs the new binary next to the old ones, moves the link
and restarts the unit:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh
```

A patch of an older major comes from its `N.x.x-stable` branch and is never
`releases/latest`. Pass `--release X.Y.Z` to both installers for it.

## Back up

The stack keeps its state in two volumes: `jatszoter_db` holds the database,
`jatszoter_seaweedfs` the scenario bundles, console logs and recordings.
Valkey keeps nothing worth saving. Stop the stack, archive both, start it
again, from its directory, `~/.local/share/jatszoter/stack` or
`/opt/jatszoter/stack`:

```bash
cd ~/.local/share/jatszoter/stack
docker compose stop
for volume in jatszoter_db jatszoter_seaweedfs; do
    docker run --rm -v "$volume:/data:ro" -v "$PWD:/backup" alpine \
        tar -czf "/backup/$volume.tar.gz" -C /data .
done
docker compose up -d --wait
```

Keep `.env` with the archives: without its master keys no credential the
database holds verifies again.

## Uninstall

`uninstall` removes everything an installer put on the host. It lists what
goes and asks first; `--force` removes without asking. For the stack that is
its containers, images and volumes, with every scenario, recording and user,
and its directory with `.env`:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh -s -- uninstall
```

For an agent it is the unit, the link, the binaries, `agent.env`, the state
and the passt it built, with its AppArmor profile:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- uninstall
```

The API keeps the record of a removed agent. A user unit leaves linger on;
`loginctl disable-linger` turns it off.

## Check a host

Each installer runs its doctor before it installs anything, and `doctor`
runs it alone:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh -s -- doctor
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- doctor
```

Every check prints one line, and a failed one says how to fix it:

```text
install-agent: doctor:
  [ok]   Linux on x86_64
  [ok]   runs as alpha, not root
  [FAIL] no read and write access to /dev/kvm
         fix: enable KVM and add alpha to the kvm group
install-agent: doctor: 1 problem(s)
```

The exit status is 1 while any check fails. Arguments reach a piped script
only after `sh -s --`.
