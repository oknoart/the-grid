# foundation decisions and open implementation details

## decisions made for Phase 1

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

## no Phase 1 blockers

The approved specification and supplied word list are sufficient to implement
and test the complete foundation phase.

## details to freeze before Phase 2 cryptographic code

The specification fixes algorithms, labels, sizes, and security behaviour, but
several byte-level interoperability details still need an explicit protocol
encoding document and fixed vectors before cryptographic code is written:

1. The canonical byte encoding and length-prefix rules for access proofs and
   session handshake transcripts.
2. The exact HKDF invocation for shorthand forms where the specification names
   an info label but does not explicitly state salt and output length.
3. The exact deterministic JSON settings for encrypted board plaintext.
4. The exact verification-code alphabet and derivation-to-eight-character map.
5. The direction-prefix constants and role ordering used in session nonces and
   transcript construction.

The proposed treatment is to define these as neutral protocol constants, use
unambiguous length-prefixed binary fields, assign roles by the server, and
publish fixed test vectors before implementing the primitives. This is a
technical specification step, not a request to change approved product
behaviour.

## non-technical repository item

The project owner has not selected a distribution licence. `LICENSE` therefore
records that the licence is pending and grants no permissions.
