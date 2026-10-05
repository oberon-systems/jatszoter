# 02 - Scenario: nginx-502 version 2 on Rocky Linux 9

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/03-scenario-format.md` and the scenario schema at
`packages/api/src/jatszoter_api/schemas/scenario.schema.yaml`.

## Goal

The sample scenario bootstraps on a stock Rocky Linux 9 GenericCloud image.
Nothing boots it yet - prompt 07 does - so this prompt is about getting the
scripts right for that distribution.

## Established facts

- Version 1 targets Debian: it writes `/etc/nginx/sites-available/default`,
  expects nginx to be running already (`systemctl reload nginx`) and names
  the image `debian-12`.
- On Rocky 9 nginx comes from AppStream, reads `/etc/nginx/conf.d/*.conf`,
  is neither enabled nor started after install, and its stock
  `/etc/nginx/nginx.conf` carries its own `default_server` on port 80.
- SELinux is enforcing; nginx may not connect to a local port unless the
  boolean `httpd_can_network_connect` is on.
- The GenericCloud image ships `curl-minimal`; installing the `curl` package
  conflicts with it, and a failed install would fail the whole bootstrap.
- `make/tests/smoke.sh` registers its blank disk as `debian-12`, and the
  API refuses a scenario whose image is not registered.
- The GenericCloud qcow2 has a virtual size of about 10 GiB; an overlay
  smaller than its base cuts the guest's disk short, and version 1 asks for
  8 GiB.

## Scope

`scenarios/nginx-502/*` and the image name in `make/tests/smoke.sh`.

- `scenario.yaml`: `version: 2`, `image: rocky-9`, `packages` without
  `curl` (`nginx`, `python3` stay), `resources.disk_gib: 12` so the overlay
  is never smaller than the base (check the image's virtual size with
  `qemu-img info`), `timeouts.bootstrap_seconds: 1800` so a run without KVM
  fits; everything else unchanged.
- `configure.sh`:
  - the app unit as before;
  - the site in `/etc/nginx/conf.d/app.conf`, proxying to
    `127.0.0.1:8081`;
  - take port 80 from the stock config: write a minimal
    `/etc/nginx/nginx.conf` that only includes `conf.d`, or remove the stock
    `server` block - whichever keeps the file readable; say which in the
    report;
  - `setsebool -P httpd_can_network_connect 1`;
  - `systemctl daemon-reload`, `systemctl enable --now app.service nginx`.
- `break.sh`: unchanged in spirit - the app moves to 8018 so nginx answers
  502.
- `check.sh`: checks `/etc/nginx/conf.d/app.conf` instead of
  `sites-available/default`; messages unchanged.
- `install.sh`, `description.md`: only if they name Debian paths.
- `make/tests/smoke.sh`: register the blank disk as `rocky-9` instead of
  `debian-12`, so the smoke stays green until prompt 07 gives it a real image.

## Failure behaviour

Every script keeps `set -eu`, so the first failing command fails its step and
the marker names it.

## Acceptance

- `.venv/bin/pre-commit run --files scenarios/nginx-502/* make/tests/smoke.sh`
  passes (schema, shellcheck, shfmt).
- `make smoke` still passes.
- Read through on paper against a Rocky 9 host, or tried on one if
  available: after `install`, `configure` and `break`, `curl -s
  http://127.0.0.1/` gives 502; after moving the app back to 8081 and
  restarting it, `check.sh` prints `solved` and exits 0.
