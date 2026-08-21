# the grid

The Grid is a lightweight private terminal messaging application for one
personal server and a small group of friends.

This repository contains the completed Phase 1 foundation, Phase 2
cryptographic core, and Phase 3 headless networking/server layer from the
approved v1 product and technical specification. The interactive terminal
client belongs to Phase 4; the final server-owner CLI and deployment workflow
belong to Phase 5.

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
no-argument entry point reports implementation status rather than exposing an
unfinished interactive client.

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

- frozen canonical cryptographic encodings and fixed vectors
- immutable Scrypt/HKDF profiles and separated access, board, and display keys
- one-use access challenge proof and opaque display tokens
- deterministic encrypted board records with authenticated metadata
- phrase-authenticated ephemeral X25519 session handshakes
- directional session keys, verification codes, counters, replay rejection,
  encrypted identity exchange, text, and close events

### Phase 3 — headless server and clients

- strict bounded newline-delimited JSON protocol frames
- TLS transport with certificate verification and explicit loopback-only plain
  development mode
- hello/version negotiation, access authentication, and display reservations
- heartbeats, dead-connection cleanup, rate limits, and bounded outbound queues
- encrypted SQLite board state, posting cooldowns, expiry, and 24-message
  capacity enforcement
- spent message-ID protection so a board AEAD key/nonce pair cannot be reused
  within an access generation
- sequence-consistent paginated canonical board snapshots and live updates
- in-memory two-user waiting rooms, pairing, handshake forwarding, and encrypted
  session routing
- headless end-to-end clients used to prove the protocol without terminal UI

## tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite covers foundation behavior, fixed cryptographic vectors, adversarial
crypto cases, transport validation, TLS, SQLite board rules, live updates,
heartbeats, bounded queues, and encrypted two-client sessions.

## protocol documentation

- `docs/approved-specification.md` — immutable approved product and technical
  source of truth
- `docs/protocol-encodings-v1.md` — frozen Phase 2 cryptographic byte encodings
- `docs/cryptographic-test-vectors-v1.md` — human-readable vector index
- `tests/vectors/phase2-v1.json` — machine-readable Phase 2 fixed vectors
- `docs/protocol-transport-v1.md` — frozen Phase 3 outer transport and relay
  rules
- `docs/phase-2-report.md` — Phase 2 implementation report
- `docs/phase-3-report.md` — Phase 3 implementation report

## current boundary

The networking core is headless. `HeadlessClient` and `RelayServer` exist so the
complete encrypted network behavior can be tested, but the ordinary `grid`
command does not yet expose the interactive connection flow. Phase 4 will add
the POSIX terminal boundary, first-launch flow, Hub rendering, input
preservation, approved commands, plain/no-colour modes, and terminal restoration.
