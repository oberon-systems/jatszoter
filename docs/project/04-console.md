# 04 - Console

The console is the VM's serial port, carried from the agent through the API
to every open terminal. Operators always see it; everyone else sees it
through a link; one party at a time holds the keyboard.

- [Pipeline](#pipeline)
- [Console store](#console-store)
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

- QEMU writes everything the guest prints on `ttyS0` into `console.log` in
  the VM directory itself, through `logfile=` and `logappend=on` on the
  serial chardev, from boot and whether or not a client is connected. The
  file is the agent's buffer; the agent keeps no copy of its own.
- The agent holds one WebSocket per live VM whose deployment is STARTING,
  BOOTSTRAPPING or READY, to
  `WS /api/v1/agents/{agent_id}/deployments/{id}/console`, signed like every
  agent request over `GET`, the path and an empty body. It ships the file
  from offset 0, so the operator sees the boot.
- An agent frame is binary: an 8-byte big-endian offset, then at most
  64 KiB read from `console.log` at that offset. The API answers the text
  frame `{"offset": n}`, how far it holds the log, once on connect and after
  every frame. The agent moves its offset to `n`, backwards too, and sends
  the next frame only after that answer.
- The agent looks at the file every 50 ms while the socket is up and
  reconnects 5 s after it drops. There is no HTTP fallback: the bytes wait
  in `console.log`, so a reconnect loses and duplicates nothing.
- The API refuses the socket for a deployment that is not the agent's, not
  under a live lease, or not STARTING, BOOTSTRAPPING or READY. A newer
  socket for the same deployment replaces the older one.
- The API stores the bytes, see [Console store](#console-store), fans them
  out to every attached viewer, and streams the same bytes into the trainee
  link's recording; see [05](05-recording.md#writing).
- Keys and resize requests travel back on the same socket. Keys are never
  stored, logged or recorded, and console bytes never reach a log line.

## Console store

The API keeps two copies of a deployment's console.

- The tail: the last `JATSZOTER_CONSOLE_TAIL_BYTES` (8 MiB by default) in
  Valkey, with the offsets where it starts and ends. A viewer that connects
  gets this tail; anything older lives only in the browsers that saw it.
- The full log: every byte appended at once to the file store as
  `consoles/<deployment_id>.log`, in SeaweedFS or under `data/storage/`,
  through the filer's append. It stops at
  `JATSZOTER_CONSOLE_LIMIT_BYTES` (64 MiB by default): the API closes the
  agent's socket with code 4413 and the agent ships nothing more for that
  deployment.
- Where the API holds the log is the end of the tail, or the full log's size
  when Valkey lost the tail, so an API restart never duplicates bytes in the
  full log.
- The log end at the moment the `bootstrap` transition is accepted is the
  READY offset, kept with the tail. Bytes before it are the operator's
  bootstrap log; a trainee's view starts at it.
- A deployment reaching COMPLETED, FAILED or CANCELLED drops its Valkey keys;
  the full log stays until the retention sweep. Every write renews a one-day
  TTL on the keys, so a tail whose drop was missed frees itself.
- `GET /api/v1/deployments/{id}/console` gives an operator the full log.

## Viewers

`WS /api/v1/deployments/{id}/attach` is the live view. It accepts either a
signed operator request, checked from the upgrade's headers, or a link
session cookie; links arrive with roadmap item 8.

- An optional query `offset` resumes a viewer that already holds the bytes
  before it, when the tail still has it; otherwise the viewer gets the whole
  tail.
- The API sends a text frame `{"offset": n}` before the first bytes and
  before any jump, then the console bytes raw as binary frames.
- The socket closes with 1000 once the deployment is terminal.

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
falls behind the cap has its queue dropped and is resent from the Valkey
tail at the last offset it was sent; if the tail no longer has it, the
socket closes with 4429 and the browser reconnects. A stalled tab never
grows API memory without bound.

## Scaling

The fan-out hub lives in the API process. Version one runs the console
routes in one API process, so the agent socket and every viewer of a
deployment meet in the same hub. The tail and the full log already live
outside the process, in Valkey and the file store. Routing sockets by `deployment_id` across
several processes is on the [roadmap](07-roadmap.md).

## Acceptance criteria

- A trainee with a valid link sees output within one second of the guest
  writing it, and cannot type while the operator holds control, even with a
  modified client.
- A revoked link closes the trainee socket and cannot be reopened.
- An agent reconnect loses and duplicates no byte of the full log, and
  neither does an agent restart.
- An API restart with Valkey alive keeps the tail; without it, the full log
  still has no gap and no duplicate.
- A viewer that stops reading does not raise API memory past its cap.
