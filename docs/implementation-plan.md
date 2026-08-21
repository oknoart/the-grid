# implementation plan

The approved specification defines five implementation phases. The practical
build order below preserves those boundaries and places tests beside security-
critical and protocol code.

## phase 1 - foundation — complete

1. Establish the package, launcher, console entry point, and test harness.
2. Add strict client configuration models, platform paths, loading, saving, and
   approved configuration keys.
3. package and byte-pin the approved 2,048-word source.
4. Implement four-word generation and received-phrase normalisation.
5. Centralise public terminology and system-written lowercase copy.
6. Define neutral internal state and configuration models.
7. Verify installation, package resources, phrase generation, and tests from a
   clean checkout.

Completion gate: a clean checkout installs and generates valid four-word
phrases from the exact approved bundled file.

## phase 2 - access and cryptography — complete

Build in this order:

1. Freeze canonical encodings, labels, field ordering, lengths, and test vectors.
2. Implement access-root derivation and labelled key separation.
3. Implement access challenge-response and opaque display tokens.
4. Implement board message serialisation, encryption, associated data, and
   token binding.
5. Implement session phrase derivation, X25519 transcript authentication,
   directional keys, verification code, counters, and encrypted close events.
6. Add mismatch, tamper, replay, role-swap, counter, and cleanup tests before
   any networking is introduced.

Completion gate: all access, board, and live-session cryptography passes fixed
vectors and failure tests without networking.

## phase 3 - headless server and clients — complete

Build in this order:

1. Implement the bounded newline-delimited JSON frame codec.
2. Add TLS connection setup, hello negotiation, access authentication, and
   heartbeats.
3. Add opaque display reservation leases.
4. Implement SQLite board storage and the single immediate posting transaction.
5. Add canonical list reads, subscriptions, sequence handling, and refresh.
6. Add waiting rooms, two-user pairing, handshake forwarding, session routing,
   bounded queues, and cleanup.
7. Exercise all behaviour through headless integration clients.

Completion gate: headless clients authenticate, exchange encrypted live board
updates, enforce capacity and cooldowns, and route encrypted sessions.

## phase 4 - okno terminal client — complete

Build in this order:

1. Define the platform-neutral terminal boundary and POSIX backend.
2. Add fixed-server TLS access flow and explicit three-character display selection.
3. Add canonical Hub rendering and safe repeated-prefix wrapping.
4. Add asynchronous input preservation, responsive redraw, and terminal editing.
5. Add the approved Hub/comm command sets and server-backed waiting cancellation.
6. Add the approved okno visual/UX amendment, plain/no-colour modes, help/status
   copy, signal handling, and terminal/cursor restoration.
7. Test two-terminal flows on macOS and Linux, including narrow widths, then
   perform the final real-terminal colour/visual review.

Completion gate: the complete normal user flow works in two ordinary terminals
and remains understandable in plain mode.

## phase 5 - administration and deployment — implementation complete; real deployment gate pending

Build in this order:

1. Add server init, run, status, live/stopped rotate-access, TLS renewal, backup,
   and public client-profile export command routing. — complete
2. Bind durable Hub/cooldown/spent-ID state to the access generation and
   coordinate live rotation/disconnect through an owner-only local admin socket.
   — complete
3. Add PID locking, metadata-only rotating logs, private-key permission checks,
   TLS validation, and backup/recovery checks. — complete
4. Add a private Grid CA, launchd service template/install workflow, and detailed
   macOS owner deployment/recovery documentation. — complete
5. Add macOS/Linux CI, Apple Silicon/Intel frozen-release build tooling, tag-driven
   GitHub Release assets, and the one-line macOS installer. — complete in code
6. Initialise and validate the permanent Mac Mini, public network path, launchd
   reboot/restart behavior, real frozen artifact sizes, and clean friend-machine
   installation. — pending real-machine Phase 5 acceptance

Completion gate: an owner can initialise, host, rotate, back up, and maintain
the personal server without source changes, and a fresh supported Mac can
install the published client without Python/Homebrew setup.
