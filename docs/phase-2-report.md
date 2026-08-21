# phase 2 implementation report

## status

Phase 2 — access and cryptography — is complete against the approved v1
specification. The implementation remains deliberately headless and contains no
network listener, TLS connection, SQLite board transaction, waiting-room
server, or terminal client.

## delivered components

### canonical encodings

- versioned, length-prefixed binary fields with strict decoding;
- exact protocol labels, field order, integer widths, sizes, role codes,
  direction codes, nonce prefixes, and payload type codes;
- canonical unpadded URL-safe Base64 for persisted verifier state;
- a frozen protocol-encoding document and machine-readable fixed vectors.

### access

- approved Scrypt profile and access salt construction;
- three separately labelled HKDF outputs;
- server verifier state containing only access generation and authentication
  verifier key;
- generated server ID and access generation, access rotation material, strict
  canonical state serialization, private file permissions, and create-once
  writes;
- one-use random challenges, client nonces, HMAC proof creation, constant-time
  verification, and replay rejection;
- exact three-character ID validation and 16-byte opaque display tokens.

### board cryptography

- deterministic compact UTF-8 JSON with duplicate/unknown-key rejection;
- 1,024-byte UTF-8 text enforcement and NUL rejection;
- random 16-byte message IDs and separately derived per-message keys;
- ChaCha20-Poly1305 with fixed nonce only under each unique derived key;
- associated data binding version, server, access generation, message ID, and
  outer display token;
- decrypted-ID/token recomputation and mismatch rejection.

### live-session cryptography

- approved comm-phrase Scrypt and room-ID derivation;
- production-only fresh X25519 participant generation and 16-byte handshake
  nonces, with deterministic key injection confined to an internal vector helper;
- creator-first canonical transcript including server, room, pair, roles,
  nonces, and public keys;
- role-bound HMAC phrase proofs;
- separately labelled directional keys, session ID, and verification seed;
- eight-character unambiguous verification code;
- encrypted identity-first exchange, text events, and close events;
- 64-bit counters, direction-specific nonces, authenticated session metadata,
  and terminal failure on replay, gaps, wrong direction/session, malformed
  payload, or invalid tag;
- mandatory channel discard after encrypted close creation and best-effort
  overwriting and clearing of active channel material.

## security-sensitive decisions frozen in Phase 2

1. HKDF outputs are separate invocations with SHA-256, 32-byte output by
   default, and `salt=None` except where the board message ID or session
   transcript hash is explicitly used.
2. Access challenges are 32 bytes; client nonces are 16 bytes.
3. Server access state is deterministic compact JSON; the server ID remains a
   separate exact 32-byte raw file.
4. Board JSON sorts keys and uses compact separators with direct UTF-8 output.
5. Session roles are creator `0x01` and joiner `0x02`; directional nonce
   prefixes are `00000001` and `00000002`.
6. Verification codes use the 32-character alphabet
   `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` and the first 40 verification-seed bits.
7. Session payloads use encrypted neutral binary event types for identity,
   text, and close rather than public command terminology.

These choices implement underspecified byte-level details without changing a
settled product or security boundary.

## verification

The Phase 2 suite contains 92 passing tests and covers:

- fixed access, board, handshake, session-key, verification-code, and encrypted
  frame vectors;
- wrong-phrase, mismatch, tamper, token-binding, role, transcript-replay,
  counter, direction, session-ID, invalid-tag, sequence, limit, and cleanup
  cases;
- a complete no-network Phase 2 gate that authenticates access, encrypts and
  decrypts a board post, completes a two-party handshake, exchanges encrypted
  IDs, and transfers an encrypted text event;
- independent standard-library Scrypt, HMAC, and HKDF calculations that reproduce
  the pinned access and comm vectors without importing the production glue code.

The full source suite was run with Python 3.13.5 and `cryptography` 46.0.4;
all 92 tests passed. The final wheel was then installed into a newly created
virtual environment and exercised with `cryptography` 43.0.0, including access,
board, handshake, encrypted identity/text, and encrypted close flows. The wheel
contains the exact approved word-list bytes. The launcher and installed console
entry point were also exercised. This execution environment has no external
package-index access, so a fresh network download of `cryptography` was not
retested here.

## accepted limitations inherited from the specification

- Stored verifier material can support offline phrase guessing after theft;
  Scrypt and the generated four-word phrase are the v1 defence.
- All authorised users share the board key and can construct a token for an
  available ID; v1 does not provide permanent human authorship signatures.
- Python cannot guarantee forensic RAM erasure. Cleanup is best effort and the
  implementation makes no physical-memory wiping claim.
- Board forward secrecy is not provided; private comm forward secrecy depends
  on fresh ephemeral X25519 keys being discarded correctly.

## next phase

Phase 3 connects these tested components through:

1. bounded newline-delimited JSON frames;
2. TLS and hello/version negotiation;
3. one-use access challenges and ID-token reservations;
4. the SQLite board transaction, cooldown, capacity, expiry, and live updates;
5. waiting rooms, pairing, handshake forwarding, encrypted frame routing,
   heartbeats, bounded queues, and cleanup;
6. headless integration clients before any terminal UI work.
