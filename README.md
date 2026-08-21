# the grid

The Grid is a lightweight private terminal messaging application for one
personal server and a small group of friends.

This repository contains the completed Phase 1 foundation and Phase 2
cryptographic core from the approved v1 product and technical specification.
Networking, SQLite server behaviour, and the interactive terminal client are
intentionally not implemented yet; they belong to Phases 3 and 4.

## requirements

- macOS or Linux
- Python 3.11 or later
- `cryptography` as the only required third-party runtime dependency

## first run

```sh
./run
```

The launcher creates `.venv`, installs the local package and its runtime
dependency, and starts the `grid` entry point. At the current phase, the
no-argument entry point reports implementation status rather than pretending
that the network client exists.

## implemented

### Phase 1 — foundation

- `pyproject.toml`, `src/` packaging, console entry point, and POSIX launcher
- platform-specific client configuration paths and strict JSON loading
- exact packaged `grid_words.txt` validation
- four-word phrase generation with distinct words
- received phrase normalisation and validation
- central public terminology and lowercase system copy
- neutral typed state and configuration models

### Phase 2 — access and cryptography

- frozen canonical binary encodings and labelled protocol constants
- immutable Scrypt and HKDF profiles for access and comm phrases
- separate access-authentication, board-encryption, and display-token keys
- one-use access challenge verification and replay rejection
- versioned server verifier state that does not contain the phrase or board key
- opaque 16-byte display tokens
- deterministic board JSON, per-message keys, ChaCha20-Poly1305, associated
  metadata, and decrypted-ID/token binding
- phrase-authenticated ephemeral X25519 comm handshakes
- canonical role-bound transcripts and HMAC phrase proofs
- separate directional keys, session IDs, and verification codes
- strict 64-bit counters, direction-specific nonces, replay/gap rejection, and
  encrypted identity, text, and close events
- fixed interoperability vectors, independent vector cross-checks, and mismatch,
  tamper, replay, role, counter, and cleanup tests

## tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests deliberately do not expose a public word-list validation command,
custom generation lists, phrase command-line arguments, or network behaviour
before its approved phase.

## protocol documentation

- `docs/approved-specification.md` — immutable approved product and technical
  source of truth
- `docs/protocol-encodings-v1.md` — frozen Phase 2 byte encodings and constants
- `docs/cryptographic-test-vectors-v1.md` — human-readable vector index
- `tests/vectors/phase2-v1.json` — machine-readable fixed vectors
- `docs/phase-2-report.md` — implementation and verification report

## current boundary

Phase 2 contains no listener, remote connection, SQLite Hub, waiting-room
server, or interactive terminal flow. Those boundaries are deliberate. Phase 3
will connect the tested cryptographic components through bounded JSON frames,
TLS, server state, and headless clients.
