# Contributing

Thank you for helping with jatszoter. This page covers how to set up the
tree, what a change has to pass and how commits are written. Everyone taking
part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

- [Before you start](#before-you-start)
- [Set up the tree](#set-up-the-tree)
- [Make a change](#make-a-change)
- [Commits](#commits)
- [Branches and releases](#branches-and-releases)
- [Pull requests](#pull-requests)
- [Scenarios](#scenarios)

## Before you start

- A bug or a feature starts as an issue: pick the matching form. Small fixes
  of typos and links can go straight to a pull request.
- A vulnerability is never an issue: follow [SECURITY.md](SECURITY.md).
- A change to the architecture is proposed in `docs/project/` first and
  agreed before it is built. [AGENTS.md](AGENTS.md) holds the invariants a
  change must keep.
- The Web UI is designed in [Penpot](https://penpot.app/) and approved
  before it is built: see [dev/web/README.md](dev/web/README.md).

## Set up the tree

Install the requirements listed in the [README](README.md#requirements), then:

```bash
make install
make shell
```

`make install` builds `.venv`, installs the pinned tools and the git hooks,
commit-msg included. `make shell` opens a shell inside that venv.

## Make a change

1. Name the component that owns the change: `packages/api`, `packages/web`,
   `packages/agent`, `scenarios/` or `docs/`.
2. Define how it fails and write the tests, positive and negative.
3. Keep the change small and scoped to that component.
4. Update the docs the change makes wrong.

Run the hooks and the tests before you push:

```bash
make lint
make test
```

`make test-api`, `make test-web` and `make test-agent` run one package.
Tokens and trainee links are credentials: never log them, and never put
them in tests, fixtures or commits.

## Commits

Messages follow the `wyld_cz` [commitizen](https://commitizen-tools.github.io/commitizen/)
style, and the commit-msg hook rejects anything else:

```text
[fix][api]: sign filer calls with a JWT
```

- The type is `fix`, `feat`, `build`, `docs` or `refactor`.
- The scope is the component: `api`, `web`, `agent`, `scenarios`, `docs`,
  `make`, `docker`.
- The subject is short and imperative.
- A breaking change carries `[!]` in front and a `BREAKING CHANGE:` block.

`cz commit` asks for each part and writes the message for you.

## Branches and releases

From the 1.0.0 release on, `main` is the default branch, is protected and
holds only released code. Work happens in `dev`.

```text
dev           --o--o--o--------o--o--------o--o--o------>
                       \  FF  /      \  FF  /       \
main          --1.0.0---M--1.1.0-------M--1.2.0------M--2.0.0-->
                                               \
1.x.x-stable                                    +--1.2.1-->
```

- Pull requests target `dev`.
- A release merges `dev` into `main` with a merge commit, and the version is
  bumped and tagged on `main`. `dev` is then fast-forwarded to `main`.
- Minor and patch releases need nothing more. The increment comes from the
  commits: `feat` gives a minor, `fix` a patch.
- A major release first branches `N.x.x-stable` off `main`, then merges
  `dev`. A patch to the old major goes into its `N.x.x-stable` branch and is
  tagged there.
- The packages keep their own versions, `api-X.Y.Z`, `web-X.Y.Z` and
  `agent-X.Y.Z`, but their major follows the release major.

## Pull requests

- One topic per pull request, branched from `dev`, or from `N.x.x-stable`
  for a patch to an older major.
- The title follows the commit format, `[type][scope]: subject`: it becomes
  the title of the merge commit. CI checks it with `cz check`.
- Fill in the template: what changed, why, and the linked issue.
- `make lint` and `make test` pass, and CI is green. A pull request into
  `main` also runs `make lint-all`, `make test` and `make smoke`, and cannot
  be merged until they pass.
- Pull requests are merged with a merge commit, never squashed or rebased.
- A maintainer reviews and merges it.

## Scenarios

A scenario is a directory under `scenarios/`; the format is in
[docs/project/03-scenario-format.md](docs/project/03-scenario-format.md).

- `id` equals the directory name, and `version` is bumped on every change.
- `check.sh` exits 0 only when the problem is fixed, and fails on the broken
  VM straight after bootstrap.
- Steps run as root inside the guest: they never reach the host or the
  network beyond what the scenario needs.
- Run the scenario end to end on a local stand before you open the pull
  request: it bootstraps, the check fails, the fix passes it.
