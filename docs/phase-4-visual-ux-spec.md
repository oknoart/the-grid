# Phase 4 visual and UX amendment

**Status:** approved product-facing Phase 4 amendment

This document records the product and visual decisions approved after the
functional Phase 4 terminal client was validated on macOS. It supersedes the
corresponding user-facing examples and reversible implementation names in the
original approved v1 specification without modifying that historical source
file. Cryptographic, privacy, persistence, capacity, cooldown, TLS, and
one-server/one-Grid security boundaries are unchanged.

## naming and deployment boundary

- The application/client is **okno**.
- **the grid** is the single environment/network users enter.
- **the hub** is the shared public area inside the grid.
- **comm** is the only two-user private communication mode.
- The internal Python import package remains `the_grid`.
- The user-facing console executable and configuration directory are `okno`.
- Ordinary users do not choose, enter, or edit a server address. One Grid server
  is provisioned during installation/deployment. Developer/admin overrides may
  remain available for testing and maintenance.

## launch and access

The normal client opens with the approved OKNO ASCII wordmark:

```text
 ______  __  __   __   __  ______
/\  __ \/\ \/ /  /\ "-.\ \/\  __ \
\ \ \/\ \ \  _"-.\ \ \-.  \ \ \/\ \
 \ \_____\ \_\ \_\\ \_\\"\_\ \_____\
  \/_____/\/_/\/_/ \/_/ \/_/\/_____/
────────────────────────────────────
```

The underline exactly matches the wordmark width. Normal connection flow shows
`status   connecting.` and animates only the dots every 0.5 seconds through one,
two, and three dots while connection/access is in progress. Once access succeeds,
the status becomes `connected`. Access-phrase input stays hidden, then okno
explicitly prompts:

```text
enter 3 character id
>
```

There is no generated or suggested ID. Lowercase valid input may be normalised
to uppercase. Wrong length uses `id must be 3 characters`; unsupported input
uses `invalid id`; an active reservation uses `id unavailable`.

If the fixed Grid is unreachable, normal UI shows `status   offline`, `unable to
reach the grid`, and only `/retry` and `/exit`. Wrong access shows `access
denied` and returns to hidden access input without exposing attempt counts or
server details.

## terminal visual language

- Normal design width is 56 columns when available; minimum supported width is
  40 columns.
- Major headings use a centred title inside a horizontal rule.
- One blank line follows major headers, separates messages, precedes the bottom
  separator, and separates the command bar from the input instruction.
- Cyan is used for structural identity/headings, white for content, dim
  white/grey for secondary information, green for successful/secure states,
  red for errors, and amber for warnings/cooldowns. Colour remains provisional
  until the real-terminal visual review.
- `--no-color` retains layout without colour. `--plain` additionally avoids
  cursor positioning/animation and leaves the terminal's normal cursor alone.
- Normal mode requests a blinking underline terminal cursor; no literal
  underscore character is printed as a fake cursor.
- Major state changes replace the active view. Temporary commands/input are
  removed once acted on in normal mode; durable okno output remains.

## the hub

The Hub header contains the current ID/connection status plus message count and
current local time. Hub items are called **messages**; `/post` is the action that
adds one.

```text
─────────────────────── THE HUB ────────────────────────

    ABC   connected                        /\____/\
    5 messages / 14:32                     >•   • <

────────────────────────────────────────────────────────

K9R < first message

M2X < second message

────────────────────────────────────────────────────────

/post    /start    /join    /status    /help    /exit

write a message with /post
>
```

Own Hub messages receive no special colour because an ID is only a live
connection identity and may be reused later.

The cat has two exact normal-mode states:

```text
   /\____/\
   >•   • <
```

```text
   /\____/\
   > •   •<
```

