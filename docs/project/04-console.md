# 04 - Console

The console is the VM's serial port, carried from the agent through the API
to every open terminal. Operators always see it; everyone else sees it
through a link; one party at a time holds the keyboard.

- [Pipeline](#pipeline)
- [Viewers](#viewers)
- [Links](#links)
- [Take control](#take-control)
- [Resize](#resize)
- [Backpressure](#backpressure)
- [Scaling](#scaling)
- [Acceptance criteria](#acceptance-criteria)

## Pipeline

```text
 ttyS0 -> QEMU serial socket -> agent --WS--> api hub --WS--> web --WS--> xterm.js
                                  ^                                         |
                                  +------------- keys, resize --------------+
```

- The agent holds one WebSocket per READY deployment to
  `WS /api/v1/agents/{agent_id}/deployments/{id}/console`. It sends binary
  frames of an 8-byte offset followed by console bytes; the API answers
  `{"offset": n}` with how far it holds. A reconnect resumes from that
  offset, so no byte is lost or duplicated.
- The API appends the bytes to the deployment's console buffer, up to
  `JATSZOTER_CONSOLE_LIMIT_BYTES`, fans them out to every attached viewer
  and streams the same bytes into the trainee link's recording in
  SeaweedFS; see [05](05-recording.md#writing).
- Keys and resize requests travel back on the same socket. Keys are never
  stored, logged or recorded.
- Bytes produced before READY belong to the operator's bootstrap log only.
  A trainee's view starts at the READY offset.

## Viewers

`WS /api/v1/deployments/{id}/attach` is the live view. It accepts either a
signed operator request or a link session cookie, sends the buffer from the
viewer's start offset, then every new byte raw.

A viewer sends:

- binary frames - keys, forwarded only when this viewer's role holds
  control;
- text frames - `{"cols": n, "rows": n}`, applied only from the viewer that
  holds control.

The API sends text frames `{"control": "operator" | "trainee"}` whenever
control changes, so every terminal can show who is typing.

## Links

A link is `https://jatszoter.example.com/s/<token>`, opens the one
deployment it names and belongs to one user. The user's `role` says what the
link can do:

| Role       | What it can do                                    |
| ---------- | ------------------------------------------------- |
| `trainee`  | works in the terminal while it holds control      |
| `operator` | watches, and takes or gives back control          |
| `viewer`   | only watches; its keys and resizes are dropped    |

- Every deployment is registered together with its trainee and that
  trainee's link; the operator issues links to operators and viewers on
  demand.
- The token is 32 random bytes, base64url. The API stores only its sha256
  and returns the plain value once.
- A link has no expiry of its own. It can be revoked at any time, and the
  deployment reaching COMPLETED, FAILED or CANCELLED revokes all its links.
- Opening the link exchanges it for an HttpOnly, SameSite=Strict session
  cookie and redirects to a URL without the token.
- Revoking a link closes every socket opened through it at once.
- The first open of the trainee link stamps its `opened_at` and starts its
  recording. Later opens, from any tab, join the same recording; revoking
  the link closes it, and a new trainee link starts a new one.

## Take control

- Only the operator decides who has the keyboard, and the API enforces it.
  The current holder is console state, not a database column.
- `POST /api/v1/deployments/{id}/control` with `{"holder": "operator"}`
  takes it; `{"holder": "trainee"}` gives it back. Only a signed operator
  request or an operator link may call it.
- While the operator holds control, trainee keys and resize frames are
  dropped in the API, whatever the browser shows.
- Every change is written as a marker into the recording.

## Resize

The serial line has no window size. When the holder of control resizes, the
agent sets it in the guest through the guest agent, as `stty cols N rows M`
on `ttyS0`, and records an `r` event in the recording.

## Backpressure

Each viewer gets a queue capped by bytes held,
`JATSZOTER_CONSOLE_VIEWER_BUFFER_BYTES`, 4 MiB by default. A viewer that
falls behind the cap has its queue dropped and is resent from the buffer at
its last acknowledged offset; if that is gone, the socket is closed and the
browser reconnects. A stalled tab never grows API memory without bound.

## Scaling

The fan-out hub lives in the API process. Version one runs the console
routes in one API process, so the agent socket and every viewer of a
deployment meet in the same hub. Routing sockets by `deployment_id` across
several processes is on the [roadmap](07-roadmap.md).

## Acceptance criteria

- A trainee with a valid link sees output within one second of the guest
  writing it, and cannot type while the operator holds control, even with a
  modified client.
- A revoked link closes the trainee socket and cannot be reopened.
- An agent reconnect loses and duplicates no console byte.
- A viewer that stops reading does not raise API memory past its cap.
