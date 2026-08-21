# protocol encodings v1

**Status:** frozen for implementation protocol v1
**Scope:** access, board, and live-session cryptography only
**Transport:** the newline-delimited JSON transport is defined in Phase 3 and is
not part of this document

Any change to a label, field order, length, integer representation, KDF
parameter, nonce construction, plaintext encoding, or verification-code map in
this document requires an explicit protocol-version revision and replacement
test vectors.

## 1. Common values

| Value | Encoding |
| --- | --- |
| Protocol version | unsigned 16-bit big-endian `0x0001` |
| SHA-256 / HMAC-SHA256 output | 32 bytes |
| Symmetric key | 32 bytes |
| Server ID | 32 bytes |
| Access generation | 16 bytes |
| Board message ID | 16 bytes |
| Opaque display token | 16 bytes |
| Room ID | 16 bytes |
| Pair ID | 16 bytes |
| X25519 public key | 32 raw bytes |
| Session ID | 16 bytes |
| AEAD | ChaCha20-Poly1305 with a 12-byte nonce |

All text used by a cryptographic encoding is UTF-8 unless a field below is
explicitly ASCII. Labels are the exact ASCII bytes shown, without a terminating
NUL.

## 2. Canonical length-prefixed field encoding

Cryptographic transcripts, associated data, proofs, and encrypted session
payloads use one binary container:

```text
field_count      uint16 big-endian
field_0_length   uint32 big-endian
field_0_bytes    exact bytes
...
field_n_length   uint32 big-endian
field_n_bytes    exact bytes
```

- `field_count` includes the domain field.
- Field zero is always the non-empty printable-ASCII domain label.
- The decoder requires the exact expected domain, exact field count, complete
  lengths, and no trailing bytes.
- The implementation bounds one field to 1 MiB and one container to 64 fields;
  every v1 cryptographic field is substantially smaller.

## 3. KDF conventions

### 3.1 Scrypt

Both phrase-hardening operations use:

```text
N = 32768
r = 8
p = 1
output length = 32 bytes
```

The normalised four-word phrase is encoded as ASCII.

### 3.2 HKDF-SHA256

Where this document writes `HKDF(ikm, info, salt, length)`, the exact operation
is HKDF-SHA256. Unless a section supplies another value:

- `salt = None`, which is the HKDF all-zero default salt;
- `length = 32` bytes;
- `info` is the exact ASCII label shown.

Each output uses a separate HKDF invocation rather than splitting one combined
output.

## 4. Access derivation and proof

### 4.1 Access root

```text
salt = SHA256(
    ASCII("access-kdf-v1") || server_id || access_generation
)

access_root = Scrypt(normalised_access_phrase_ascii, salt)
```

The concatenated values after the fixed label have fixed lengths and are
therefore unambiguous.

### 4.2 Separated keys

```text
access_auth_key = HKDF(access_root, info="access-auth-v1")
board_master_key = HKDF(access_root, info="board-master-v1")
display_token_key = HKDF(access_root, info="display-token-v1")
```

Only `access_auth_key` is retained by the server. The server-side field is named
`verifier_key` to avoid exposing sibling client keys through the state API.

### 4.3 Access proof

- Server challenge: 32 random bytes.
- Client nonce: 16 random bytes.
- Proof: 32-byte HMAC-SHA256.

The HMAC message is the canonical field encoding:

| Field | Value |
| --- | --- |
| Domain | `access-proof-v1` |
| 1 | protocol version `0x0001` |
| 2 | 32-byte server ID |
| 3 | 16-byte access generation |
| 4 | 32-byte challenge |
| 5 | 16-byte client nonce |

```text
proof = HMAC-SHA256(access_auth_key, encoded_fields)
```

A server challenge object is one-use. Its first verification attempt consumes
it, whether the attempt succeeds or fails.

### 4.4 Opaque display token

The display ID is normalised to exactly three uppercase ASCII characters from
`A-Z` and `2-9`.

```text
display_token = HMAC-SHA256(
    display_token_key,
    uppercase_display_id_ascii
)[:16]
```

## 5. Server access-state files

The server ID file contains exactly the 32 raw server-ID bytes.

The access-state file is compact deterministic UTF-8 JSON followed by one LF:

```json
{"access_generation":"<base64url>","v":1,"verifier_key":"<base64url>"}
```

- Object keys are sorted and no insignificant spaces are written.
- Binary values use canonical unpadded URL-safe Base64.
- Unknown keys, duplicate keys, padding, non-canonical Base64, wrong lengths,
  non-integer versions, versions other than integer `1`, and any byte-level
  representation other than the canonical form above are rejected.
- Files are created with owner-only POSIX permissions where supported.
- The state does not contain the phrase, access root, board master key, or
  display-token key.

## 6. Board plaintext and encryption

### 6.1 Plaintext object

The only accepted object has exactly these logical fields:

```json
{"id":"J7K","text":"are you receiving this?","v":1}
```

Serialization uses Python-equivalent JSON settings:

```text
sort_keys = true
separators = (",", ":")
ensure_ascii = false
allow_nan = false
UTF-8 output
```

The decoder rejects duplicate or extra keys, non-integer version values,
versions other than `1`, invalid IDs, invalid UTF-8, NUL, empty text, and text
longer than 1,024 UTF-8 bytes.

### 6.2 Per-message key

```text
message_key = HKDF-SHA256(
    ikm = board_master_key,
    salt = 16-byte message_id,
    info = "board-message-v1",
    length = 32
)
```

