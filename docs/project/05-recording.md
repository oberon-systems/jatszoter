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
{"version": 2, "width": 120, "height": 32, "timestamp": 1790000000, "title": "nginx-502 v1 5f0c..."}
[0.512, "o", "trainee@alpha:~$ "]
[4.031, "r", "140x40"]
[9.870, "m", "control operator"]
[21.004, "m", "check 9b2e... failed: nginx still does not serve the app page"]
```

- `o` - output bytes as the API received them from the agent.
- `r` - resize, applied from the holder of control.
- `m` - markers: control changes and check results.
- There are no `i` events: keys are never stored.

## Writing

- When the trainee link is first opened, the API opens one new object in
  SeaweedFS and streams the asciicast into it as console bytes arrive from
  the agent, with times since the first open taken from the API clock.
- It is the same stream the API fans out to the terminals. Nothing is
  buffered into a whole file first; a crash loses only the bytes in flight.
- Control changes and check results go into the stream as markers, in order
  with the output.
- Revoking the link closes the stream and writes the final size and duration
  to the Recording row. Ending the deployment revokes the link, so no
  recording outlives it.
- In tests the API streams into the local directory `JATSZOTER_FILES_DIR`.

## Storage

- Files live in [SeaweedFS](https://github.com/seaweedfs/seaweedfs) as
  `<deployment_id>/<recording_id>.cast`; the Recording row keeps the file's
  URL, its size and duration.
- When `JATSZOTER_SEAWEED_URL` is unset, the API writes the same layout to
  the local directory `JATSZOTER_FILES_DIR` instead, as in development and
  tests.
- A recording larger than `JATSZOTER_RECORDING_LIMIT_BYTES` stops taking
  output and gets a final marker saying so.
- Recordings older than `JATSZOTER_RECORDING_RETENTION_DAYS` are deleted by
  a background sweep, file and row together. Unset means keep forever.

## Replay

- `GET /api/v1/recordings/{recording_id}.cast` returns the file, signed
  operator request only.
- The web UI plays it with a vendored asciinema-player and lists markers as
  jump points.
- A recording of a live link is served as far as it is written.

## Acceptance criteria

- Replaying a recording shows the same screen the trainee saw, including
  resizes.
- No recording contains a key the trainee or the operator typed, other than
  what the guest echoed back.
- Every control change and check result appears as a marker.
