# 05 - Recording

Every trainee session is recorded as an
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
{"version": 2, "width": 120, "height": 32, "timestamp": 1790000000, "title": "nginx-502 v1 dep_..."}
[0.512, "o", "trainee@alpha:~$ "]
[4.031, "r", "140x40"]
[9.870, "m", "control operator"]
[21.004, "m", "check chk_... failed: nginx still does not serve the app page"]
```

- `o` - output bytes as the API received them.
- `r` - resize, applied from the holder of control.
- `m` - markers: control changes and check results.
- There are no `i` events: keys are never stored.

## Writing

- The API opens the file when the session starts and appends every output
  chunk with the time since session start, taken from the API clock.
- The file is flushed at least once a second, so a crash loses at most the
  last second.
- Closing the session writes the final size and duration to the Recording
  row. A session never outlives its deployment.

## Storage

- Files live under `JATSZOTER_RECORDINGS_DIR` as
  `<deployment_id>/<recording_id>.cast`; metadata lives in PostgreSQL.
- A recording larger than `JATSZOTER_RECORDING_LIMIT_BYTES` stops taking
  output and gets a final marker saying so.
- Recordings older than `JATSZOTER_RECORDING_RETENTION_DAYS` are deleted by
  a background sweep, file and row together. Unset means keep forever.

## Replay

- `GET /api/v1/recordings/{recording_id}.cast` returns the file, operator
  token only.
- The web UI plays it with a vendored asciinema-player and lists markers as
  jump points.
- A recording of a session still open is served as far as it is written.

## Acceptance criteria

- Replaying a recording shows the same screen the trainee saw, including
  resizes.
- No recording contains a key the trainee or the operator typed, other than
  what the guest echoed back.
- Every control change and check result appears as a marker.
