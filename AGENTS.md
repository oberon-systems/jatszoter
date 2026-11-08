# AGENTS.md

## Mission

jatszoter is a training and exam stand. It boots disposable VMs from
scenarios, lets a trainee repair them through a shared web terminal, records
the terminal and checks the result.

Primary invariant:

> A trainee reaches only the deployment their link was issued for, and only
> while that link is valid and the operator has not taken control.

jatszoter is not a security boundary against a hostile guest. Strong VM
isolation is a non-goal; correctness of access to deployments is a goal.

## Principles

1. The API owns desired and persistent state; the agent reconciles actual
   state with it.
2. Links are credentials: random, stored hashed, scoped to one deployment,
   revocable, and revoked when the deployment ends.
3. Take control is enforced in the API, not in the browser.
4. Check scripts never live on the VM between checks.
5. Every recording belongs to exactly one trainee link.
6. The Web UI is designed in Penpot and approved before it is built.
7. Failures leave a deployment in a terminal state, never half-running.

## Deployment model

A Deployment is the primary runtime entity; the VM is an implementation
detail. Identifiers: `agent_id`, `lease_id`, `scenario_id`, `deployment_id`,
`vm_id`, `link_id`, `recording_id`, `check_id`.

Lifecycle:
PENDING -> STARTING -> BOOTSTRAPPING -> READY -> STOPPING -> COMPLETED

Any non-terminal state may move to FAILED. The operator stops a STARTING,
BOOTSTRAPPING or READY deployment: it moves to STOPPING. Stopping a PENDING
deployment moves it to CANCELLED. A READY deployment past its session TTL
moves to STOPPING with reason `expired`. COMPLETED, FAILED and CANCELLED are
terminal.

## Architecture

Web -> API -> Agent -> QEMU + cloud-init + qemu-guest-agent -> VM.

The agent registers, keeps a lease, receives deployments, creates and
destroys VMs, bootstraps them, runs checks, ships consoles and reconciles
after restart. It never invents deployments or keeps VMs the API did not ask
for.

## Development behavior

Before coding:

1. read the relevant docs in `docs/`;
2. name the component that owns the change;
3. define failure behavior;
4. define tests, positive and negative.

Implement the smallest scoped change. Do not silently redesign the
architecture; propose the change in `docs/` first.

Never implement host execution as `/bin/sh -c <scenario or trainee input>`.
Never log tokens or links.

## Branches and releases

The model in [CONTRIBUTING.md](CONTRIBUTING.md#branches-and-releases) binds
from the moment tag `1.0.0` exists (`git tag -l 1.0.0`). Before that, work
stays on `main`.

- Work happens on `dev`, and pull requests target `dev`. Never commit or push
  to `main`; when the current branch is `main`, stop and ask the user.
- `main` moves only at a release: `dev` is merged with a merge commit, then
  the bump and the tag land on `main`, then `dev` is fast-forwarded to
  `main`. Never rebase or force-push `main`, `dev` or a stable branch.
- Minor and patch releases come straight from `main`. Only a major first
  branches `N.x.x-stable` off `main`.
- A patch to an older major is made on its `N.x.x-stable` branch and tagged
  there.
- The packages `api-`, `web-` and `agent-X.Y.Z` keep their own versions, but
  their major follows the release major.
- Releases, bumps and tags happen only when the user asks for them.
