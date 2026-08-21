# phase 1 implementation report

Date: 19 August 2026

## status

Phase 1 - foundation is implemented and its completion gate is satisfied at the
code, package-resource, and test levels.

## source material

- Approved specification SHA-256:
  `509909d44a5e0626284e09372209556293dcc854eb95cf9109f66b3bdf29372e`
- Approved `grid_words.txt` SHA-256:
  `99b2c78777db24127047b1535e13da44b7c89f24d387a1041ef09627c7ca0bc5`
- Approved word count: 2,048 unique lowercase ASCII words.

## implemented

- Python 3.11+ `src/` package with `grid` console entry point.
- POSIX `./run` launcher and isolated `.venv` workflow.
- `cryptography` as the sole third-party runtime dependency.
- Linux and macOS client configuration paths.
- Strict, atomic, owner-only JSON configuration storage.
- Exact bundled word-list byte validation and package-resource loading.
- Cryptographically secure four-distinct-word generation.
- Four-distinct-word received-phrase normalisation without local-list
  membership requirements.
- Central public terminology and approved lowercase system copy.
- Neutral typed state and configuration models.
- Structural module boundaries for later approved phases, without placeholder
  product features.

## verification

- 49 standard-library unit tests pass.
- An editable package build succeeds.
- A distributable wheel builds successfully.
- The wheel contains `the_grid/data/grid_words.txt` with the approved bytes.
- The wheel installs into a new virtual environment.
- The installed `grid` console entry point loads all 2,048 approved words and
  generates four distinct approved words.
- A copied-checkout launcher run and the full test suite pass when the sandbox's
  already-installed build/runtime packages are supplied to the isolated test
  process.

The sandbox cannot contact the Python package index, so a real network download
of `setuptools` and `cryptography` could not be exercised here. The normal
launcher path uses `pip` to retrieve them when they are not already available;
no dependency is vendored or silently replaced.

## not implemented yet

Access cryptography, board cryptography, live-session cryptography, networking,
SQLite storage, terminal interaction, and server administration remain in
Phases 2-5 exactly as specified.
