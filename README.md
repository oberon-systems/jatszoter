<p align="center"><img src="docs/project/img/logo.svg" alt="jatszoter logo" width="96"></p>

# jatszoter

jatszoter is a self-hosted, open-source training and exam stand for Linux
troubleshooting, in the spirit of [SadServers](https://sadservers.com/). An
operator starts a deployment: a VM on one of the agents, bootstrapped from a
scenario that installs, configures and then breaks something. The operator
watches the VM terminal, hands it to a trainee through a unique link, can take
control back at any moment, and a check script decides whether the trainee
fixed the problem. Every terminal session is recorded for replay.

- [Demo](#demo)
- [Install](#install)
- [Components](#components)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Development](#development)
- [Contributing](#contributing)

## Demo

[![Jatszoter Demo #2](https://img.youtube.com/vi/1Lx9MX-OTjM/maxresdefault.jpg)](https://youtu.be/1Lx9MX-OTjM)

[Demo #2](https://youtu.be/1Lx9MX-OTjM) installs jatszoter on a bare Ubuntu
server: the packages, the stack with `install-stack.sh` and the agent with
`install-agent.sh`, which then waits for an operator to approve it.

[![Jatszoter Demo #3](https://img.youtube.com/vi/9ZFgtnBvWYM/maxresdefault.jpg)](https://youtu.be/9ZFgtnBvWYM)

[Demo #3](https://youtu.be/9ZFgtnBvWYM) sets the stand up in the web UI: the
first login, the agent's approval, a trainee, the Rocky Linux 9 base image and
the `nginx-502` scenario uploaded as a bundle.

[![Jatszoter Demo #4](https://img.youtube.com/vi/5a0pXOhpPdM/maxresdefault.jpg)](https://youtu.be/5a0pXOhpPdM)

[Demo #4](https://youtu.be/5a0pXOhpPdM) walks one full cycle on the
`nginx-502` scenario: a deployment and its trainee link, the trainee fixes the
502 in the terminal and the check passes, then the operator revokes the link,
stops the deployment, replays the session and purges its logs.

## Install

A jatszoter release installs with two scripts: the stack with docker compose
on one host, an agent on every host that runs VMs. Each checks the host
first, asks for what no flag gave, and has `doctor` and `uninstall`.
[docs/project/11-install.md](docs/project/11-install.md) covers the layouts,
the flags, upgrades, backups and installing from a checkout.

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh
```

```bash
curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --api-url https://api.example.com
```

## Components

- `packages/api` - the control plane, [FastAPI](https://fastapi.tiangolo.com/)
  over [SQLModel](https://sqlmodel.tiangolo.com/): agents, leases, scenarios,
  deployments, links, recordings and checks.
- `packages/agent` - the host daemon in Rust: leases deployments, runs up to N
  [QEMU](https://www.qemu.org/) VMs, bootstraps them with
  [cloud-init](https://cloud-init.io/) and ships their consoles.
- `packages/web` - the operator and trainee UI: FastAPI, Jinja2,
  [HTMX](https://htmx.org/) and [xterm.js](https://xtermjs.org/).

How they fit together is in [docs/project/01-architecture.md](docs/project/01-architecture.md).

## Repository layout

| Path         | What it holds                                   |
| ------------ | ----------------------------------------------- |
| `packages/`  | api, agent, web and shared Python code          |
| `scenarios/` | scenarios, one directory each                   |
| `docs/`      | project documentation and its GitHub Pages site |
| `docker/`    | container images of api and web                 |
| `prompts/`   | step-by-step implementation prompts             |
| `dev/web/`   | Penpot mockups of the Web UI                    |

## Requirements

On a Debian or Ubuntu workstation:

```bash
sudo apt install python3 python3-venv make docker.io docker-compose-v2 shellcheck
```

Rust comes from [rustup](https://rustup.rs/); `cargo fmt` and `cargo clippy`
run as pre-commit hooks. [hadolint](https://github.com/hadolint/hadolint) is
a single binary on `PATH`.

## Development

```bash
make install
make shell
make lint
```

`make install` builds `.venv`, installs the pinned tools and the git hooks.
`make shell` opens a shell inside that venv. `make lint` runs every
pre-commit hook over the tree.

The Web UI is designed before it is built: see [dev/web/README.md](dev/web/README.md).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before you open a pull request, and
follow the [Code of Conduct](CODE_OF_CONDUCT.md). Vulnerabilities go through
[SECURITY.md](SECURITY.md), never through a public issue.
