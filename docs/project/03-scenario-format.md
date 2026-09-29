# 03 - Scenario format

A scenario is a directory under `scenarios/` holding `scenario.yaml` and the
files it names. The schema is `schemas/scenario.schema.yaml`; pre-commit
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

```bash
curl -X POST https://jatszoter.example.com/api/v1/scenarios \
  -H "Authorization: Bearer $JATSZOTER_TOKEN" \
  -F "bundle=@nginx-502.tar.gz"
```

The API validates the bundle against the same schema, checks that every
named file exists, and refuses a `version` that is already registered for
that `id` with different content. Registering the same content again is a
no-op.

## Acceptance criteria

- `scenarios/nginx-502` passes the schema and bootstraps to READY on a
  stock `debian-12` image with cloud-init and qemu-guest-agent.
- After READY, none of the step scripts, the check script or the
  `user-data` are left in the guest filesystem.
- Nothing a step prints reaches the trainee's terminal.
