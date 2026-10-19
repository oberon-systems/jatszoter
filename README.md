# jatszoter

jatszoter is a self-hosted, open-source training and exam stand for Linux
troubleshooting, in the spirit of [SadServers](https://sadservers.com/). An
operator starts a deployment: a VM on one of the agents, bootstrapped from a
scenario that installs, configures and then breaks something. The operator
watches the VM terminal, hands it to a trainee through a unique link, can take
control back at any moment, and a check script decides whether the trainee
fixed the problem. Every terminal session is recorded for replay.

- [Components](#components)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Development](#development)

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
| `docs/`      | project documentation                           |
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
