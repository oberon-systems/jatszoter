# 04 - API: images and the scenario registry

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/03-scenario-format.md` (all of it) and the Image and Scenario
entities in `docs/project/02-domain-model.md`.

## Goal

The operator registers base images and scenario versions. A scenario comes
as an uploaded bundle or as a URL the API fetches; the bundle is validated
and stored through `jatszoter_api.files`, and the row keeps the parsed
document, the bundle URL and its digest.

## Established facts

- `models/image.py`: `name` (unique), `url`, `digest`. `models/scenario.py`:
  `slug`, `version`, `document`, `files` (unique on slug and version).
- `require_operator` (prompt 02) and `get_files()` (prompt 03) exist.
- The schema is `schemas/scenario.schema.yaml`; `.pre-commit-config.yaml`
  points `check-jsonschema` and `check-metaschema` at it.
- `scenarios/nginx-502/` is a valid scenario whose image is `debian-12`.

## Scope

- The schema is part of the API version: move it to
  `packages/api/src/jatszoter_api/schemas/scenario.schema.yaml`, add it to
  the package data of `jatszoter-api`, drop the root `schemas/` directory, and
  update both hooks in
  `.pre-commit-config.yaml` and any other reference. Load it with
  `importlib.resources`.
- Dependencies of `jatszoter-api`: `pyyaml`, `jsonschema`, `python-multipart`
  (pin them in `requirements.txt` too, at the current releases).
- Settings: `scenario_bundle_limit_bytes: int = 10 * 1024 * 1024`.
- `models/image.py`: classmethods `by_name`, `by_id`, `every`;
  `register(session, name, url, digest)` returns `(image, created)`: same
  values again is a no-op, a different URL or digest raises a conflict.
- `models/scenario.py`: replace `files` with `bundle_url: str` and
  `digest: str`; classmethods `by_id`, `by_version(slug, version)`, `every`;
  `register(session, bundle: bytes)` returns `(scenario, created)`.
  Alembic revision `v0003_scenario_bundle`.
- `packages/api/src/jatszoter_api/bundles.py` - bundle handling, no HTTP:
  - open the tar.gz with `tarfile`, members read into memory; refuse links,
    devices, absolute names, `..`, more than one top directory, a top
    directory not named like `id`, and an uncompressed total above four times
    the limit;
  - `scenario.yaml` through `yaml.safe_load`, validated with `jsonschema`;
  - every file it names (`description`, `steps.*`, `check`) is present;
  - `digest` = sha256 over the sorted `(name, sha256(content))` pairs;
  - the image the document names must be registered.
- `Scenario.register`: existing slug and version with the same digest is a
  no-op; a different digest raises a conflict; otherwise put the original
  bytes at `scenarios/<slug>/<version>.tar.gz`, then save the row, and
  delete the file if the save fails.
- Fetching a URL (in the route module or a helper, not in the model):
  `httpx`, https or http to a loopback address only, `follow_redirects=False`
  and a redirect answered 422, 30 s timeout, body streamed and cut at the
  limit (413).
- `routes/images.py` and `routes/scenarios.py`, operator only:
  - `POST /api/v1/images` `{name, url, digest}`: 201 created, 200 no-op,
    409 conflict, 422 for a bad name, URL scheme or digest (64 lowercase hex);
  - `GET /api/v1/images`;
  - `POST /api/v1/scenarios`: `multipart/form-data` with `bundle`, or JSON
    `{url}`; 201, 200 no-op, 409, 413, 422 with a message naming what is
    wrong (schema path, missing file, unknown image);
  - `GET /api/v1/scenarios`: `id`, `slug`, `version`, `title`, `image`.
- Tests: `test_images.py`, `test_bundles.py`, `test_scenarios_api.py`, using
  bundles built in the tests from `scenarios/nginx-502` and a URL served
  through `httpx.MockTransport` or a patched client.

## Failure behaviour

Nothing of an invalid bundle reaches the file store or the database. The
bundle's content is never logged.

## Tests

Positive: the sample scenario registers from an upload and from a URL, and
the file lands at `scenarios/nginx-502/1.tar.gz` in the files dir; the same
content re-packed with other mtimes is a 200 no-op; a new version registers
beside the old one.

Negative, one test each: a symlink member, `../x`, two top directories, a
missing step file, a schema violation, an unknown image, an oversized
upload, an oversized download, a redirect, an `http://` URL to a
non-loopback host, the same version with changed content (409), a non-operator
caller (403).

## Acceptance

`.venv/bin/pytest -q packages/api` green; `.venv/bin/pre-commit run --files
scenarios/nginx-502/scenario.yaml` still validates it against the moved
schema; pre-commit clean on every changed file.
