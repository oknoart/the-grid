# cryptographic test vectors v1

**Status:** fixed Phase 2 interoperability vectors
**Machine-readable source:** `tests/vectors/phase2-v1.json`

These vectors freeze protocol v1 encodings. Tests compare the implementation to
these exact values so accidental changes to field order, labels, KDF settings,
JSON serialization, transcript construction, nonce construction, or ciphertext
are detected before networking is introduced.

All hexadecimal values are lowercase and contain no separators.

## access vector

```text
phrase              velvet orbit green cabin
server_id           000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
access_generation   202122232425262728292a2b2c2d2e2f
access_root         9c7bd95e2453d7c32e08abb18ce90289d4916c667976af48ac21247a3ae675da
access_auth_key     67b3fd7e1ad1d545aea7723e9c399244ba907f6aab35edc65c7253bb7e5ebbdc
board_master_key    42b939abe7715e4b5c226833a9b21241ec7a7f57b527be1bc55eba4946dcc70c
display_token_key  8e913922f6b10e1b3d15345f4b58257e3f2be22a0d9b10133aac2e8b1b94ce1a
challenge           303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f
client_nonce        505152535455565758595a5b5c5d5e5f
proof               493b1490527db27ab6c67d9eb20487a757a5ef56676ea6434f95dec130ce28cf
display_id          J7K
display_token       d99dd341bf41ec984b7f483bfd8f627d
```

## board vector

```text
message_id          a0a1a2a3a4a5a6a7a8a9aaabacadaeaf
plaintext_utf8      {"id":"J7K","text":"are you receiving this?","v":1}
message_key         0445917820cd7e276d060ae92b4a8216df090b5ba2f7bdf8937b8c89144a648f
ciphertext          ff95c5294a39bd1f1493f7116896951026f7f0e7f2117984bcbaa899fa05b1d78643fb9d7f2718cca664c8af4b73f42f51677099ae51fd56ff92b5d70424a3fa53a981
```

The complete associated-data encoding is retained in the JSON vector file.

## live-session derivation vector

```text
phrase                  amber meadow signal copper
phrase_root             580307fa3bb47f5856d811ab02012b8e1843a29728bfafac3f7c6c407c0c7e02
auth_key                594828adc2a32d68f44553ccd1db6b13b8253703d23a7271cda84af86deff88e
room_id                 7eff0c7b2891201bac1dff1e0dc5c4bc
pair_id                 808182838485868788898a8b8c8d8e8f
creator_public_key      07a37cbc142093c8b755dc1b10e86cb426374ad16aa853ed0bdfc0b2b86d1c7c
joiner_public_key       5869aff450549732cbaaed5e5df9b30a6da31cb0e5742bad5ad4a1a768f1a67b
transcript_hash         9a7a8d13dab7bfa82cc8464c5c62de4be1f80365ffb99f691dff5f1e37f76685
creator_proof           b22d7ff49a5977acada7de9d5df1e5bfd2d66cb972455273adc1209a588a1975
joiner_proof            ae7137ae9efb4167088bbc36af7cffaeef544fdcb51fbe2f47dc15066a7fcc2b
shared_secret           a84dc7c3c8f058b1b2dc4cd1e9b5dc0a7987f88b6a9564cde3391fc421159e77
creator_to_joiner_key   307c4d37300f749ef3e8b8a8a87e66179521146d714593665f41c515cfe9f398
joiner_to_creator_key   49b2d9a454c668cca5dc11a0d591fb44726b31361f27d34e8a2db7ed983d480e
session_id              36028912c487cb4f525665bc405387fb
verification_seed       058c304ce4fba8c9a5c34628eac2f5a8af04a91f510326cc21b92c289d34ebaf
verification_code       2Q85-2M96
```

The JSON vector file additionally contains the deterministic private-key test
inputs, handshake nonces, complete canonical transcript, and exact encrypted
identity, text, and close frame bodies for counters zero through two.

## required negative checks

The vector suite is accompanied by tests proving that the following do not
silently produce accepted data:

- wrong access or comm phrase;
- changed access challenge or client nonce;
- replay of a consumed access proof;
- changed board ciphertext, message ID, display token, server ID, or access
  generation;
- decrypted board ID that does not match the outer token;
- changed handshake public key, nonce, role, or pair ID;
- replayed handshake proof in another pair transcript;
- changed session ciphertext;
- duplicate, lower, or gapped counters;
- wrong direction or session ID;
- text before encrypted identity exchange;
- use after an integrity failure or explicit cleanup.
