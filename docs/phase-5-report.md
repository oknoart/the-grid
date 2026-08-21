# Phase 5 implementation and verification report

**Status:** Phase 5 code complete as a release candidate; real Mac Mini/public-network and frozen-macOS release gates remain

Phase 5 turns the validated Phase 4 application into an operable one-person
server deployment and a friend-installable macOS product without changing the
v1 cryptographic protocol or message privacy model.

## Server-owner CLI

The `okno` executable now includes local administration commands:

```text
okno server init
okno server run
okno server status
okno server rotate-access
okno server renew-tls
okno server backup
okno server export-client
```

Normal client use remains `okno`. Server administration is not exposed inside
the friend-facing terminal UI.

## Persistent state and rotation safety

- strict versioned server configuration;
- owner-only state directory, server identity, access verifier and private keys;
- SQLite Hub storage is explicitly bound to an access generation;
- a generation change atomically clears Hub messages, cooldowns and spent
  message IDs;
- the running server exposes an owner-only Unix admin socket and PID lock;
- live access rotation persists new verifier state, disconnects old clients,
  clears ephemeral/session state and resets generation-bound Hub state;
- restart after an interrupted rotation cannot silently revive old-generation
  Hub/cooldown data;
- a live PID lock prevents a failed admin-socket request from being mistaken for
  a stopped server and prevents unsafe offline mutation.

## TLS

- server initialisation validates the dedicated state target before showing an
  access phrase and refuses non-empty/symlinked targets;
- server initialisation creates a private P-256 Grid CA and hostname-bound
  P-256 server certificate;
- private keys are unencrypted for unattended service startup but restricted to
  owner-only filesystem permissions;
- startup validates certificate/key match, CA signature, SAN/public-host match,
  validity dates and private-key permissions;
- certificate renewal keeps the same private CA, so installed clients retain
  trust across normal server-certificate renewal;
- only the public CA certificate is exported for client distribution.

## Runtime hardening and logging

- remote `server run` always supplies TLS to the existing relay layer;
- runtime PID locking prevents accidental double-starts;
- owner-only local admin socket is separate from the network protocol;
- metadata-only rotating logs intentionally exclude phrases, IDs, message IDs,
  Hub/comm plaintext, ciphertext payloads and keys;
- existing connection limits, rate limits, heartbeat/dead-client cleanup,
  bounded frames/queues, and encrypted Hub/session rules remain in force.

## Backup and recovery

`okno server backup` creates an owner-only compressed operational set. SQLite is
snapshotted through its backup API. Runtime sockets/PID/log files are excluded.
Recovery procedure and privacy limitations are documented in
`docs/phase-5-deployment.md`.

## macOS service deployment

`deploy/macos/` contains a launchd LaunchDaemon template plus install/remove
scripts. The service runs as the server-owner account, starts at boot without a
graphical login, and is kept alive by launchd.

## Friend distribution

- product version advances to `0.5.0`;
- `cryptography` remains the only third-party runtime dependency;
- PyInstaller is an optional release-build dependency only;
- the macOS release build creates a self-contained terminal executable and
  architecture-specific tarball;
- GitHub Actions CI covers macOS/Linux and Python 3.11/3.14;
- the tag release workflow builds Apple Silicon and Intel artifacts and combines
  them with the deployment-provisioned public hostname, port and CA certificate;
- root `install.sh` detects Mac architecture, downloads the latest matching
  GitHub Release, verifies SHA-256 hashes, installs `/usr/local/bin/okno`, and
  provisions the one fixed Grid connection;
- users do not install Python, Homebrew, pip or a virtual environment;
- the same one-line installer is also the v1 update mechanism.

## Public-source privacy boundary

The production endpoint/CA are release-profile values rather than committed
server state. Private keys, access verifier material, phrases and backups are
never release assets. Public project publication still requires a one-time Git
history/metadata anonymity audit before the repository is made public.

## Automated coverage added in Phase 5

Phase 5 adds tests for:

- strict server configuration/path/permission handling;
- private-CA generation, hostname/IP SAN binding, key matching and renewal;
- database access-generation binding, legacy/unbound-state clearing and safe
  generation changes;
- live local status and live rotation against a real TLS relay;
- old-phrase rejection and new-phrase acceptance after rotation;
- public-only client-profile export;
- SQLite-safe owner-only backups with no runtime/log files;
- one-line installer properties and shell syntax;
- launchd template/service scripts;
- CI/release workflow architecture coverage;
- an end-to-end Phase 5 owner gate covering init, run, status, Hub use, live
  rotation, backup and client-profile export.

The real Phase 5 completion gate is intentionally not claimed from container
unit tests alone. It requires the permanent Mac Mini deployment and real frozen
macOS artifacts described in `docs/phase-5-deployment.md`.
