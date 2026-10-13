# Roadmap item 11 - implementation prompts

Item 11 of `docs/project/07-roadmap.md` (the Web UI, design-first in
Penpot), cut into prompts that run one at a time, in order, each on a clean
tree. Every prompt assumes the previous ones have landed. Item 10's prompts
landed before this set was written; git history keeps them.

| #  | Prompt                 | Component | Result                                            |
| -- | ---------------------- | --------- | ------------------------------------------------- |
| 01 | `01-design-stack.md`   | dev/web   | the Penpot stack is current and exports lint      |
| 02 | `02-docs.md`           | docs      | the Web UI contract, a new `08-web-ui.md`         |
| 03 | `03-design-system.md`  | dev/web   | the `Design system` boards, approved and exported |
| 04 | `04-design-screens.md` | dev/web   | a board for every screen, approved and exported   |
| 05 | `05-api-gaps.md`       | api       | the routes the screens need and the API lacks     |
| 06 | `06-web-session.md`    | web       | login, the signed client, CSRF, the relay, shell  |
| 07 | `07-web-operator.md`   | web       | the operator list and form screens                |
| 08 | `08-web-terminal.md`   | web       | the deployment detail and the trainee page        |
| 09 | `09-web-replay.md`     | web       | the replay page with markers as jump points       |
| 10 | `10-smoke.md`          | smoke     | the operator and trainee pages load on the stand  |

Prompts 03 and 04 end with boards handed to the user and nothing built.
Prompt 05 onwards starts only after the boards it builds from are approved
and exported.

## Decisions behind the cut

Taken with the user on 2026-10-13; prompt 02 writes them into `docs/`.

- An operator logs in on a page of `web`. `web` keeps the credential in an
  encrypted HttpOnly cookie and signs every API call itself; the signing
  key never reaches JavaScript and `web` keeps no store of its own.
- The browser talks to one origin, served by `web`. `web` relays to the
  API whatever a link may call: `GET /s/{token}`, the routes under
  `/api/v1/deployments/{id}` and the terminal socket. No reverse proxy is
  needed on any stand, the smoke one included.
- Screens of version one: login; deployments (list, create, detail with
  the terminal, take control, links, checks, recordings, stop); scenarios
  and images; agents; users; logs; storage management; results; the
  trainee page at `/t/{deployment_id}`; the replay page.
- The contract of all of it is a new `docs/project/08-web-ui.md`.

## Left open, for prompt 02 to settle with the user

- What the logs, storage management and results screens show. The API has
  a console log download per deployment and nothing else for them.
- The API has no operator route that lists agents or enables one, and none
  that enables or disables a user. Prompt 02 names every such gap; prompt
  05 closes them.
- How the cookie of `web` is encrypted, which library does it and which
  setting carries the key.
- Who checks the CSRF token of a link's request, `web` or the API, given
  that `web` only relays it.

## Ground rules for every prompt

Each prompt starts by pointing here. These hold for all of them.

- Do not commit, stage, branch or run any writing git command.
- Read `AGENTS.md` and the docs a prompt names before writing code. Where
  the code and a prompt disagree with `docs/`, the docs win; stop and say so.
- Code, comments, docs and messages are in English, UTF-8.
- Comments only where the code is not obvious, at most two lines each.
- No real names of hosts, IPs, users, repositories or paths in code, tests,
  docs or boards: use `alpha`, `beta`, `example.com`, `192.0.2.x`. Loopback
  `127.0.0.1` is fine.
- Off limits: `.claude/`, `.gemini/`, `.codex/`, `target/`, `.venv/`, any
  `.env` file, `CLAUDE.local.md`, `GEMINI.md`.
- Every edited file must pass `.venv/bin/pre-commit run --files <paths>`.
- Ask before adding a dependency, a package, a directory or a settings key
  a prompt does not name.
- Design rules (see `dev/web/README.md`):
  - every screen is drawn in the `base` template before any code exists
    for it; the `Design system` page is approved before any screen board;
  - a board is addressed by its page and board name, and read from the
    export in `dev/web/templates/base`; the stack is started only to draw;
  - starting and stopping the stack is the user's: ask for `make design`
    and `make -C dev/web down`, and keep a file open in the Penpot tab
    while the Penpot MCP server is used;
  - boards are handed to the user and the prompt stops; only an explicit
    approval lets the export be committed and the build begin;
  - images and icons are SVG; an export with a raster image is refused.
- Web conventions (see `docs/project/01-architecture.md`):
  - Jinja2, HTMX, xterm.js and asciinema-player, no JavaScript build step;
    third-party files are vendored under the package's static directory
    with their version and licence, never loaded from another host;
  - no inline script or style: the `Content-Security-Policy` forbids them;
  - `web` holds no state of its own and reads no database; everything it
    shows comes from the API;
  - a page is built only from an approved board. A screen without one gets
    nothing built and is reported as missing;
  - the UI is never an authorization boundary: every action is authorized
    by the API, and a dangerous one asks for confirmation;
  - settings are read once in `create_app(settings)` and passed on.
- Python API conventions are those of `docs/project/02-domain-model.md`;
  the project is in development, so a schema change edits
  `migrations/versions/v0001_initial.py` and adds no revision.
- Never log or render a password, token, link, credential key, signature,
  cookie value, the content of a seed, a check script, a check's stdout or
  stderr beyond its message, console bytes or the content of a recording.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `.venv/bin/pytest -q packages/web` for web prompts;
  - `make smoke` after every prompt that touches `packages/`; prompt 10
    changes it.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
