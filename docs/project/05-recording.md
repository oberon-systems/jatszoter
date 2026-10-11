# 05 - Recording

Every trainee link is recorded as an
[asciicast v2](https://docs.asciinema.org/manual/asciicast/v2/) file, so the
operator can replay exactly what the terminal showed.

- [Format](#format)
- [Writing](#writing)
- [Storage](#storage)
- [Replay](#replay)
- [Acceptance criteria](#acceptance-criteria)

## Format

One JSON header line, then one JSON event per line:

```text
{"version": 2, "width": 80, "height": 24, "timestamp": 1790000000, "title": "nginx-502 v2 5f0c..."}
[0.0, "o", "trainee@alpha:~$ "]
[4.031, "r", "140x40"]
[9.87, "m", "control operator"]
```

- The header says 80 by 24, the size of a serial line nobody has resized.
  `timestamp` is when the link was first opened; `title` is the scenario's
  slug, its version and the deployment id.
- `o` - output: the console bytes the API received from the agent, decoded
  as UTF-8. A sequence split between two frames is carried over to the
  next event, and an invalid byte is written as U+FFFD.
- `r` - resize, `<cols>x<rows>`, from the holder of control.
- `m` - markers: `control operator` and `control trainee` for control
  changes, and `recording limit reached`, see [Storage](#storage). Check
  results join them with roadmap item 10.
- There are no `i` events: keys are never stored. Nothing a viewer sends
  reaches the file except the size in a resize.

## Writing

- The first open of a trainee link stores the Recording row and writes the
  header. Links of other roles record nothing. Later opens, from any tab,
  join the same recording.
- Time zero is the row's `created_at`, a whole second on the API clock. An
  event's time is the API clock minus it.
- Output is recorded from the READY offset, the place where a trainee's
  terminal starts; see [04](04-console.md#console-store). Nothing is
  written before READY. A link first opened after READY gets the bytes
  from the READY offset to the log end as its first event, at time 0; when
  the tail no longer holds them, the recording starts at the log end.
- From then on every frame the API stores is recorded before it is fanned
  out, and only the bytes past what is already recorded. It is the same
  stream the terminals get. Nothing is buffered into a whole file first; a
  crash loses only the bytes in flight.
- An `r` event is written when the API passes a resize of the holder of
  control to the agent. The API cannot see whether the guest applied it.
- An `m` event is written when
  [the control route](04-console.md#take-control) changes the holder.
- Each event is stamped and appended on its own, one at a time, so the
  times in the file never go backwards.
- What the recorder needs between events lives in Valkey under
  `recording:<recording id>:*`, and `recording:of:<deployment id>` names
  the deployment's live recording; see
  [02](02-domain-model.md#ephemeral-state). When Valkey loses them, the
  bytes written are read back from the file's size and output resumes
  from the log end once the trainee's terminal attaches again.
- Revoking the link closes the recording: the row gets the file's final
  size and the seconds since time zero, and the Valkey keys are deleted. A
  deployment reaching COMPLETED, FAILED or CANCELLED revokes its links and
  closes the recording the same way, so no recording outlives it open. A
  new trainee link starts a new recording.
- A recording that cannot be written never stops the console: the agent
  still gets its answer, the terminals their bytes, and the link still
  opens.

## Storage

- Files live in the file store as
  `recordings/<deployment_id>/<recording_id>.cast`, in
  [SeaweedFS](https://github.com/seaweedfs/seaweedfs) or, when
  `JATSZOTER_SEAWEED_URL` is unset, under `data/storage/` in the API's
  working directory, as in development and tests. Each event is one append
  through the filer.
- The Recording row keeps the deployment, the trainee link, the file's
  URL, and the size and duration once the recording is closed; both are 0
  before that.
- `JATSZOTER_RECORDING_LIMIT_BYTES` is 64 MiB by default. The event that
  would take the file past it is not written; the marker `recording limit
  reached` takes its place, once, and nothing is written after it.
- A closed recording stays until it is deleted. The retention sweep that
  deletes old files and rows together arrives with roadmap item 12.

## Replay

- `GET /api/v1/deployments/{id}/recordings` lists a deployment's
  recordings, oldest first: id, link, creation time, size and duration,
  never the URL.
- `GET /api/v1/recordings/{recording_id}.cast` returns the file as
  `application/x-asciicast`. A recording of a live link is served as far
  as it is written. An unknown recording, or one whose file is gone,
  answers 404.
- Both take a signed operator request and nothing else: a link, of any
  role, never reads a recording.
- The web UI plays it with a vendored asciinema-player and lists markers
  as jump points; that page arrives with roadmap item 11.

## Acceptance criteria

- Replaying a recording shows the same screen the trainee saw, including
  resizes.
- No recording contains a key the trainee or the operator typed, other than
  what the guest echoed back.
- Every control change appears as a marker, in order with the output.
- A recording never grows past its limit, and a console whose recording
  fails keeps working.
- No link token and no session token appears in a recording.
