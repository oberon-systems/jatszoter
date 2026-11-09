# 11 - Install

A jatszoter release, tagged `X.Y.Z`, is the whole stack as one version: a
compose file with Postgres, Valkey, the SeaweedFS filer, `api` and `web`, the
two images pinned by digest, and `install-agent.sh`, which knows the agent of
that release. It is made of the newest `api-`, `web-` and `agent-` releases
of its major. This page installs, upgrades, backs up and removes a stack and
its agents.

- [Install the stack](#install-the-stack)
- [Install an agent](#install-an-agent)
- [Upgrade](#upgrade)
- [Back up](#back-up)
- [Uninstall](#uninstall)
- [Check a host](#check-a-host)

## Install the stack

The stack needs [Docker Engine](https://docs.docker.com/engine/install/) with
the compose plugin. Download the files of the latest release into a directory
of their own and check them:

```bash
mkdir jatszoter && cd jatszoter
base=https://github.com/oberon-systems/jatszoter/releases/latest/download
curl -fsSLO "$base/compose.yaml" -O "$base/env.example" -O "$base/SHA256SUMS"
sha256sum -c --ignore-missing SHA256SUMS
cp env.example .env
chmod 600 .env
```

Fill in `.env`. Every secret is empty in `env.example`, and compose refuses
to start until each one is set. The comments above each value give the
command that generates it:

| Value                        | Generate with                  |
| ---------------------------- | ------------------------------ |
| `JATSZOTER_DB_PASSWORD`      | `openssl rand -hex 24`         |
| `JATSZOTER_VALKEY_PASSWORD`  | `openssl rand -hex 24`         |
| `JATSZOTER_SEAWEED_JWT_KEY`  | `openssl rand -hex 32`         |
| `JATSZOTER_MASTER_KEYS`      | `openssl rand -hex 32`, as `{"k1": "<key>"}` |
| `JATSZOTER_WEB_COOKIE_KEYS`  | 32 random bytes as base64url, as `{"c1": "<key>"}` |

Start the stack and wait until every service is healthy:

```bash
docker compose up -d --wait
```

Open `http://localhost:8000/login` on the stack host and log in as `admin`
with the password `admin`. The web UI asks for a new password at once.

- Both ports are published on `127.0.0.1`. Agents on other hosts and
  browsers elsewhere reach the stack through a reverse proxy with TLS, see
  [10](10-reverse-proxy.md). `web` sets its cookies `Secure`, so plain HTTP
  works on `localhost` only.
- The API counts login failures and agent registrations per client address.
  Without a proxy every browser reaches `web` from the gateway of the
  stack's network, so all of them count as one address.
- `JATSZOTER_SUBNET` is the stack's own network. Change it, and
  `JATSZOTER_WEB_FORWARDED_ALLOW_IPS` with it, when the subnet is taken on
  the host.

## Install an agent

An agent host needs Linux on x86_64 with KVM, QEMU 7.2 or newer, `qemu-img`,
`xorriso`, OVMF and passt `2026_10_02` or newer with its AppArmor profile, as
[09](09-guest-network.md) sets up. On Debian or Ubuntu:

```bash
sudo apt install qemu-system-x86 qemu-utils xorriso ovmf
```

Run the installer as the user the agent runs as, never as root:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --api-url https://api.example.com
```

It checks the host first and stops on any problem, then downloads the agent
binary, checks it against `SHA256SUMS`, writes `agent.env` and runs
`jatszoter-agent check-network`. A guest network that is not safe ends the
install there, with no unit installed. Otherwise it installs and starts the
systemd unit `jatszoter-agent.service`.

| Layout   | Binaries                                       | `agent.env`                      | State                     | Unit                     |
| -------- | ---------------------------------------------- | -------------------------------- | ------------------------- | ------------------------ |
| `xdg`    | `~/.local/share/jatszoter/bin`                 | `~/.config/jatszoter/agent.env`  | `~/.cache/jatszoter`      | user unit                |
| `system` | `/opt/jatszoter/agent/bin`, `/usr/local/bin/jatszoter-agent` | `/opt/jatszoter/agent/agent.env` | `/var/lib/jatszoter-agent` | system unit, `User=` you |

| Flag                         | Meaning                                              |
| ---------------------------- | ---------------------------------------------------- |
| `--release X.Y.Z`            | install another release than the latest              |
| `--layout xdg\|system`       | where the agent lives; `system` needs sudo           |
| `--api-url URL`              | the API the agent leases from                        |
| `--name NAME`                | the agent's name, the host name by default           |
| `--capacity N`               | VMs at once, 1 by default                            |
| `--labels A,B`               | labels deployments can be pinned by                  |
| `--systemd`, `--no-systemd`  | install the unit, yes by default                     |
| `--linger`, `--no-linger`    | keep a user unit running after you log out           |
| `--yes`                      | never ask: a value it lacks is an error              |

Settings the installer does not manage, such as `JATSZOTER_AGENT_QEMU` or
`JATSZOTER_AGENT_OVMF_CODE` for the paths of another distribution, can be
added to `agent.env` by hand. The doctor checks those paths, and an upgrade
keeps them.

The new agent waits for an operator to approve it on the Agents page of the
web UI. A stack started with `JATSZOTER_AUTO_REGISTER_AGENTS=true` takes it
in at once.

## Upgrade

Every release of a new major, `N.0.0`, says in its notes what it breaks; read
them first. For the stack, replace `compose.yaml`, keep `.env` and start it
again. The API migrates the database on start:

```bash
base=https://github.com/oberon-systems/jatszoter/releases/latest/download
curl -fsSLO "$base/compose.yaml" -O "$base/SHA256SUMS"
sha256sum -c --ignore-missing SHA256SUMS
docker compose up -d --wait
```

For an agent, run the same install command again. It finds the layout and
the settings, installs the new binary next to the old ones, moves the link
and restarts the unit:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh
```

A patch of an older major comes from its `N.x.x-stable` branch and is never
`releases/latest`. Take its files from `releases/download/X.Y.Z` instead,
and pass `--release X.Y.Z` to the installer.

## Back up

The stack keeps its state in two volumes: `jatszoter_db` holds the database,
`jatszoter_seaweedfs` the scenario bundles, console logs and recordings.
Valkey keeps nothing worth saving. Stop the stack, archive both, start it
again:

```bash
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

The stack goes with its containers, volumes and images. This deletes every
scenario, recording and user:

```bash
docker compose down --volumes --rmi all
```

`uninstall` removes everything the agent installer put on the host: the
unit, the link, the binaries, `agent.env` and the state. It lists what goes
and asks first; `--force` removes without asking:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- uninstall
```

The API keeps the record of a removed agent. A user unit leaves linger on;
`loginctl disable-linger` turns it off.

## Check a host

On the stack host, every service shows `healthy`:

```bash
docker compose ps
```

On an agent host, the installer's doctor checks the tools, KVM and the API:

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- doctor
```

Expected on a ready host:

```text
install-agent: doctor: the host can run the agent
```

Otherwise each problem is listed with its fix, and the exit status is 1.
Arguments reach a piped script only after `sh -s --`.