The server must reject duplicate message IDs. Because a unique key is derived
for every accepted ID, the board AEAD nonce is twelve zero bytes.

### 6.3 Board associated data

| Field | Value |
| --- | --- |
| Domain | `board-aad-v1` |
| 1 | protocol version `0x0001` |
| 2 | 32-byte server ID |
| 3 | 16-byte access generation |
| 4 | 16-byte message ID |
| 5 | 16-byte display token |

The plaintext is encrypted with ChaCha20-Poly1305 using `message_key`, the fixed
zero nonce, and this associated data. After decryption, the client recomputes
the display token from the plaintext ID and rejects a mismatch.

## 7. Comm phrase and room derivation

```text
salt = SHA256(ASCII("comm-kdf-v1") || server_id)
phrase_root = Scrypt(normalised_comm_phrase_ascii, salt)
comm_auth_key = HKDF(phrase_root, info="session-auth-v1")
room_id = HMAC-SHA256(comm_auth_key, ASCII("room-id-v1"))[:16]
```

Only access-authenticated clients may use a room ID at the server layer.

## 8. Ephemeral handshake

### 8.1 Roles and contribution values

| Role | Code |
| --- | --- |
| creator | `0x01` |
| joiner | `0x02` |

Each side contributes:

- one fresh X25519 key pair;
- the raw 32-byte public key;
- one random 16-byte handshake nonce.

The server contributes one random 16-byte pair ID after matching the two users.

### 8.2 Canonical transcript

The transcript is always creator first, regardless of network arrival order:

| Field | Value |
| --- | --- |
| Domain | `session-handshake-v1` |
| 1 | protocol version `0x0001` |
| 2 | 32-byte server ID |
| 3 | 16-byte room ID |
| 4 | 16-byte pair ID |
| 5 | creator role code `0x01` |
| 6 | creator 16-byte nonce |
| 7 | creator 32-byte public key |
| 8 | joiner role code `0x02` |
| 9 | joiner 16-byte nonce |
| 10 | joiner 32-byte public key |

### 8.3 Phrase proof

For each role, build:

| Field | Value |
| --- | --- |
| Domain | `session-proof-v1` |
| 1 | protocol version `0x0001` |
| 2 | role code |
| 3 | `SHA256(canonical_transcript)` |

```text
role_proof = HMAC-SHA256(comm_auth_key, encoded_fields)
```

A changed role, nonce, public key, server ID, room ID, pair ID, field order, or
protocol version changes the proof.

### 8.4 Session material

After verifying the peer proof, each side computes the X25519 shared secret.
Each value below is a separate HKDF-SHA256 invocation with
`salt = SHA256(canonical_transcript)`:

| Output | Info label | Length |
| --- | --- | --- |
| creator-to-joiner key | `session-c2j-v1` | 32 |
| joiner-to-creator key | `session-j2c-v1` | 32 |
| session ID | `session-id-v1` | 16 |
| verification seed | `session-verify-v1` | 32 |

The ephemeral private-key object is discarded after finalisation or handshake
failure.

### 8.5 Verification code

The alphabet is exactly:

```text
23456789ABCDEFGHJKLMNPQRSTUVWXYZ
```

It contains 32 characters and excludes `0`, `1`, `I`, and `O`. Treat the first
five verification-seed bytes as one 40-bit big-endian integer, divide it into
eight consecutive five-bit values from most significant to least significant,
map each value to the alphabet, and display the result as `XXXX-XXXX`.

## 9. Encrypted live-session frames

### 9.1 Directions and nonce prefixes

| Direction | Code | Four-byte nonce prefix |
| --- | --- | --- |
| creator to joiner | `0x01` | `00000001` |
| joiner to creator | `0x02` | `00000002` |

Each direction has an independent unsigned 64-bit counter beginning at zero.
The 12-byte nonce is:

```text
direction_prefix || counter_uint64_big_endian
```

### 9.2 Associated data

| Field | Value |
| --- | --- |
| Domain | `session-data-aad-v1` |
| 1 | protocol version `0x0001` |
| 2 | 16-byte session ID |
| 3 | one-byte direction code |
| 4 | eight-byte unsigned counter |

### 9.3 Encrypted payload

The plaintext is another canonical field encoding:

| Field | Value |
| --- | --- |
| Domain | `session-payload-v1` |
| 1 | protocol version `0x0001` |
| 2 | one-byte event type |
| 3 | event body |

| Event | Type code | Body |
| --- | --- | --- |
| encrypted identity | `0x01` | exactly three uppercase display-ID ASCII bytes |
| text | `0x02` | UTF-8, at most 4,096 bytes, no NUL |
| close | `0x03` | ASCII internal close-reason value |

The first successfully decrypted data payload in each direction must be an
identity. An authenticated close may abort before identity exchange. Text cannot
be sent until both encrypted identities have been exchanged.

### 9.4 Fatal receive conditions

The receiver expects the next exact counter on its ordered connection. Any of
the following permanently fails the channel and triggers best-effort key
cleanup:

- duplicate or lower counter;
- gapped counter;
- wrong direction;
- wrong session ID;
- invalid authentication tag;
- malformed payload;
- text before the required first encrypted identity;
- repeated identity.

## 10. Memory boundary

Python does not guarantee forensic erasure. The implementation avoids
intentional persistence, stores active directional keys in mutable buffers that
are overwritten on discard, drops ephemeral private-key references, resets
counters, and clears in-channel identity and verification values. This is a
best-effort application cleanup boundary, not a physical-memory erasure claim.
