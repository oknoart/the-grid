# Phase 4 implementation and verification report

**Status:** Phase 4 complete and approved after real macOS Terminal visual review

Phase 4 places the okno terminal experience on top of the Phase 3 headless
client without moving protocol, cryptography, or persistence responsibilities
into the UI layer. Server-owner administration and production deployment remain
Phase 5.

The original approved v1 specification is retained unchanged. Later explicit
user-facing decisions are recorded in `docs/phase-4-visual-ux-spec.md` and
supersede the corresponding historical UI examples/reversible names.

## terminal boundary and safety

- POSIX terminal backend using only the Python standard library;
- cancellable fd-based plain input and cbreak normal input;
- original terminal state and cursor behavior restored on normal/common failure
  paths;
- normal-mode blinking underline cursor;
- editing support for Enter, Backspace/Delete, left/right, Home/End, Ctrl-U and
  Ctrl-D-on-empty;
- safe multi-line paste with buffered trailing lines;
- incoming output, fixed-region animation, and resize redraw preserve unfinished
  input/cursor position; fixed-region updates use DEC cursor save/restore for
  macOS Terminal compatibility;
- `--plain`, `--no-color`, persisted UI preferences, and `NO_COLOR` support;
- CR/LF normalisation, tabs-to-spaces, NUL rejection, and visible neutralisation
  of terminal control characters.

## product-facing okno UX

- application/client renamed to **okno** while internal Python package remains
  `the_grid` and the environment remains **the grid**;
- normal users use one deployment-provisioned Grid server and never select a
  server during login; admin/developer overrides remain available;
- exact OKNO ASCII wordmark and matching underline on every launch;
- hidden access phrase followed by explicit `enter 3 character id`; no generated
  ID suggestion;
- responsive 56-column major layouts, 40-column minimum, re-wrapping on resize;
- Hub metadata shows current ID/connection plus `N messages / HH:MM`;
- exact two-state cat animation every 0.5 seconds in normal Hub mode, static in
  plain mode and hidden when narrow, without stealing the active input cursor;
- empty Hub and empty COMM copy `no messages`;
- one blank line between messages and repeated ID prefix on wrapped lines;
- Hub compose mode uses a single `message > ` prompt and leaves only canonical
  posted output in normal-mode scrollback;
- COMM header uses `ABC × J7K / encrypted`;
- verification code remains available through `/status`;
- `/clear` removed because local terminal clearing cannot honestly imply message
  deletion/privacy;
- `/cancel` added while waiting to start a comm and backed by an authenticated
  server request that removes the unpaired waiting room;
- waiting/connecting status dots animate one-to-three every 0.5 seconds;
- status/help explicitly say `press return to go back`;
- concise status/help/error/cooldown/transition copy from the approved amendment.

## protocol-level Phase 4 adjustments

Two small headless protocol responses/actions support the approved terminal
experience without changing security boundaries:

1. successful display reservation includes opaque-token `post_remaining` so the
   UI can accurately report a cooldown after the visible Hub message has been
   evicted;
2. authenticated `session_cancel` lets a creator remove its own still-unpaired
   waiting room. If pairing wins the race, cancellation reports unsuccessful and
   the established pair proceeds normally.

Both are documented in `docs/protocol-transport-v1.md`. Neither reveals raw IDs,
phrases, Hub plaintext, comm plaintext, or end-to-end keys.

## verification coverage

Phase 4 regression tests cover:

- terminal text normalisation/control-sequence safety;
- repeated-prefix wrapping and narrow layouts;
- POSIX entry/restore and macOS transient terminal-state behavior;
- cancellable plain reads so old state readers cannot swallow later commands;
- active-input preservation during live output;
- submitted-input deduplication in normal mode;
- multi-line paste and editing controls;
- fixed-server normal login and explicit three-character ID selection;
- approved OKNO/Hub/COMM layouts and exact cat geometry;
- two-client TLS Hub and comm flows, verification status, `/end`, direct Hub
  return and immediate `/exit` after peer-ended transitions;
- server-backed waiting-room cancellation.

The authoritative full macOS regression suite passed after the final real-terminal visual review. The approved colour palette, cat geometry/animation, launch connection animation, JOIN `/cancel`, Hub/COMM spacing, prompts and transition behavior are frozen for the Phase 5 baseline.

## deferred by design

Phase 4 does not add final `server init`, `server run`, `server status`,
`server rotate-access`, production certificate provisioning, launchd/systemd
service installation, backup/recovery workflow, persistent client logs/local
message history, or Windows support. Those remain Phase 5 or explicitly out of
v1 scope.

### Final visual-review corrections

The real macOS Terminal visual review produced three final refinements. The
launch `connecting` status now cycles through one, two, and three dots every
0.5 seconds until access succeeds, at which point it becomes `connected`. JOIN
COMM now exposes `/cancel` before phrase entry and returns directly to the Hub
without attempting a join. The Hub cat is rendered as an independent fixed-width
block with both rows anchored to the same column, so the face geometry cannot be
shifted by the message-count/time metadata.
