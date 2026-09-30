# 08 - Agent: image cache checked by digest

Read `promts/00-README.md` first; its ground rules apply. Then read VM runtime
in `docs/project/01-architecture.md` and Guest and host in
`docs/project/06-security.md`.

## Goal

Base images are downloaded once per digest into
`<state_dir>/images/<digest>.qcow2`, verified while they stream, and shared by
every VM that uses them.

## Established facts

- `Config.state_dir` is a private (0700) directory; `prepare_private_dir`
  creates and checks one.
- `config::parse_api_url` holds the https-or-loopback rule for the API URL.
- `HttpApi` uses a client with a 10 s total timeout, unfit for large
  downloads; the cache gets its own client.
- `AgentError` is in `libs/error/mod.rs`.

## Scope

- `Cargo.toml`: reqwest feature `stream`; tokio features `fs`, `io-util`,
  `sync` as needed; `futures-util` if the stream needs it.
- `libs/config`: factor the https-or-loopback check into a helper the cache
  reuses; `JATSZOTER_AGENT_IMAGE_LIMIT_GIB` (default 20, bounded 1..=512).
- `libs/error`: an `Image(String)` variant.
- `libs/images/mod.rs`, `ImageCache`:
  - `new(dir, limit_bytes)` creates `dir` private;
  - `async fn ensure(&self, url: &Url, digest: &str) -> Result<PathBuf>`:
    - digest must be 64 lowercase hex, else an error before any request;
    - an existing `<digest>.qcow2` is returned at once;
    - else stream into `<digest>.part-<random>` (0600) hashing as it goes,
      abort over the limit, compare the digest, `rename` into place;
      any failure removes the part file;
    - concurrent `ensure` calls for one digest share a single download
      (a per-digest `tokio::sync::Mutex` in a map);
    - client: connect timeout 10 s, read timeout 60 s, no total timeout,
      redirects only to URLs that pass the same scheme rule.
  - on `new`, stale `*.part-*` files are removed.
- No pruning of unused images; that belongs to item 12.
- Tests in `libs/images/tests.rs` with `wiremock` and `tempfile`.

## Failure behaviour

A digest mismatch leaves nothing under the final name and returns an error
naming the digest, not the URL's query string. The caller (prompt 10) turns
it into a FAILED deployment.

## Tests

Positive: a download lands under the digest name with mode 0600; a second
`ensure` makes no request; two concurrent `ensure` calls make exactly one
request (`wiremock` `expect(1)`).

Negative: wrong digest leaves no file; body over the limit leaves no file;
`http://192.0.2.1/...` is refused without a request; a malformed digest is
refused; a 404 is an error; stale part files vanish on `new`.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green.
