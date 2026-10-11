# 03 - Scenario format

A scenario is a directory under `scenarios/` holding `scenario.yaml` and the
files it names. The schema is part of the API version and lives at
`packages/api/src/jatszoter_api/schemas/scenario.schema.yaml`; pre-commit
validates every `scenario.yaml` against it.

- [Layout](#layout)
- [Fields](#fields)
- [Bootstrap](#bootstrap)
- [Check](#check)
- [Registering](#registering)
- [Acceptance criteria](#acceptance-criteria)

## Layout

```text
scenarios/nginx-502/
  scenario.yaml     metadata, resources, steps, check
  description.md    task text shown to the trainee
  install.sh        steps, run as root in order
  configure.sh
  break.sh
  check.sh          exit 0 means solved
```

## Fields

| Field         | Required | Meaning                                              |
| ------------- | -------- | ---------------------------------------------------- |
| `id`          | yes      | slug, equals the directory name                      |
| `version`     | yes      | integer, bumped on every change                      |
| `title`       | yes      | one line, up to 120 characters                       |
| `summary`     | no       | one paragraph for the catalog                        |
| `description` | yes      | Markdown file with the task text                     |
| `difficulty`  | no       | `easy`, `medium` or `hard`                           |
| `tags`        | no       | lowercase slugs                                      |
| `image`       | yes      | name of an image registered in the API               |
| `resources`   | no       | `cpus`, `memory_mib`, `disk_gib`                     |
| `timeouts`    | no       | bootstrap, check and session limits                  |
| `trainee`     | no       | `user` the console logs in as, `sudo` for that user  |
| `packages`    | no       | installed by cloud-init before the steps             |
| `steps`       | yes      | `install`, `configure`, `break`; `break` is required |
| `check`       | yes      | check script file                                    |
| `hints`       | no       | strings the operator can reveal, in order            |

Missing `resources`, `timeouts` and `trainee` values fall back to API
settings. Every file reference is a bare file name next to `scenario.yaml`.
A deployment copies resources and timeouts at create time; the fallbacks
are:

| Value               | Setting                               | Default |
| ------------------- | ------------------------------------- | ------- |
| `cpus`              | `JATSZOTER_DEFAULT_CPUS`              | 1       |
| `memory_mib`        | `JATSZOTER_DEFAULT_MEMORY_MIB`        | 1024    |
| `disk_gib`          | `JATSZOTER_DEFAULT_DISK_GIB`          | 10      |
| `bootstrap_seconds` | `JATSZOTER_DEFAULT_BOOTSTRAP_SECONDS` | 900     |
| `check_seconds`     | `JATSZOTER_DEFAULT_CHECK_SECONDS`     | 30      |
| `session_minutes`   | `JATSZOTER_DEFAULT_SESSION_MINUTES`   | 60      |
| `trainee.user`      | `JATSZOTER_DEFAULT_TRAINEE_USER`      | trainee |
| `trainee.sudo`      | `JATSZOTER_DEFAULT_TRAINEE_SUDO`      | false   |

## Bootstrap

The API renders a cloud-init NoCloud seed, `user-data` and `meta-data`,
from the stored bundle of the deployment's scenario version and serves it on
the agent's seed route, see [01](01-architecture.md#agent-protocol). The
agent packs it into the seed ISO, volume label `cidata`, and never parses a
scenario. The `user-data` does, in order:

1. create the trainee user, with passwordless sudo when `sudo` is true;
2. install `packages`;
3. write the step scripts to a root-only directory and run `install`,
   `configure`, `break` as root, output to a log file, not to the console;
4. delete the scripts, the log and cloud-init's copy of `user-data`;
5. enable a serial getty on `ttyS0` that logs in as the trainee user and
   clear the screen;
6. write the marker file: `ok`, or `failed <step> <exit code>`.

The steps are carried out as follows:

- `user-data` is a `#cloud-config` document. `meta-data` holds
  `instance-id: <vm_id>` and `local-hostname: <scenario id>`.
- The trainee user is created through cloud-config `users`, with
  passwordless sudo when `sudo` is true, and no password.
- `packages` always gains `qemu-guest-agent`. On RHEL-family guests the
  seed also allows the guest agent's `guest-file-*` and `guest-exec*` calls,
  which the distribution blocks by default, and restarts it.
- The step scripts and one driver script are written under
  `/var/lib/jatszoter/`, mode 0700, owned by root. `runcmd` runs the driver.
- The driver runs `install`, `configure`, `break` in order with output to
  `/var/lib/jatszoter/bootstrap.log` and stops at the first failure. It then
  removes the scripts, the log and cloud-init's copies of `user-data`, sets
  up the serial getty, and writes the marker last.
- The marker is `/var/lib/jatszoter/marker`: `ok`, or
  `failed <step> <exit code>`. The agent reads it with `guest-file-open`,
  `guest-file-read` and `guest-file-close`.
- The getty on `ttyS0` logs in as the trainee user without a password and
  clears the screen.

The agent probes the guest agent and then the marker, see
[01](01-architecture.md#reconciliation). On `ok` it ejects the seed ISO and
moves the deployment to READY. A `failed` marker moves it to FAILED with the
marker as reason, and `bootstrap_seconds` passing does so with reason
`bootstrap timeout`.

## Check

- The script never lives on the VM between checks: the agent writes it into
  the guest for each run and deletes it afterwards.
- It runs as root with `check_seconds` as the timeout; at the deadline the
  agent kills it in the guest.
- Exit 0 is `passed`, any other code `failed`, a timeout `timeout`, and a
  guest agent error `error`.
- The first line of stdout, up to 200 characters, is the message shown to
  the trainee and written into the recording. The first 4096 bytes of
  stderr are kept as the check's detail, for the operator only.
- A check must not change the state it checks, so running it twice gives the
  same result.

## Registering

An operator registers a scenario with a signed `POST /api/v1/scenarios`
([01](01-architecture.md#operator-api)), in one of two forms:

- multipart with a `bundle` field: a tar.gz of the scenario directory;
- JSON naming a bundle the API fetches:

  ```json
  {"url": "https://example.com/nginx-502.tar.gz"}
  ```

Fetch rules:

- `https`, or `http` to a loopback address only;
- no redirects;
- at most `JATSZOTER_SCENARIO_BUNDLE_LIMIT_BYTES`, 10 MiB by default;
- a 30 second timeout.

Bundle rules:

- regular files only: no links, no absolute paths, no `..`;
- files sit at the root of the archive or under one top directory named
  like the `id`.

The API validates `scenario.yaml` against the schema, checks that every
named file exists and that the named `image` is already registered. It
saves the row first and then stores the bundle in the file store as
`scenarios/<slug>/<version>.tar.gz`, so a refused version never touches a
stored bundle.
The digest is sha256 over the sorted file names and their contents, so
re-packing the same files gives the same digest.

A `version` already registered for that `id` is refused with 409, whatever
its digest: a registered version is immutable, and a change is a new
version.

## Acceptance criteria

- `scenarios/nginx-502` passes the schema and bootstraps to READY on a
  stock Rocky Linux 9 GenericCloud image registered as `rocky-9`.
- After READY, none of the step scripts, the check script or the
  `user-data` are left in the guest filesystem.
- Nothing a step prints reaches the trainee's terminal.
