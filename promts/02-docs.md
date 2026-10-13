# 02 - Docs: the Web UI contract

Read `promts/00-README.md` first; its ground rules and decisions apply.
Then read all of `docs/project/` and `packages/api/src/jatszoter_api/routes/`.

## Goal

`docs/project/08-web-ui.md` says what the Web UI is before a board is
drawn: every screen, the route that serves it, the API calls behind it,
how a browser is authenticated and how a state-changing request is
guarded. The other documents agree with it.

## Established facts

- The API authenticates signed requests and link cookies and nothing
  else. A link cookie is scoped to `Path=/api/v1/deployments/{id}`.
- `GET /s/{token}` answers 303 to `/t/{deployment_id}`; that page does not
  exist yet.
- `docs/project/01-architecture.md` draws browser -> web -> api and also
  says the API is the only component browsers talk to.
- `docs/project/06-security.md` promises CSRF tokens on every
  state-changing request with this item, and refuses a cookie request
  whose `Origin` does not name its own host.
- `docs/project/05-recording.md` promises a replay page with a vendored
  asciinema-player and markers as jump points; only a signed operator
  reads a recording.

## Scope

`docs/project/08-web-ui.md`, new, and the documents it touches:
`00-overview.md`, `01-architecture.md`, `04-console.md`, `05-recording.md`,
`06-security.md`, `07-roadmap.md`. No code.

Settle the points the README leaves open with the user first, one question
at a time, then write:

- The screens of version one, each with its route, who may open it, the
  API calls it makes and the actions it offers: login; deployments list,
  create and detail; scenarios and images; agents; users; logs; storage
  management; results; the trainee page; the replay page.
- For every screen, the API routes it needs that do not exist. That list
  is the scope of prompt 05; a screen that needs none says so.
- The operator session: the login page, the encrypted cookie and its
  attributes, credential refresh and logout, what a lapsed or refused
  credential shows.
- The relay: which paths `web` passes to the API unchanged, what it does
  with `Host`, `Origin`, cookies and the access log, and how the terminal
  socket is relayed, close codes included.
- CSRF: where the token comes from, how a form and an HTMX request carry
  it, who checks it for an operator session and for a link's request.
- Security headers on every page, and the rule that no page loads a file
  from another host.
- Acceptance criteria a test or the smoke stand can prove.

Then make the other documents agree: one sentence each for who the
browser talks to, where `/t/{deployment_id}` is served, where the replay
page lives and which CSRF sentence is no longer a promise.

## Failure behaviour

A screen whose content the user has not settled is written as open, by
name, and gets no board in prompt 04.

## Acceptance

- Every screen in the README's list is in `08-web-ui.md`, settled or
  named as open.
- No document contradicts another on who the browser talks to.
- `.venv/bin/pre-commit run --files docs/project/*.md` passes.
