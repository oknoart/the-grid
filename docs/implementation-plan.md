# implementation plan

The approved specification defines five implementation phases. The practical
build order below preserves those boundaries and places tests beside security-
critical and protocol code.

## phase 1 - foundation

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

## phase 2 - access and cryptography

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

## phase 3 - headless server and clients

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

## phase 4 - terminal client

Build in this order:

1. Define the platform-neutral terminal boundary and POSIX backend.
2. Add first-launch configuration, TLS access flow, and display selection.
3. Add canonical board rendering and safe repeated-prefix wrapping.
4. Add asynchronous input preservation and live redraw.
5. Add the approved board and private-session commands only.
6. Add plain/no-colour modes, help/status copy, signal handling, and terminal
   restoration.
7. Test two-terminal flows on macOS and Linux, including narrow widths.

Completion gate: the complete normal user flow works in two ordinary terminals
and remains understandable in plain mode.

## phase 5 - administration and deployment

Build in this order:

1. Add server init, run, status, and rotate-access command routing.
2. Add durable access/server state and rotation disconnect coordination.
3. Add metadata-only logging, rate limits, hardening, and recovery checks.
4. Add TLS/custom-CA documentation and systemd/launchd examples.
5. Add macOS and Linux CI and a clean-host deployment verification.

Completion gate: an owner can initialise, host, rotate, back up, and maintain
the personal server without source changes.
