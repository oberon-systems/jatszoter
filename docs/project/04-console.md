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
  out to every attached viewer, and writes the bytes from the READY offset
  on into the trainee link's recording; see [05](05-recording.md#writing).
- Keys and resizes travel down the same socket, between the `{"offset": n}`
  answers: keys as a binary frame, a resize as the text frame
  `{"cols": n, "rows": n}`. Up to 256 such messages wait for a slow agent;
  more are dropped, and so is one that arrives while no agent socket is
  attached.
- The agent connects `serial.sock` on the first key and keeps it until its
  console socket ends, reading and discarding what the guest echoes there,
  since `console.log` already carries it. Keys that cannot be written are
  lost; the console socket lives on.
- Keys are never stored, logged or recorded, not their bytes and not their
  length, and console bytes never reach a log line.

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

`WS /api/v1/deployments/{id}/attach` is the live view. An upgrade that
carries `X-Signature` is checked as a signed operator request; any other is
checked as a link session, from the cookie described in [Links](#links).

- A signed operator attaches in any status. A link attaches only while the
  deployment is READY, its link is not revoked, its user is `enabled`, and
  the upgrade's `Origin`, when sent, names the upgrade's own host.
- Every refusal before the socket is accepted closes with 1008, whatever
  the reason.
- An operator link sees what a signed operator sees. A trainee or viewer
  link starts at the READY offset: an older `offset` is raised to it, and
  without a READY offset the view starts at the log end.
- An optional query `offset` resumes a viewer that already holds the bytes
  before it, when the tail still has it; otherwise the viewer gets the whole
  tail.
- The API sends a text frame `{"offset": n}` before the first bytes and
  before any jump, then the console bytes raw as binary frames.
- The socket closes with 1000 once the deployment is terminal, and with
  4403 once its link is revoked.

A viewer sends:

- binary frames - keys, at most 4096 bytes each; a larger frame closes the
  socket with 1009;
- text frames - `{"cols": n, "rows": n}`, both integers from 1 to 500;
  anything else is dropped.

Either is forwarded only from a socket whose role holds control, see
[Take control](#take-control). The API sends the text frame
`{"control": "operator" | "trainee"}` after a viewer's first offset and
whenever control changes, so every terminal can show who is typing.

## Links

A link is `https://jatszoter.example.com/s/<token>`, opens the one
deployment it names and belongs to one user. The user's `role` says what the
link can do:

| Role       | What it can do                                    |
| ---------- | ------------------------------------------------- |
| `trainee`  | works in the terminal while it holds control      |
| `operator` | watches, and takes or gives back control          |
| `viewer`   | only watches; its keys and resizes are dropped    |

- The users behind links are made by the operator with `POST
  /api/v1/users`: name, role and comment. Such a user is `enabled` at once
  and has no password, so it cannot log in; it exists to own links.
- `POST /api/v1/deployments` requires `trainee_id`, an enabled user with
  role `trainee`, and answers with the deployment and its trainee link.
  `POST /api/v1/deployments/{id}/links` with `user_id` issues a link to any
  enabled user while the deployment is not terminal. A deployment holds one
  live trainee link at a time; a second is refused with 409.
- `GET /api/v1/deployments/{id}/links` lists a deployment's links without
  tokens or hashes; `POST /api/v1/links/{id}/revoke` revokes one.
- The token is 32 random bytes, base64url. The API stores only its sha256
  and returns the plain value once, in the response that creates the link.
- A link has no expiry of its own. It can be revoked at any time, and the
  deployment reaching COMPLETED, FAILED or CANCELLED revokes all its links.
- `GET /s/{token}`, served by the API outside `/api/v1` and reached through
  the relay of `web`, opens the link: it sets the session cookie and answers
  303 to `/t/{deployment_id}`, a URL without the token, where `web` serves
  the trainee page, see [08](08-web-ui.md#trainee-page). An unknown or
  revoked link and a link of a disabled user get the same 404. The link
  opens in any non-terminal status; the terminal attaches at READY.
- The cookie `jatszoter_link` is HttpOnly, Secure, SameSite=Strict, without
  an expiry, and scoped to `Path=/api/v1/deployments/{id}`. Its value is a
  random session token of 32 bytes; Valkey maps its sha256 to the link id
  for a day, renewed on use. Every use loads the link row, so a revoked
  link ends all its sessions.
- The access log shows the open as `/s/<redacted>`.
- Revoking a link closes every socket opened through it at once, with
  4403.
- The first open of the trainee link stamps its `opened_at` and starts its
  recording, which takes output from the READY offset on. Later opens, from
  any tab, join the same recording; revoking the link closes it, and a new
  trainee link starts a new one. Links of other roles record nothing.

## Take control

- Only the operator decides who has the keyboard, and the API enforces it.
  The holder is `trainee` or `operator`, kept in Valkey beside the tail as
  `console:<id>:control`; it is `trainee` when the key is absent and is
  dropped with the tail.
- `POST /api/v1/deployments/{id}/control` with `{"holder": "operator"}`
  takes it; `{"holder": "trainee"}` gives it back. Only a signed operator
  request or an operator link may call it, and only while the deployment
  is READY; otherwise it gets 409. A link's request whose `Origin`, when
  sent, does not name the request's own host is refused.
- A signed operator and an operator link type as `operator`, a trainee link
  as `trainee`; a viewer link never types. The API reads the holder for
  every frame and forwards it only from a socket of that role, whatever
  the browser shows.
- Every change is written into the trainee link's recording as the marker
  `control operator` or `control trainee`, see [05](05-recording.md#format).

## Resize

The serial line has no window size. When the holder of control resizes, the
agent sets it in the guest through the guest agent: `guest-exec` of `stty -F
/dev/ttyS0 cols N rows M`, spawned as an argument vector.

- The agent waits at most 5 s for the command and ships no console bytes
  meanwhile. A resize that fails or times out is tried again at the next
  resize frame.
- A size equal to the last one applied runs nothing.
- This is proven on the Rocky 9 smoke guest with SELinux enforcing: the
  command exits 0 and `stty size` reads the new size back. The bootstrap
  makes the guest agent's domain permissive there, see
  [03](03-scenario-format.md#check).
- The API writes an `r` event into the recording when it passes the
  holder's resize to the agent, whether or not the guest then applies it;
  see [05](05-recording.md#writing).

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
- A revoked link closes its sockets with 4403 and cannot be reopened.
- A viewer link never types: its keys and resizes reach no agent.
- No link token and no session token appears in any log.
- An agent reconnect loses and duplicates no byte of the full log, and
  neither does an agent restart.
- An API restart with Valkey alive keeps the tail; without it, the full log
  still has no gap and no duplicate.
- A viewer that stops reading does not raise API memory past its cap.