Both rows are anchored to the same fixed right-side column: `>` is directly
below the first `/`, and `<` is directly below the final `\`. Only the eyes
move one character right between states. The cat starts in the first state and alternates
every 0.5 seconds while the Hub is visible. It is static in `--plain`, hidden
before narrow layouts become cramped, and does not react to events.

An empty Hub shows `0 messages / HH:MM` and a dim `no messages` line.

### posting

`/post` switches the existing input area to a single compose prompt:

```text
message >
```

No separate compose panel appears. In normal mode `/post` and the editable
compose line disappear after submission; only the canonical Hub message remains.
There is no `posted`/`sent` confirmation.

If posting is on cooldown, `/status` shows `post            available in ...`.
Trying `/post` shows concise `post available in ...` copy and does not enter
compose mode.

## comm start and join

Creator waiting view:

```text
──────────────────── START COMM ────────────────────────

comm phrase

    column parity jaeger vehicle

waiting for connection.

/cancel

────────────────────────────────────────────────────────
```

The waiting line animates only its dots every 0.5 seconds: one, two, three, then
repeat. It has no input cursor because it is passive status.

`/cancel` actually cancels the server waiting room. It requires no confirmation.
Cancellation, expiry, and unavailable join use the concise copy `comm cancelled`,
`comm expired`, and `comm unavailable` respectively. If pairing and cancellation
race, whichever the server accepts first wins cleanly.

Join view:

```text
──────────────────── JOIN COMM ─────────────────────────

comm phrase

/cancel

────────────────────────────────────────────────────────
>
```

Entering `/cancel` before a phrase returns directly to the Hub without attempting
a join. After phrase submission, `connecting.` animates through one, two, and three dots
every 0.5 seconds, then enter COMM on success.

## comm

There is no separate public/private comm taxonomy; the heading is simply
`COMM`. The compact relationship/security line mirrors the Hub metadata style:

```text
───────────────────────── COMM ─────────────────────────

    ABC × J7K / encrypted

────────────────────────────────────────────────────────

    no messages

────────────────────────────────────────────────────────

/status    /end    /help

write a message
>
```

The dim `no messages` line is shown only while the established comm has no
messages. It disappears as soon as either side sends the first message. With
messages present, the same area becomes:

```text
J7K < hello

ABC > hello. can you hear me?

────────────────────────────────────────────────────────

/status    /end    /help

write a message
>
```

The verification code is available through `/status`, not permanently shown.
There is no `/clear` command: comm history is already ephemeral and terminal
scrollback cannot honestly be presented as deleted by the application.

`/end` prompts `end comm? [y/n]`. `n` silently resumes the comm. `y` shows
`comm ended` and returns to the Hub. The peer sees `<ID> ended the comm` before
returning to the Hub. `/exit` is a Hub command; a comm must be ended first.

## status and help

Hub status contains only:

```text
server          connected
id              ABC
hub             connected
post            available
```

Comm status contains only:

```text
server          connected
id              ABC
comm            J7K
encrypted       yes
verification    QTCE-9QSD
```

Hub help lists `/post`, `/start`, `/join`, `/status`, `/help`, `/exit`. Comm
help lists only `/status`, `/end`, `/help`. Help/status are temporary views and
show `press return to go back` above the input prompt; Return restores the
underlying Hub or COMM view.

## messages, wrapping, input, and resizing

- No per-message timestamps, delivery markers, sequence numbers, or read states.
- Hub current time appears only in its metadata line; comm has no timestamp.
- Every explicit or visual wrapped line repeats `ID <` or `ID >`.
- Messages wrap vertically; they are not truncated and the UI never horizontally
  scrolls.
- One blank line separates messages.
- Normal input supports Backspace/Delete, left/right, Home/End, Ctrl-U, safe
  multi-line paste, and Ctrl-D exit only from an empty line.
- Incoming output preserves unfinished input and cursor position.
- Up/down command history is intentionally absent so prior private text cannot
  unexpectedly reappear.
- On resize, okno redraws/re-wraps the current view without duplicating messages
  or losing partial input. The cat disappears when needed. Below 40 columns the
  client shows `terminal too narrow` and `minimum width: 40 columns` until a
  usable layout can be rendered.

## exit and error copy

Hub `/exit` ends immediately with `end of line`; there is no confirmation or
`disconnecting...` screen.

Errors are specific, lowercase, and normally one line. `unknown command` is
preferred to generic help hints. Blocking failures may use a framed ERROR view.
Colour is never the sole carrier of meaning.
