# implementation decisions

## Phase 1 foundation decisions

- Python import package: `the_grid`.
- Console executable: `grid`.
- Configuration directory name: `the-grid`.
- Default server port: `7331`, following the approved examples.
- Approved word-list checksum: SHA-256 of the exact supplied bytes,
  `99b2c78777db24127047b1535e13da44b7c89f24d387a1041ef09627c7ca0bc5`.
- Received phrases accept repeated ASCII whitespace and hyphens between words.
  Leading or trailing hyphens are rejected as empty-word forms.
- Received phrases must contain four distinct words, but the words do not need
  to appear in the local bundled list.
- Configuration writes are atomic and use owner-only permissions where POSIX
  permissions are available.
- No public phrase-generation, phrase-argument, custom-list, or word-list check
  command is exposed.

These are reversible implementation names or direct interpretations of the
approved specification; none changes a product or security boundary.

## Phase 2 frozen protocol decisions

The byte-level details that were open after Phase 1 are now frozen in
`docs/protocol-encodings-v1.md` and pinned by
`tests/vectors/phase2-v1.json`.

- Cryptographic field containers use a two-byte field count and four-byte
  big-endian length before every exact field, with the domain as field zero.
- Protocol version `1` is encoded as unsigned 16-bit big-endian `0x0001` inside
  cryptographic encodings.
- HKDF shorthand means HKDF-SHA256, separate invocation per output, 32-byte
  output, and `salt=None` unless another salt is explicitly stated.
- Access challenges are 32 bytes and client nonces are 16 bytes.
- The server ID file is exactly 32 raw bytes. Access verifier state is compact,
  sorted UTF-8 JSON with canonical unpadded Base64url and contains only version,
  access generation, and verifier key.
- Board plaintext uses sorted compact JSON keys, direct UTF-8 output, and strict
  duplicate/unknown-key rejection.
- Session transcript order is always creator then joiner. Role codes are `01`
  and `02`; direction nonce prefixes are `00000001` and `00000002`.
- Verification codes use
  `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` and map the first 40 seed bits to eight
  five-bit characters displayed as two groups of four.
- Encrypted session payload types are neutral internal identity, text, and close
  events rather than public command names.
- Production handshake generation always creates a fresh X25519 private key;
  deterministic private-key construction exists only as an internal test-vector
  helper.
- Creating an encrypted close event always discards the live channel material;
  there is no continue-after-close option.

Changing any item in this section requires a protocol revision and new fixed
vectors rather than an informal code change.

## no Phase 2 product blockers

The approved specification was sufficient to complete access, board, and
private-session cryptography. No settled product decision was reopened.

## non-technical repository item

The project owner has not selected a distribution licence. `LICENSE` therefore
records that the licence is pending and grants no permissions.


## Phase 3 frozen transport and relay decisions

The Phase 3 wire and server details are frozen in `docs/protocol-transport-v1.md`.

- Outer frames are one compact UTF-8 JSON object plus LF, with a 16 KiB total
  maximum and strict duplicate-key/non-finite-value rejection.
- Binary outer fields use canonical unpadded Base64url. Ordinary request/response
  frames use the same neutral type and an echoed request ID.
- TLS is required for remote operation; explicit plain transport is restricted
  to loopback development. Client/server TLS helpers require certificate
  verification and use TLS 1.2 as the minimum version.
- Operator `max_frame_bytes` is validated between 12 KiB and the 16 KiB protocol
  maximum so a valid maximum encrypted board record can always fit.
- The canonical board snapshot is internally paginated because 24 maximum-size
  encrypted records cannot fit in one 16 KiB outer frame. Pages are anchored to
  a publication sequence and restart if that sequence changes.
- Board mutation events increment one in-memory publication sequence each. On a
  capacity eviction, removal is published before the accepted update.
- Accepted message IDs are retained as opaque `board_seen_ids` tombstones for
  the current access generation. This prevents reuse of the Phase 2
  message-ID-derived key with its fixed AEAD nonce after expiry or eviction.
- The default bounded outbound queue is 64 complete frames. Slow clients are
  disconnected rather than allowed unbounded relay memory.
- Session pair IDs are server-random 16-byte values. The relay forwards but
  never verifies phrase proofs; proof validation remains end to end. A client
  that rejects a peer proof closes the opaque route.
- The relay enforces pair membership and sender direction for encrypted session
  frames and latches the opaque session ID, but counter/tag/plaintext validation
  remains at the Phase 2 receiving client.

These decisions preserve the approved one-server/one-Grid product and security
boundaries. The board pagination and spent-ID tombstones resolve implementation
constraints without changing user-visible Hub behavior.

## no Phase 3 product blockers

The approved specification was sufficient to complete the headless server and
client phase. No user-facing decision required reopening. The two implementation
constraints above were resolved internally because both approved requirements
could be preserved simultaneously.
