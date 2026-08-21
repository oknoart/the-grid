# Phase 3 implementation and verification report

**Status:** Phase 3 completion gate satisfied in headless integration tests

Phase 3 connects the frozen Phase 2 cryptography to a bounded network protocol,
TLS transport, persistent encrypted board state, and temporary two-party
routing. It deliberately does not implement the interactive terminal client or
the Phase 5 server-owner CLI/deployment workflow.

## implemented

### transport and protocol

- strict newline-delimited UTF-8 JSON frame codec;
- 16 KiB complete-frame maximum;
- canonical unpadded Base64url binary fields;
- request/response IDs and neutral machine-facing frame names;
- hello/version negotiation before access processing;
- TLS client/server contexts with certificate verification and TLS 1.2 minimum;
- explicit loopback-only plain transport for development;
- ping/pong heartbeats and dead-connection timeout;
- bounded outbound queues and slow-client disconnection.

### access and display reservation

- one-use Phase 2 access challenge/proof over the network;
- failed-access rate limiting without candidate phrase logging;
- opaque display-token reservation without sending raw display IDs;
- active collision rejection and post-disconnect reservation leases.

### encrypted board state

- SQLite schema for ciphertext records, independent posting cooldowns, and
  opaque spent-message-ID tombstones;
- one immediate transaction for cleanup, cooldown validation, insertion,
  capacity eviction, and cooldown update;
- 24-message rolling capacity and 86,400-second expiry/cooldown rules;
- duplicate message-ID rejection even after expiry or capacity eviction;
- sequence-numbered live accepted/removal events;
- canonical paginated list snapshots with sequence-consistent restart;
- client-side decryption, token binding validation, and resync after gaps;
- restart persistence for ciphertext and cooldowns while transient reservations
  disappear.

### private sessions

- in-memory waiting rooms with 15-minute timeout;
- first-joiner two-party pairing and third-user rejection;
- forwarding of Phase 2 public handshake material and phrase proofs;
- 30-second handshake timeout;
- role/direction enforcement at the relay;
- opaque session-ID latching;
- end-to-end encrypted identity exchange, text, and close events;
- no comm SQLite table, persistence, offline queue, or session resumption;
- route cleanup on explicit close, integrity failure, timeout, or disconnect.

## implementation contradictions resolved

### 24 maximum-size board records versus a 16 KiB frame

The product requires up to 24 current messages and independently limits each
outer frame to 16 KiB. A fully populated board with maximum-size encrypted
records cannot fit in one frame. The implementation keeps both requirements by
paginating the internal `board_list` snapshot. Pagination is invisible to the
product model: clients still expose one canonical board list.

A snapshot is bound to a publication sequence. If the board changes between
pages, the client restarts; after the final page, subscription succeeds only at
the same sequence. This avoids a lost-update race.

### duplicate message IDs after removal

Phase 2 uses a message-ID-derived key and a fixed ChaCha20-Poly1305 nonce. The
approved specification says duplicate message IDs are rejected. Restricting
that check to current rows would allow a previously expired/evicted ID to reuse
the same key and nonce under the same access generation. The server therefore
stores an opaque spent-ID tombstone for every accepted message ID during that
access generation. Phase 5 access rotation must clear these tombstones with the
board and cooldowns.

Neither resolution changes a user-facing product decision or weakens a security
boundary.

## verification

The Phase 3 tests cover:

- strict codec validation, malformed data, duplicate JSON keys, and frame-size
  limits;
- incompatible protocol rejection before access authentication;
- trusted TLS connection and rejection of an untrusted self-signed certificate;
- explicit loopback-only plain development mode;
- correct/wrong access, one-use challenges, and access rate limiting;
- display-token collisions and lease expiry;
- SQLite restart persistence and transaction behavior;
- posting cooldown independent of capacity eviction;
- exact 25th-message oldest-row eviction;
- natural 24-hour expiry broadcast and cooldown expiry;
- spent message-ID rejection after records disappear;
- full 24-record maximum-size board snapshots across multiple 16 KiB pages;
- live two-client encrypted board updates and gap resynchronisation;
- waiting-room expiry and handshake timeout;
- two-user encrypted comm establishment and verification-code agreement;
- bidirectional encrypted comm text and encrypted close;
- third-user rejection;
- comm route cleanup after disconnect, bad proof, and integrity/replay failure;
- heartbeat survival, dead-peer cleanup, and bounded slow-client queues;
- CPU-hardening Scrypt work runs outside the asyncio event-loop thread so
  heartbeat processing remains responsive during access and comm phrase derivation;
- absence of any persisted comm history table.

The final source suite contains **132 tests; all 132 pass**. Wheel-install and
clean-package verification are recorded in the release handoff produced with
this phase.

## deferred by design

Phase 3 does not add:

- terminal rendering or interactive input;
- `/post`, `/start`, `/join`, `/status`, `/end`, `/help`, or `/exit` terminal
  command handling;
- server-owner `init`, `run`, `status`, or `rotate-access` CLI commands;
- launchd/systemd deployment files;
- production logging configuration;
- Windows support or any other deferred v1 feature.

Those boundaries remain assigned to Phases 4 and 5 by the approved
specification.
