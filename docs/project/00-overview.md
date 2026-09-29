# 00 - Overview

jatszoter is a self-hosted training and exam stand for Linux troubleshooting.
It boots a disposable VM from a scenario that breaks something on purpose,
hands the VM terminal to a trainee through a link, and lets a check script
decide whether the trainee fixed it.

- [Why](#why)
- [Goals](#goals)
- [Non-goals](#non-goals)
- [Roles](#roles)
- [How it works](#how-it-works)
- [Glossary](#glossary)
- [Acceptance criteria](#acceptance-criteria)

## Why

Hosted services such as [SadServers](https://sadservers.com/) show that a
broken server plus a check script is a good way to teach and to test
troubleshooting. They run someone else's scenarios on someone else's
machines. jatszoter runs your own scenarios on your own hosts, and puts an
operator next to the trainee: the operator watches live, takes the keyboard
when needed and replays the recording later.

## Goals

- An operator starts a deployment from a scenario in a few clicks and gets a
  ready, broken VM within the scenario's bootstrap timeout.
- A trainee needs nothing but a browser and a link.
- The operator can take control and give it back at any moment, and the
  trainee cannot type while the operator holds it.
- The check result is decided by the scenario's own script, not by a person.
- Every trainee link's terminal is recorded and can be replayed.
- A scenario is a directory of plain files that lives in git and is
  validated before it is registered.
- One agent host runs several VMs at once; more hosts add capacity.

## Non-goals

- Strong isolation against a hostile guest. The VM is a training box, not a
  sandbox for untrusted code; see [06](06-security.md).
- Hiding the scenario from a determined trainee with root inside the VM.
- Multi-tenant hosting, billing or public sign-up.
- A scenario marketplace or authoring UI; scenarios are edited as files.
- Graphical desktops; the only interface to the VM is a text terminal.

## Roles

| Role     | How it authenticates | What it can do                                |
| -------- | -------------------- | --------------------------------------------- |
| Operator | operator token       | everything: agents, scenarios, deployments    |
| Trainee  | trainee link         | one deployment's terminal and its check       |
| Viewer   | viewer link          | watches one deployment's terminal             |
| Author   | git                  | writes scenarios, registers them via the API  |
| Agent    | signed requests      | its lease, its deployments, their consoles    |

## How it works

1. The author registers a scenario from `scenarios/<id>/`.
2. The operator creates a deployment: scenario plus, optionally, an agent.
3. The agent boots a VM from the scenario image and bootstraps it with
   [cloud-init](https://cloud-init.io/): packages, install, configure,
   break.
4. The deployment turns READY. The operator opens the terminal and hands
   the trainee link out; it was issued when the deployment was created.
5. The trainee opens the link, repairs the VM and runs the check.
6. The operator stops the deployment; the VM is destroyed, the recording and
   checks stay.

## Glossary

| Term       | Meaning                                                      |
| ---------- | ------------------------------------------------------------ |
| Scenario   | a versioned task: image, bootstrap steps, check, hints       |
| Deployment | one VM started from one scenario version on one agent        |
| Agent      | host daemon that runs VMs for the API                        |
| Lease      | the agent's time-limited right to hold deployments           |
| Link       | a credential that opens one deployment: trainee, operator,   |
|            | or viewer                                                    |
| Recording  | the asciicast of one trainee link, from first open to revoke |
| Check      | one run of the scenario's check script and its result        |

## Acceptance criteria

- A reader who knows nothing about the project can say what a deployment
  is, who the operator and the trainee are, and what the check decides.
- Every non-goal is reflected in [06](06-security.md) or
  [07](07-roadmap.md), not silently dropped.
