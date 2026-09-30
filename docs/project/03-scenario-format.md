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

## Bootstrap

The agent builds a cloud-init NoCloud seed ISO (volume label `cidata`) from
the scenario version. Its `user-data` does, in order:

1. create the trainee user, with passwordless sudo when `sudo` is true;
2. install `packages`;
3. write the step scripts to a root-only directory and run `install`,
   `configure`, `break` as root, output to a log file, not to the console;
4. delete the scripts, the log and cloud-init's copy of `user-data`;
5. enable a serial getty on `ttyS0` that logs in as the trainee user and
   clear the screen;
6. write the marker file: `ok`, or `failed <step> <exit code>`.

The agent polls the marker through the guest agent. On `ok` it ejects the
seed ISO and moves the deployment to READY; on failure or on
`bootstrap_seconds` it moves it to FAILED with the marker as reason.

## Check

- The script never lives on the VM between checks: the agent writes it into
  the guest for each run and deletes it afterwards.
- It runs as root with `check_seconds` as the timeout.
- Exit 0 is `passed`, any other code `failed`, a timeout `timeout`, and a
  guest agent error `error`.
- The first line of stdout is the message shown to the trainee; stderr goes
  to the operator only.
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
stores the bundle in the file store as `scenarios/<slug>/<version>.tar.gz`.
The digest is sha256 over the sorted file names and their contents, so
re-packing the same files gives the same digest.

A `version` already registered for that `id` with a different digest is
refused. Registering the same digest again is a no-op.

## Acceptance criteria

- `scenarios/nginx-502` passes the schema and bootstraps to READY on a
  stock `debian-12` image with cloud-init and qemu-guest-agent.
- After READY, none of the step scripts, the check script or the
  `user-data` are left in the guest filesystem.
- Nothing a step prints reaches the trainee's terminal.
