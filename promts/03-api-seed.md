# 03 - API: render a deployment's NoCloud seed

Read `promts/00-README.md` first; its ground rules apply. Then read Agent
protocol in `docs/project/01-architecture.md`, Bootstrap in
`docs/project/03-scenario-format.md` and Scenario secrecy in
`docs/project/06-security.md`.

## Goal

The agent holding a STARTING deployment fetches its `user-data` and
`meta-data` with one signed request. The API renders both from the stored
bundle; the agent never sees the bundle itself.

## Established facts

- `bundles.read(data, limit)` checks a bundle and returns
  `Bundle(document, digest)`; the checked file contents are dropped.
- `Scenario.bundle_path` is `scenarios/<slug>/<version>.tar.gz`;
  `File(path, filer_url=...)` with `get()` fills `.data` from the filer or
  from `data/storage/`, as `routes/scenarios.py` does with `put()`.
- `Deployment.scenario` is a cached property; `Deployment.vm_id` is set on
  assignment.
- `routes/agents.py` has `LeasedDep` (409 without a live lease) and loads a
  deployment by `id` and `agent_id` for transitions, 404 on a miss.
- Settings hold `default_*` fallbacks for resources and timeouts; nothing
  for the trainee yet.

## Scope

- `settings.py`: `default_trainee_user: str = "trainee"` (same pattern as
  the schema's `trainee.user`), `default_trainee_sudo: bool = False`.
- `bundles.py`: `Bundle` also carries `files: dict[str, bytes]`, the checked
  files named relative to `scenario.yaml`. Registering ignores them.
- `seed.py`, pure and tested without I/O:
  `render(document, files, vm_id, trainee_user, trainee_sudo) -> Seed` with
  `Seed(user_data: str, meta_data: str)`:
  - `meta_data`: `instance-id: <vm_id>`, `local-hostname: <document id>`;
  - `user_data`: `#cloud-config` followed by YAML from `yaml.safe_dump`,
    never string templating of scenario text:
    - `users`: the trainee (document value or fallback), no password, a
      sudo rule for passwordless sudo only when `sudo` is true; `default`
      kept so the image's own user is untouched;
    - `packages`: the document's list plus `qemu-guest-agent`, no
      duplicate;
    - `write_files`: the three step scripts as
      `/var/lib/jatszoter/steps/<install|configure|break>.sh` and a driver
      `/var/lib/jatszoter/bootstrap.sh`, all `encoding: b64`, owner
      `root:root`, mode `0700`;
    - `runcmd`: run the driver, nothing else.
  - The driver, a POSIX `sh` script rendered from a constant in `seed.py`:
    1. on RHEL-family guests, clear the guest agent's RPC block list in
       `/etc/sysconfig/qemu-ga`, allow it to read files under SELinux if a
       boolean is needed, enable and restart `qemu-guest-agent`; elsewhere
       just enable and start it. Verify the variable name and the boolean
       on a Rocky 9 GenericCloud image, and say in the report what you
       checked;
    2. run `install`, `configure`, `break` in order as root, output appended
       to `/var/lib/jatszoter/bootstrap.log`, stopping at the first non-zero
       exit;
    3. remove the step scripts, the log, and every copy of the user-data
       cloud-init keeps under `/var/lib/cloud/instances/<instance-id>/`
       (`user-data.txt*`, `cloud-config.txt`, `scripts/`, `obj.pkl`), but
       keep `sem/` so a reboot does not run the bootstrap again;
    4. on success only: a systemd drop-in so `serial-getty@ttyS0` logs in
       as the trainee without a password, `daemon-reload`, restart that
       getty, and clear the screen on `ttyS0`;
    5. write `/var/lib/jatszoter/marker` last: `ok`, or
       `failed <step> <exit code>`; then remove the driver itself.
- `models/deployment.py`: `seed(files, trainee_user, trainee_sudo) -> Seed`
  calling `seed.render` with `self.vm_id` and `self.scenario.document`.
- `routes/agents.py`:
  `GET /{agent_id}/deployments/{deployment_id}/seed` with `LeasedDep`:
  - the agent's own deployment or 404; not STARTING is `ConflictError`;
  - read the bundle through `File(scenario.bundle_path, filer_url=...)`,
    `bundles.read` it with the bundle limit, call `deployment.seed(...)`;
  - answer `SeedOut(user_data, meta_data)`; log nothing of it.
- Tests in `tests/test_seed.py` for `render` and in
  `tests/test_agents_api.py` for the route.

## Failure behaviour

- A stored bundle that no longer reads (missing, or failing the checks it
  passed at registration) is a 500 the agent turns into a failed start; it
  is never served half-rendered.
- A step name or trainee user never reaches a shell unquoted: the scripts
  are files, and the driver takes no input from the document except fixed
  file names.

## Tests

Positive: `meta_data` fields; packages gain `qemu-guest-agent` once; the
trainee with and without sudo, and the fallbacks; each step decodes back to
the bundle's bytes; the driver runs the steps in order and writes the marker
last; the route returns the seed for a STARTING deployment.

Negative: another agent's deployment is 404; a PENDING or BOOTSTRAPPING one
is 409; no live lease is 409; an unsigned request is 401; a missing stored
bundle is not answered with a seed.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` still passes.
