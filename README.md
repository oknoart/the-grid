# okno

**okno** is the terminal client for **the grid**, a lightweight private
messaging environment hosted on one personal server for a small group of
friends. The shared public area inside the grid is **the hub**; two-user private
communication is a **comm**.

The application protocol/crypto, terminal client, server-owner administration,
macOS service deployment, and self-contained macOS distribution tooling are now
implemented through the Phase 5 release-candidate boundary.

## ordinary macOS installation

The approved friend-facing installation is one command from the public GitHub
repository:

```sh
curl -fsSL https://raw.githubusercontent.com/oknoart/the-grid/main/install.sh | sh
```

The installer detects Apple Silicon versus Intel, downloads the matching
pre-built GitHub Release, verifies SHA-256 checksums, installs the `okno`
executable, and provisions the one fixed Grid server plus its public CA.

It does **not** require the user to install Python, Homebrew, pip, Git, or a
virtual environment.

After installation:

```sh
okno
```

The public release files must exist before the installation command is usable.
Termux/Android is a possible later target; macOS is the only v1 friend-facing
distribution target.

## development requirements

- Python 3.11 or later
- `cryptography` as the only required third-party runtime dependency
- macOS or Linux for development/CI; v1 distribution support is macOS

From a source checkout:

```sh
./run
```

Developer one-launch client overrides remain available for testing:

```text
okno --server HOST:PORT
okno --ca-file PATH
okno --plain
okno --no-color
```

Access and comm phrases are always entered interactively; there is no supported
phrase command-line argument. The access phrase is hidden. Users explicitly
enter their own three-character ID; okno does not suggest one.

## server owner

The one personal Grid server is administered locally with:

```text
okno server init --public-host HOST
okno server run
okno server status
okno server rotate-access
okno server renew-tls
okno server backup --output FILE
okno server export-client --output DIR
```

`server init` creates the persistent server identity, first four-word access
phrase, encrypted SQLite Hub state, and a private Grid CA/server certificate.
The plaintext access phrase is displayed for the owner to save but is not
retained by the server.

The permanent macOS service is installed with:

```sh
./deploy/macos/install-server-service.sh
```

See `docs/phase-5-deployment.md` before configuring the production Mac Mini.

## implemented

### Phase 1 — foundation

- packaging, launcher, strict non-secret configuration, exact approved word list
- four-word phrase generation/normalisation
- central public terminology and neutral internal models

### Phase 2 — access and cryptography

- frozen cryptographic encodings and fixed vectors
- Scrypt/HKDF access separation, challenge proofs, opaque display tokens
- encrypted authenticated Hub records
- phrase-authenticated ephemeral X25519 sessions, directional AEAD, counters,
  replay rejection, verification codes, encrypted identity/text/close

### Phase 3 — headless networking

- bounded newline-delimited JSON protocol and TLS transport
- authentication, display reservations, heartbeats, rate limits, bounded queues
- encrypted SQLite Hub persistence, cooldown/expiry/capacity rules, live updates
- sequence-consistent paginated snapshots
- two-user waiting rooms, pairing, handshake forwarding, encrypted routing

### Phase 4 — okno terminal client

- approved OKNO launch wordmark and fixed-server normal connection flow
- explicit three-character ID entry with no generated suggestion
- responsive Hub/COMM layouts and exact normal-mode two-state cat animation
- preserved editable input during live updates and resize redraws
- `/post`, `/start`, `/join`, `/status`, `/help`, `/exit` in the Hub
- `/status`, `/end`, `/help` in a comm; no misleading local `/clear`
- server-backed `/cancel` while waiting to start or join a comm
- safe wrapping/paste/editing, underline cursor, colour/plain modes and terminal
  restoration

### Phase 5 — administration and deployment

- strict persistent server configuration and owner-only state
- `server init`, `run`, `status`, live/stopped `rotate-access`, TLS renewal,
  backup, and public client-profile export
- private Grid CA and hostname-verifying server TLS
- generation-bound Hub/cooldown/spent-ID state across rotations/crashes
- owner-only Unix admin socket, PID locking and metadata-only rotating logging
- launchd boot/restart service workflow for the Mac Mini
- macOS Apple Silicon/Intel frozen-release build tooling
- macOS/Linux CI and tag-driven GitHub Release workflow
- one-line GitHub installer that needs no Python/Homebrew on the recipient Mac

## tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite covers foundation behavior, fixed cryptographic vectors, adversarial
crypto cases, transport/TLS, SQLite Hub rules, live updates, sessions, terminal
safety/editing/redraw, visual layouts, server administration, access rotation,
private-CA TLS, backup/export boundaries, launchd/release files, and complete
Phase 1–5 gates.

## documentation

- `docs/approved-specification.md` — retained approved v1 historical source
- `docs/phase-4-visual-ux-spec.md` — approved later user-facing UX amendment
- `docs/protocol-encodings-v1.md` — frozen cryptographic byte encodings
- `docs/cryptographic-test-vectors-v1.md` — human-readable vector index
- `tests/vectors/phase2-v1.json` — machine-readable fixed vectors
- `docs/protocol-transport-v1.md` — v1 outer transport and relay rules
- `docs/phase-5-deployment.md` — owner deployment/release/recovery guide
- `docs/phase-5-report.md` — Phase 5 implementation report

## current release boundary

The Phase 5 code is a **release candidate**, not yet the final production claim.
The remaining gate is real-machine work: initialise the permanent Mac Mini with
a neutral public hostname, prove external TLS reachability and launchd reboot
recovery, build/measure the actual Apple Silicon and Intel frozen executables,
and perform a clean one-line installation on a separate Mac.
