# 03 - Scenario format

A scenario is a directory under `scenarios/` holding `scenario.yaml` and the
files it names. The schema is part of the API version and lives at
`packages/api/src/jatszoter_api/schemas/scenario.schema.yaml`; pre-commit
validates every `scenario.yaml` against it.

- [Layout](#layout)
- [Fields](#fields)
- [Bootstrap](#bootstrap)
- [Check](#check)
- [Checks from outside](#checks-from-outside)
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
| `outside`     | no       | `network` and `disk` checks from outside the guest   |
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
- It runs in the guest agent's SELinux domain. Where SELinux is on, the
  bootstrap makes that domain permissive, so a check can open a socket or
  read whatever it checks.
- Exit 0 is `passed`, any other code `failed`, a timeout `timeout`, and a
  guest agent error `error`.
- The first line of stdout, up to 200 characters, is the message shown to
  the trainee and written into the recording. The first 4096 bytes of
  stderr are kept as the check's detail, for the operator only.
- A check must not change the state it checks, so running it twice gives the
  same result.
- The trainee can make this script pass; see
  [06](06-security.md#check-verdicts).

## Checks from outside

The optional `outside` block adds what the agent checks without the guest's
help. The check script stays required. These checks are declarative: the agent
runs no scenario text on the host.

```yaml
outside:
  network:
    - port: 80
      http:
        path: /
        status: 200
        contains: hello from app
    - port: 22
  disk:
    - path: /etc/nginx/conf.d/app.conf
      contains: proxy_pass http://127.0.0.1:8081;
    - path: /var/lib/alpha/broken
      exists: false
```

Limits:

- At most 8 `network` probes and 8 `disk` files.
- `path` of a probe is an absolute URL path, by default `/`; `status` is by
  default 200.
- `path` of a disk file is an absolute path of letters, digits, `.`, `_` and
  `-`, without `.` or `..` as a part. `exists` is by default true.
- `contains` is plain text of at most 200 characters that starts with a letter
  or a digit. It cannot go with `exists: false`.

### Network probes

- They run in every check, from the agent host, after the script passed. A
  check passes only when the script and every probe pass. A failed probe is
  `failed` with exit code 1, a fixed message for the trainee, and the port and
  the reason in the detail for the operator.
- With `http` the agent sends a GET and compares the status and the first
  64 KiB of the body. It follows no redirect.
- Without `http` the probe connects and passes unless the guest closes the
  connection within 2 seconds.
- The ports are forwarded when the VM starts, so a probe needs the service
  only when it runs. Each guest port gets a free port on the agent host's
  `127.0.0.1`, kept in `vm.json`.

### Disk checks

- They run once, when a deployment that reached READY stops, so they give no
  answer the trainee can act on. The verdict reaches the operator only: on the
  deployment page, and as a marker in the recording.
- The agent asks the guest to power off and waits up to 60 seconds, then kills
  it. It then boots an inspector VM: a pinned
  [Alpine](https://alpinelinux.org/cloud/) image, 256 MiB, with no network and
  the trainee's disk attached read-only. The agent downloads the image once
  and checks its digest, and the host needs nothing new.
- The inspector mounts the first partition holding `/etc/os-release`, tests the
  files and powers off. The agent posts the verdict and destroys both VMs. All
  of this is bounded at 300 seconds, and a stop goes on whatever it comes to.
- XFS and ext4 roots are read. A root on LVM is not, and its verdict is `error`.
- `exists` looks for a regular file. A symlink does not count, because an
  absolute one would resolve inside the inspector. `contains` searches the file
  for the text.
- The status is `passed`, `failed`, `error` when the disk cannot be read, or
  `timeout`. A deployment that ends without a verdict stores `error` with the
  message `deployment ended`.
- An agent without internet access needs a mirror of the image; see the
  [agent README](../../packages/agent/README.md).

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
- only public addresses, unless a network is listed in
  `JATSZOTER_SCENARIO_FETCH_INTERNAL_CIDRS`; see
  [06](06-security.md#bundle-downloads);
- no redirects;
- no `Content-Encoding`: the API asks for `identity` and refuses anything
  else, since the bundle is already a tar.gz;
- at most `JATSZOTER_SCENARIO_BUNDLE_LIMIT_BYTES`, 10 MiB by default;
- 30 seconds for the whole download.

Bundle rules:

- regular files only: no links, no sparse files, no absolute paths, no `..`;
- files sit at the root of the archive or under one top directory named
  like the `id`;
- `scenario.yaml` is at most 64 KiB and uses no YAML aliases.

The schema also limits the lists: at most 16 `tags`, 64 `packages` and 32
`hints`. A refused document gets its JSON path and the rule it breaks, not
its value.

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
