# the grid

The Grid is a lightweight private terminal messaging application for one
personal server and a small group of friends.

This repository currently contains the completed Phase 1 foundation from the
approved v1 product and technical specification. Networking, cryptography,
server persistence, and the interactive terminal client are intentionally not
implemented yet; they belong to later approved phases.

## requirements

- macOS or Linux
- Python 3.11 or later
- `cryptography` as the only required third-party runtime dependency

## first run

```sh
./run
```

The launcher creates `.venv`, installs the local package and its runtime
dependency, and starts the `grid` entry point. During Phase 1, the no-argument
entry point reports the implementation status rather than pretending that the
network client exists.

## implemented in Phase 1

- `pyproject.toml`, `src/` packaging, console entry point, and POSIX launcher
- platform-specific client configuration paths and strict JSON loading
- exact packaged `grid_words.txt` validation
- four-word phrase generation with distinct words
- received phrase normalisation and validation
- central public terminology and lowercase system copy
- neutral typed state and configuration models
- standard-library `unittest` coverage for the foundation

## tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests deliberately do not expose a public word-list validation command or
accept custom generation lists.

## source of truth

The approved specification is retained at `docs/approved-specification.md`.
Implementation sequencing and unresolved protocol details are recorded in
`docs/implementation-plan.md` and `docs/foundation-decisions.md`.
