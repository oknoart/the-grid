# The Grid - Version 1 Product and Technical Specification

**Status:** Approved v1 specification

**Approved source:** Review Draft 3, approved without further changes on 19 August 2026.

> This Markdown file is the lightweight source-of-truth handoff for future ChatGPT work. It contains the approved Draft 3 specification without page-layout formatting.

*Approved message language*

```text
the hub
ABC < are you receiving this?
J7K < yes.

private comm
ABC > are you receiving this?
J7K < yes.
```

| Document field | Value |
| --- | --- |
| Status | Approved v1 specification |
| Target release | v1 |
| Deployment model | One personal/home server, one Grid, one Hub |
| Target platforms | macOS and Linux desktop terminals |
| Runtime | Python 3.11 or later |
| Runtime dependency | cryptography |
| Executable placeholder | <app> |
| Phrase format | Four words for access and comm phrases |
| Bundled phrase source | grid_words.txt - 2,048 approved words |
| Document date | 19 August 2026 |

> **Requirement language**
> Must means required for v1. Should means strongly preferred unless implementation testing reveals a practical reason to change it. May means optional or safe to defer.

# Draft 3 revision summary

Draft 3 consolidates the Review Draft 2 feedback into a smaller personal-deployment model. The changes are applied across terminology, user flows, cryptography, protocol, storage, server administration, tests and acceptance criteria rather than only at the annotated paragraphs.

| Revision | Draft 3 treatment |
| --- | --- |
| One Grid | Each server instance hosts one Grid and one Hub. Multi-Grid creation, selection and registry behaviour are removed from v1. |
| One personal server | The intended deployment is a home or personal server shared with friends. Normal user-facing material calls it the server; relay remains an internal technical function. |
| No Portal | Users launch, connect, enter the access phrase, choose an ID and arrive directly at the Hub. Portal and leave-the-Grid states are removed. |
| ID | ID replaces Disc throughout user-facing terminology. IDs remain transient three-character uppercase values. |
| /end | /end replaces /derez as the only intentional command for finishing a comm. Confirmation is end comm? y/n. |
| Hub capacity | The Hub contains at most 24 current messages. A 25th accepted message evicts the oldest, even if it is less than 24 hours old. |
| Posting cooldown | An ID may post once every 24 hours. Early Hub eviction does not end that ID's cooldown. |
| Hub rendering | Hub messages always use <. Private comm markers remain viewer-relative. Every displayed visual line repeats ID and marker. |
| Live Hub | Accepted posts appear automatically for connected users. Capacity eviction and expiry also update the open Hub view. |
| Access phrase | The server owner receives one generated four-word access phrase at initialisation and gives it privately to authorised friends. It remains valid until deliberately rotated. |
| Phrase consistency | Access and comm phrases both use four distinct words from the approved 2,048-word list. |
| Server administration | Initialisation and access rotation are explicit local administrator actions. Rotation clears the active Hub, resets posting cooldowns and disconnects old sessions. |
| Status | Status remains available as a useful secondary command, but no separate settings screen is required. |

# Contents

| Sections 1-15 | Sections 16-30 |
| --- | --- |
| 1. Product summary | 16. Server transport and protocol |
| 2. Approved v1 decisions | 17. Server state and storage |
| 3. Terminology and interface style | 18. Default limits |
| 4. Product goals | 19. Terminal interface and text safety |
| 5. Explicit non-goals | 20. User-facing copy and errors |
| 6. System model | 21. Logging |
| 7. Trust and privacy model | 22. Configuration |
| 8. Phrase and access system | 23. Installation and deployment |
| 9. Server lifecycle and administration | 24. Suggested repository structure |
| 10. ID specification | 25. Application state machines |
| 11. Primary user flows | 26. Testing requirements |
| 12. Command-line interface | 27. Acceptance criteria |
| 13. The Hub | 28. Implementation order |
| 14. Private comms | 29. Deferred possibilities |
| 15. Cryptographic specification | 30. Final v1 definition |

# 1. Product summary

The application is a lightweight terminal messaging system intended primarily for one person to host on a home or personal server and share with friends. One server instance provides one Grid, one public encrypted Hub and temporary private two-user comms.

*v1 deployment model*

```text
friends' clients  -- outbound TLS -->  personal server
                                      |
                                      +-- one encrypted Hub
                                      |   max 24 current messages
                                      |
                                      +-- temporary private comm routing
```

- The server owner initialises the server once and receives a generated four-word access phrase.
- The owner privately gives the server address and access phrase to friends who should be able to connect.
- A user launches the client, connects to the Grid, enters the access phrase, chooses a transient three-character ID and arrives directly at the Hub.
- The Hub is one live public list. Every connected user can read every current Hub message.
- An ID may post once every 24 hours. The Hub keeps at most 24 current messages, so an older message may be removed before its nominal 24-hour lifetime.
- A private comm connects exactly two users and uses a separate generated four-word comm phrase.
- The server routes encrypted comm frames so users do not need to expose public IP addresses or configure routers.
- Hub messages are encrypted before storage. Comm messages are end-to-end encrypted and are never intentionally persisted.
> **v1 in one sentence**
> Launch, connect with the shared access phrase, choose an ID, view the Hub, and optionally start or join a private comm.

# 2. Approved v1 decisions

| Area | Approved v1 decision |
| --- | --- |
| Deployment | One personal/home server instance shared with friends. |
| Grid model | One Grid per server instance; no client-side Grid creation or selection. |
| Hub model | One public encrypted live message list for that Grid. |
| Hub capacity | Maximum 24 current messages. Accepting a new message at capacity removes the oldest. |
| Hub lifetime | A message normally remains for 24 hours but may be removed earlier by capacity eviction. |
| Posting rule | One accepted Hub post per ID in any rolling 24-hour period. Early eviction does not shorten the cooldown. |
| Public identifier | ID: exactly three uppercase characters from A-Z and 2-9; transient per connection. |
| Private exchange | A two-user live comm. |
| Ending a comm | /end followed by the concise confirmation end comm? y/n. |
| Access phrase | One long-lived generated four-word phrase established at server initialisation and shared privately by the owner. |
| Comm phrase | One generated four-word phrase for each new comm. |
| Phrase source | The single bundled approved grid_words.txt list containing 2,048 words. |
| Manual phrases | Not allowed when creating access or comm phrases. Received phrases are entered normally. |
| Hub rendering | All Hub visual lines use ID < message. |
| Comm rendering | > means my line and < means the other user's line. |
| Multiline rendering | Every displayed visual line repeats the ID and marker, including wrapped lines. |
| Live updates | New posts, expiry and capacity eviction update connected Hub views automatically. |
| Status | Available as a secondary command in the Hub and comm. |
| Settings | No dedicated settings screen; a small configuration file and CLI flags cover technical preferences. |
| TLS | Required by default for remote connections. Plain transport allowed only for explicit local development. |
| Comm storage | Client memory and live server routing memory only. |
| Hub storage | Encrypted SQLite records on the personal server. |
| Platforms | macOS and Linux desktop terminals. Windows remains outside v1. |
| Installation | ./run creates the environment, installs cryptography and launches the client. |

# 3. Terminology and interface style

## 3.1 Public terminology

| Term | Meaning in v1 |
| --- | --- |
| user | A person using the client. |
| the Grid | The complete shared environment provided by one server. A user connects to it; it is not a selectable room. |
| the Hub | The main public view containing the current shared message list. |
| server | The owner-hosted machine and service that accepts connections, stores Hub ciphertext and routes comm traffic. |
| ID | A transient three-character public identifier chosen for the current connection. |
| comm | A private live exchange between exactly two users. |
| access phrase | The shared four-word phrase required to connect to the Grid and decrypt the Hub. |
| comm phrase | The separate four-word phrase used to rendezvous for one private comm. |
| /end | The intentional user command that finishes the active comm and returns to the Hub. |
| status | A concise view of connection, ID, Hub posting availability and comm details. |
| end of line | The final text shown when the application exits. |

## 3.2 Lowercase user-facing copy

System-written interface text must be lowercase by default. This includes headings, labels, prompts, status messages, warnings, errors, help text and command names.

- ID values remain uppercase because uppercase is part of the ID data format.
- User-authored Hub and comm text is preserved after safety normalisation; the application does not force it to lowercase.
- Technical identifiers, protocol constants, environment variables, file names and Python symbols keep the casing required by their format.
- The specification may use ordinary document capitalisation in explanatory prose; quoted interface copy follows the lowercase UI rule.
*example system copy*

```text
the grid
connecting...
access phrase: ••••• ••••• ••••• •••••
id: ABC
connected

the hub
```

## 3.3 Message visual language

The default message presentation at every terminal width is ID + direction marker + message. Right-aligned speech bubbles are not part of v1.

| Context | Marker rule | Example |
| --- | --- | --- |
| Hub | Every displayed Hub line uses <. The Hub does not attempt to identify a post as mine, even when its ID matches the current ID. | ABC < public Hub message |
| Private comm | > means sent by this client; < means received from the other user. | ABC > my line / J7K < their line |

Every visual line repeats the ID and marker. This applies both to explicit line breaks in a message and to lines created by terminal wrapping.

*multiline and wrapped rendering*

```text
ABC < this is a longer Hub message that wraps
ABC < onto another displayed line.
ABC < an explicit new line is also prefixed.
```

## 3.4 Terminal-native visual identity

- ASCII art or an ASCII wordmark at launch and selected major transitions.
- Simple ASCII separators and labels where they improve orientation.
- Intentional spacing and a small restrained ANSI colour palette.
- System terminal fonts rather than bundled typefaces.
- Line-oriented output that remains understandable without colour or cursor control.
- No heavy TUI framework, mouse interface, pane manager or full-screen dashboard.
## 3.5 Internal terminology

Protocol fields, database columns and cryptographic labels must remain neutral so public language can change later without a protocol migration.

| Public concept | Recommended internal name |
| --- | --- |
| the Grid | environment |
| the Hub | board |
| Hub message | board_message |
| ID | display_id |
| comm | live_session |
| access phrase | access_phrase |
| comm phrase | session_phrase |
| /end | user_close |
| server relay function | relay |

Public terms and complete interface copy should live in central modules such as terms.py and ui_text.py. The internal relay function may still be described as a relay in code and protocol documentation, while ordinary users see server.

# 4. Product goals

- Simple to clone, install, launch and understand.
- Designed first for one owner-hosted personal server and a small group of friends.
- No accounts, central identity provider or permanent user database.
- Private from the server at the message-content level under normal operation.
- Clear about what encryption, expiry, capacity eviction and /end do not guarantee.
- Fast, low-friction terminal use with very few concepts and commands.
- Distinctive through ASCII art, ASCII logos, separators, spacing and restrained ANSI colour.
- Usable in ordinary macOS and Linux terminals without a heavy TUI dependency.
- Built mainly from the Python standard library with cryptography as the only required runtime dependency.
- Structured so terminology and a future Windows terminal backend can change without rewriting the protocol or cryptography.
# 5. Explicit non-goals

- Multiple Grids or Hubs inside one server instance.
- User-created Grids, Grid discovery, tenant administration or a hosted multi-community platform.
- Windows, mobile or Termux support in v1.
- Group comms or more than one active comm per user.
- Offline private messages, file transfer, images, audio, voice or video.
- Permanent accounts, password recovery, persistent profiles or permanent ID ownership.
- Direct comm requests by ID, a searchable user directory or a contact list.
- Recipient-locked Hub items, private Hub posts or structured comm invite objects.
- Hub channels, threads, reactions, editing, replacement or user-controlled early deletion.
- Persistent private comm history, read receipts or typing indicators.
- Custom phrase word lists or a public word-list validation command.
- Automatic scheduled access-phrase rotation.
- Anonymity from the server, protection from a compromised endpoint, or protection from a person who knows the access phrase.
- Guaranteed erasure from screenshots, clipboards, terminal scrollback, backups or modified clients.
# 6. System model

## 6.1 Personal server

The server is a self-hostable Python service running on the owner's home or personal server. It is the common meeting point for every client.

- Accepts TLS client connections.
- Authenticates knowledge of the shared access phrase without requiring accounts.
- Maintains one Grid and one Hub.
- Stores encrypted Hub records and posting cooldown records in SQLite.
- Reserves active opaque ID tokens.
- Matches two users who provide the same comm phrase and forwards their encrypted comm frames.
- Enforces size, capacity, cooldown, timeout and rate limits.
- Does not intentionally log plaintext messages, phrases, raw IDs or encryption keys.
> **Why the server remains necessary**
> Without the server, there is no shared place for the Hub and no simple way for two users behind normal routers to find each other. Removing it would require direct peer-to-peer networking, public IP exchange, port forwarding or a different intermediary service.

## 6.2 One Grid

Each server instance provides exactly one Grid. The Grid is the overall shared environment associated with that server, not a selectable namespace.

- No normal client command creates a Grid.
- No Grid list, registry, browser or selection screen exists.
- All authorised users on that server connect to the same Grid.
- Running a separate unrelated Grid requires a separate server instance, configuration and database; this is an operator deployment choice, not a v1 in-app feature.
## 6.3 One Hub

The Hub is the main public view of the Grid. It contains one rolling list of current messages visible to every authorised user.

- Maximum 24 current messages.
- A message normally remains for 24 hours.
- Accepting a message at capacity removes the oldest current message early.
- One accepted post per ID in each rolling 24-hour period.
- All Hub content is encrypted from the server but readable by every user with the access phrase.
- No recipients, locked items, channels or structured invite types.
- Connected Hub views receive accepted posts, expiry and eviction updates automatically.
## 6.4 Private comm

A comm is a live two-user exchange established through a generated comm phrase. Comm messages and keys exist only in client memory and live server routing memory while the comm is active.

## 6.5 Where Hub records physically live

Encrypted Hub records are physically stored on the owner's server, in its SQLite database file or configured storage volume. The official client downloads those records and decrypts them locally for display.

- If the server runs on a home computer, NAS or small home server, that machine stores the encrypted records.
- The official client does not persist Hub plaintext locally by default.
- The server owner can copy, back up, lose, retain or delete ciphertext even though the server software does not normally possess the Hub decryption key.
- Expiry and capacity removal describe the active database state, not guaranteed deletion from every backup or copied disk image.
# 7. Trust and privacy model

## 7.1 What the system protects

- Hub and comm content from the server software under normal operation.
- Content from passive network observers through TLS plus application-layer encryption.
- Hub content from people who do not know the access phrase.
- Comm content from people who do not know the comm phrase.
- Previous comm content from later phrase disclosure when fresh ephemeral keys were used and discarded correctly.
## 7.2 What the server can observe

- Client IP addresses, connection times, durations and approximate traffic sizes.
- Whether access authentication succeeds or fails.
- Stable opaque ID tokens during one access-phrase generation.
- The existence, creation time, expiry time and size of Hub ciphertext records.
- Which opaque ID token is in a 24-hour Hub posting cooldown.
- The number of active users and Hub subscribers.
- Opaque comm room identifiers and whether two users enter the same room.
- Comm timing, direction and ciphertext sizes.
## 7.3 What the server cannot normally read

- The access phrase or comm phrases as plaintext.
- Raw ID values.
- Hub message text.
- Comm message text.
- Comm verification codes or directional session keys.
## 7.4 Access-phrase boundary

Every authorised user shares the same access phrase. Anyone who knows it can connect, derive the Hub key, read the current Hub, submit posts and calculate opaque tokens for any available ID.

- The access phrase is not an individual account credential.
- Removing one person requires rotating the shared access phrase for everyone who should remain.
- A copied access verifier or Hub ciphertext may support offline phrase guessing. Scrypt makes each guess expensive, but rate limits cannot stop an offline attack.
- The four-word generated phrase is therefore a deliberate balance between usability and protection, not a claim of high-assurance enterprise authentication.
## 7.5 Hub authorship limitation

The Hub is encrypted with a key shared by all authorised users. The official client inserts the active ID into each post and binds it to the accompanying opaque token, but v1 does not provide a permanent cryptographic identity or signature for human authorship.

- A modified authorised client can claim misleading ID text.
- An ID may be reused in a later connection after it becomes available.
- Hub messages always render with < and are not presented as historically mine.
## 7.6 What the system cannot guarantee

- The server may drop, delay, reorder or refuse traffic.
- The server owner may retain ciphertext after active deletion.
- Another user may copy text, take screenshots or run a modified client.
- /end clears official in-app state but cannot erase copies outside the application.
- A compromised client computer can reveal plaintext and keys while in use.
# 8. Phrase and access system

## 8.1 Bundled word list

v1 uses one bundled and approved phrase source only: grid_words.txt. The file contains exactly 2,048 unique lowercase ASCII words and must be packaged at src/<package>/data/grid_words.txt.

- Custom generation lists are not supported.
- There is no public wordlist check command.
- Development and release tests validate the exact approved count, uniqueness, format and checksum.
- The application fails clearly if the packaged resource is missing or invalid.
## 8.2 Phrase types and lifetime

| Phrase | Words | Created when | Lifetime and audience |
| --- | --- | --- | --- |
| access phrase | 4 | The server owner initialises the server or deliberately rotates access. | Long-lived. Given privately to every friend authorised to connect. |
| comm phrase | 4 | A user starts a new private comm. | One-time and short-lived. Shared only with the intended second user. |

The access and comm phrase formats deliberately match so users have one simple rule: phrases on the Grid contain four words. Their lifetimes and audiences remain different.

## 8.3 Phrase space and trade-off

Four distinct words selected without replacement from 2,048 approved words provide 17,540,692,561,920 ordered possibilities, approximately 44 bits of phrase space.

> **Why v1 keeps four words**
> A three-word phrase would provide approximately 33 bits and about 2,045 times fewer possibilities. The short-lived comm could tolerate that reduction more comfortably than the long-lived access secret, but v1 keeps both formats at four words for consistency and stronger access protection.

## 8.4 Generation

- Use secrets or an equivalent cryptographically secure source.
- Select four different words without replacement.
- Display words separated by single spaces.
- Do not allow a user or server owner to invent a phrase during normal generation.
- Do not write generated phrases to ordinary logs.
## 8.5 How users receive the access phrase

The server owner receives the access phrase once during server initialisation and gives it privately to the friends who should connect. The application does not distribute it automatically because users cannot access the Grid before they know it.

- The owner also gives users the server address when it is not already configured in the client.
- Suitable out-of-band sharing methods include in person, a trusted existing message channel or a phone call.
- The access phrase does not change on a schedule. It remains valid until the owner deliberately rotates it.
- If a friend forgets it, the owner or another authorised user gives it to them again.
- If nobody retains it, the owner rotates access and distributes the new phrase.
## 8.6 Phrase input and normalisation

Received phrases are entered interactively, not as ordinary command-line arguments, so shell history and process listings do not expose them.

1. Trim leading and trailing whitespace.

2. Convert ASCII uppercase to lowercase.

3. Treat hyphens as word separators.

4. Collapse repeated whitespace and separators to one space.

5. Require exactly four words containing lowercase ASCII letters only.

6. Reject empty words and non-ASCII characters.

A received phrase is checked for structure, not membership in the local generation list. This keeps a phrase usable if the bundled generation list changes in a future version.

# 9. Server lifecycle and administration

## 9.1 Initialisation

Initialisation is a local server-owner action, not a normal client flow. It creates the one Grid associated with that server instance.

*conceptual initialisation flow*

```text
$ <app> server init

access phrase:
velvet orbit cabin cedar

save this phrase.
give it only to people you want on the grid.
it cannot be recovered from the server.
```

1. Create a persistent random server ID and access generation value.

2. Generate a four-word access phrase from grid_words.txt.

3. Derive separate access-authentication, Hub-encryption and ID-token keys.

4. Store only the server-side authentication verifier/key material required for challenge-response, not the plaintext phrase or Hub key.

5. Create the SQLite database and initial tables.

6. Display the phrase once and ask the owner to confirm that it has been saved.

## 9.2 Normal server start

*normal server command*

```text
$ <app> server run
```

- Load the persistent server ID, access generation, verifier and database.
- Validate configuration and TLS files before accepting clients.
- Run expiry and cooldown cleanup at startup.
- Never print the access phrase because the server does not retain it as plaintext.
## 9.3 Access rotation

The access phrase changes only when the owner deliberately rotates it, for example because it was exposed or a previous user should no longer connect.

*conceptual rotation flow*

```text
$ <app> server rotate-access

rotate access phrase?
this clears the current hub and disconnects users
using the old phrase. continue? y/n

new access phrase:
amber meadow signal copper
```

1. Generate a new access generation value and four-word phrase.

2. Derive and store the new access-authentication verifier/key material.

3. Delete active Hub records and posting cooldown records from the live database.

4. Disconnect current clients so the old phrase cannot remain active.

5. Keep the persistent server ID and general server configuration.

6. Display the new phrase once for private redistribution.

The Hub is intentionally cleared rather than re-encrypted because it contains at most 24 short-lived messages. Backups or copied ciphertext may still exist outside the active database.

## 9.4 Recovery limitations

- The server cannot reveal a forgotten access phrase.
- There is no account recovery or email reset flow.
- If the phrase is lost, the owner rotates access and gives users the new phrase.
- The server ID, access verifier data, database and TLS files should be backed up as an operational set, but backups must not be described as message-privacy guarantees.
# 10. ID specification

## 10.1 Format and selection

An ID is exactly three characters from the following alphabet:

> **ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789**

- The digits 0 and 1 are excluded because they can resemble O, I or L in some terminal fonts.
- The client suggests a random ID at each connection.
- The user may accept the suggestion or enter another valid value.
- Lowercase input may be normalised to uppercase before validation.
## 10.2 Lifetime

- The ID belongs only to the current active connection.
- It is not permanently registered or owned.
- Its active reservation ends when the user exits, disconnects long enough for the lease to expire, or the server restarts.
- A returning user may choose the same ID if it is not currently reserved.
- An old Hub post can remain visible after the user disconnects because the message and active reservation have separate lifetimes.
## 10.3 Opaque ID token

The raw ID is not sent to the server. The client derives a stable opaque token from the access-derived ID key and uppercase ID text. The server uses that token for active collision handling and the 24-hour posting cooldown.

- The same ID produces the same token while the current access phrase generation remains active.
- Rotating access changes the ID key, clears cooldowns and produces different tokens.
- The server can link actions using the same token but cannot normally recover the three characters.
- All authorised clients can calculate tokens because they share the access phrase; v1 does not make ID ownership cryptographic.
## 10.4 Posting cooldown

An ID may create one accepted Hub post in any rolling 24-hour period. The cooldown is based on the server acceptance time, not whether the message is still visible.

- Natural expiry and posting eligibility occur 86,400 seconds after acceptance.
- If capacity eviction removes the message early, the cooldown continues until the original 24-hour point.
- Reconnecting with the same ID remains blocked until the cooldown ends.
- Changing to another ID produces another token and bypasses the rule. This is an accepted limitation of transient identity.
## 10.5 ID limitations

- An ID is a display label, not an address or proof of human identity.
- It is not sufficient to initiate a private comm in v1.
- The Hub does not render a message as mine based on token matching.
- Direct comm by ID is deferred and would require presence, routing and recipient-key decisions.
# 11. Primary user flows

## 11.1 First launch

1. Launch the client with <app> or ./run.

2. If no server address is configured, prompt for it and offer to remember it locally.

3. Connect using TLS and verify the certificate.

4. Prompt for the four-word access phrase without placing it in shell history.

5. Authenticate with the server and derive local Hub and ID keys.

6. Suggest a three-character ID and reserve its opaque token.

7. Open the Hub as the main view.

*conceptual first connection*

```text
the grid

server: grid.example.net:7331
connecting...
access phrase: ••••• ••••• ••••• •••••
id [J7K]:
connected

the hub
```

## 11.2 Returning launch

The client may remember the server address and certificate preference, but it does not remember the access phrase or ID by default. A returning user therefore normally enters the access phrase, chooses an ID and arrives at the Hub.

## 11.3 Hub view

The Hub is the default application view after connection. It retrieves the current encrypted list, decrypts and validates it locally, then remains subscribed to updates.

*normal Hub view*

```text
the hub

ABC < are you receiving this?
J7K < yes.
M8Q < online later.

/post  /start  /join  /status  /help  /exit
```

If no valid current messages remain, show exactly:

> **you're on your own**

## 11.4 Live Hub updates

- A newly accepted post appears automatically without manual refresh.
- Natural expiry removes the message from the canonical open view.
- If a 25th message is accepted, the oldest message disappears and the new one appears.
- Because the list is at most 24 messages, the normal ANSI client may redraw the Hub list while preserving the input prompt.
- Plain mode avoids full cursor redraw; it reports changes sequentially and rebuilds the canonical list when the Hub is redisplayed.
- While the user is inside a comm, the client records only that the Hub changed and reloads the list when the comm ends.
## 11.5 Post to the Hub

1. Enter /post.

2. Type one plain-text message.

3. Reject empty, oversized or terminal-unsafe input.

4. Encrypt the ID and text locally and submit the ciphertext with the opaque ID token.

5. The server checks the ID cooldown, accepts or refuses the post, sets its 24-hour timestamps and enforces the 24-message capacity inside one transaction.

6. The accepted post is broadcast to connected Hub subscribers and rendered with < on every client, including the author's client.

If the current ID is still in cooldown, the server returns the remaining time. The client may show:

> **you can post again in 6h 12m**

## 11.6 Start a comm

1. Enter /start.

2. Generate and display a four-word comm phrase.

3. Share the phrase with the intended second user through another channel.

4. Wait for one matching user for up to 15 minutes.

5. Begin the encrypted handshake after pairing.

6. Open the comm only after phrase authentication and key derivation succeed.

*start flow*

```text
start comm

comm phrase:
velvet orbit green cabin

waiting...
```

The Hub has no structured comm-invite object. A user may manually type a comm phrase into a Hub post, but it is then public to everyone and is not recommended as a private rendezvous method.

## 11.7 Join a comm

1. Enter /join.

2. Enter the four-word comm phrase interactively.

3. Derive the opaque room identifier and ask the server to pair with the waiting creator.

4. Complete and verify the encrypted handshake.

5. Exchange IDs inside the encrypted channel and open the comm.

An unavailable, expired, occupied or mistyped room uses one generic response:

> **no matching comm is available**

## 11.8 Active comm

*normal private comm*

```text
private comm

ABC > are you receiving this?
J7K < yes.
ABC > this line wraps and keeps its prefix
ABC > on every displayed visual line.

/status  /clear  /end  /help
```

The Hub view is paused while the comm is active. Hub changes are loaded when the user returns.

## 11.9 End a comm

/end is the only intentional user command dedicated to finishing an active comm.

```text
/end
end comm? y/n
```

- n cancels and leaves the comm active.
- y sends an encrypted close event where possible, closes server routing, discards session keys and in-app message objects, clears the comm display where practical, and returns directly to the Hub.
- The detailed deletion limitations live in help, onboarding and documentation rather than the normal confirmation prompt.
- Exiting or losing the application may also close a comm internally, but those are cleanup reasons rather than additional comm-ending commands.
## 11.10 Status

Status remains available because it gives useful operational information without requiring a settings screen.

*Hub status example*

```text
status
server: connected
id: ABC
hub: 17 / 24
post: available in 6h 12m
comm: none
tls: active
```

Inside a comm, status additionally shows the peer ID and optional verification code:

```text
status
server: connected
id: ABC
peer: J7K
comm: encrypted
verification: 7K3M-PQ8R
```

## 11.11 Connection loss and exit

- A server disconnect, heartbeat timeout, protocol failure or process interruption closes an active comm internally.
- Session keys are discarded and messages are not queued or resumed.
- The client may offer to reconnect to the same server, but access authentication and ID reservation begin again.
- The /exit command closes the connection, restores the terminal and displays end of line.
> **end of line**

# 12. Command-line interface

## 12.1 Normal client commands

```text
<app>
<app> status
<app> config show
<app> config set server.host HOST
<app> config set server.port PORT
<app> --server HOST:PORT
<app> --ca-file PATH
<app> --plain
<app> --no-color
<app> --version
<app> --help
```

Launching <app> opens the interactive client and connects to the configured server. There is no portal, Grid chooser or settings menu.

## 12.2 Hub commands

```text
/post
/start
/join
/status
/help
/exit
```

| Command | Action |
| --- | --- |
| /post | Create the current ID's Hub post if its 24-hour cooldown allows. |
| /start | Generate a new comm phrase and wait for one matching user. |
| /join | Prompt for a comm phrase and join a waiting comm. |
| /status | Show connection, ID, Hub count and posting availability. |
| /help | Show commands and concise explanations. |
| /exit | Close the client and display end of line. |

## 12.3 Active comm commands

```text
/status
/clear
/end
/help
```

| Command | Action |
| --- | --- |
| /status | Show connection, peer ID, encryption state and verification code. |
| /clear | Clear the local display without changing the comm or deleting remote copies. |
| /end | Ask end comm? y/n and return to the Hub after confirmation. |
| /help | Show comm commands. |

## 12.4 Server-owner commands

```text
<app> server init
<app> server run
<app> server status
<app> server rotate-access
```

These commands are administrative and normally run on the personal server. They are not part of the friend/user client flow.

## 12.5 Phrase safety

The following forms must not be supported:

```text
<app> --access-phrase "velvet orbit cabin cedar"
<app> join "amber meadow signal copper"
```

Phrases must be requested interactively so application-generated commands do not place them in shell history or process listings.

# 13. The Hub

## 13.1 Message model

The Hub contains only one logical message type: a plain-text public post. The encrypted plaintext object contains the displayed ID and message text.

*conceptual decrypted Hub object*

```text
{
  "v": 1,
  "id": "ABC",
  "text": "are you receiving this?"
}
```

- No recipient field.
- No private or locked message type.
- No comm-invite type.
- No edit, reaction, thread or attachment fields.
## 13.2 Capacity and lifetime

The Hub is a rolling list with two independent removal rules: age and capacity.

| Rule | Behaviour |
| --- | --- |
| Age | A message expires 86,400 seconds after the server accepts it. |
| Capacity | The Hub contains at most 24 current messages. Inserting a 25th removes the oldest current message inside the same transaction. |
| Cooldown | The posting ID remains blocked until 86,400 seconds after acceptance even if its message is removed early. |

Ordering uses server acceptance time, with message ID as a deterministic tie-breaker if required.

## 13.3 Posting transaction

1. Begin an immediate SQLite transaction.

2. Remove naturally expired Hub rows and expired cooldown rows.

3. Reject the post if the opaque ID token has an unexpired cooldown.

4. Validate frame and ciphertext size limits.

5. Insert the new encrypted record with server creation and expiry timestamps.

6. Insert or update the ID cooldown to the same 24-hour end time.

7. If current message count exceeds 24, delete the oldest Hub row.

8. Commit once and broadcast the accepted post plus any removed message IDs.

Performing the checks and insertion in one transaction prevents two simultaneous posts using the same ID token from both succeeding.

## 13.4 Live subscription

- A connected client subscribes automatically after access authentication and ID reservation.
- The server sends the canonical initial list, then accepted-post and removal events.
- Clients validate and decrypt before displaying content.
- A client that misses events reloads the canonical list rather than attempting complex reconciliation.
- The normal Hub view redraws because the list is deliberately capped at 24 messages.
## 13.5 Rendering

- Every Hub visual line uses <.
- The client never changes a Hub post to > because its ID or token matches the current connection.
- Explicit line breaks and wrapped lines repeat the same ID and < prefix.
- Current canonical ordering is oldest to newest.
- Timestamps are server metadata but are not required in the default minimal message line.
## 13.6 Hub encryption and authenticity boundary

All authorised users derive the same Hub master key from the access phrase. This keeps content unreadable to the server but means Hub posts are not individually signed.

- The server database contains ciphertext, opaque token, message ID and timestamps, not plaintext ID or message text.
- Other authorised users can decrypt every current Hub message.
- The official client checks that the decrypted ID recomputes to the authenticated opaque token.
- A modified client with the access phrase can still forge both a claimed ID and its corresponding token if that ID is available.
## 13.7 Removal semantics

A message may disappear because it expired, was pushed out by the 24-message capacity, the owner rotated access, the server operator deleted the database, or storage was lost.

- The UI must not promise that a message will remain visible for a full 24 hours.
- The UI may say messages normally remain for 24 hours and older messages can be pushed out sooner.
- Active deletion does not prove erasure from backups or copied ciphertext.
- Users cannot manually remove or replace a Hub post in v1.
# 14. Private comms

## 14.1 Scope

- Exactly one creator and one joiner.
- One active comm per client.
- No group comms, offline queue or session resumption.
- The user remains connected to the Grid, but the Hub UI is paused until the comm closes.
## 14.2 Waiting and pairing

- Starting a comm creates an in-memory waiting room on the server.
- The default waiting timeout is 15 minutes.
- The room is addressed by an opaque identifier derived from the comm phrase and server context.
- The server pairs the first valid creator and joiner only.
- A third user receives the generic unavailable response.
- No comm content exists before the encrypted handshake completes.
## 14.3 Authentication and encrypted ID exchange

Both clients prove knowledge of the comm phrase while exchanging fresh ephemeral X25519 public keys. The comm becomes active only after both proofs verify and the first encrypted ID exchange succeeds.

- Raw IDs are sent only inside the encrypted comm channel.
- The server routes handshake material but cannot derive the final directional keys.
- A compact verification code is available through /status for optional comparison through another trusted channel.
## 14.4 Message behaviour

- Messages are UTF-8 text and may contain explicit line breaks.
- Every displayed visual line repeats ID and marker.
- Comm messages are encrypted end to end.
- The server forwards ciphertext without writing it to SQLite.
- The official client keeps comm messages in application memory only.
- If either client disconnects, the comm ends and pending messages are not delivered later.
## 14.5 Ending and interruption

- /end is the only normal comm-ending command.
- An encrypted close event is sent where possible, then both official clients discard keys and in-app comm objects.
- Application exit, server loss, heartbeat timeout, integrity failure and process interruption close the comm internally with a reason code.
- A new comm always uses a new generated phrase and fresh ephemeral keys.
# 15. Cryptographic specification

## 15.1 Required primitives

- Scrypt for human phrase hardening.
- HKDF-SHA256 for labelled key separation.
- HMAC-SHA256 for identifiers, challenge proofs and transcript authentication.
- X25519 for ephemeral comm key exchange.
- ChaCha20-Poly1305 for authenticated encryption.
- Python secrets for phrase words, IDs, nonces and random identifiers.
Use the cryptography package for cryptographic primitives. Do not implement algorithms manually.

## 15.2 Server ID and access generation

- The server ID is a persistent random 32-byte value created at initialisation.
- The access generation is a random 16-byte value created at initialisation and replaced on access rotation.
- Clients receive both after the TLS connection and before access authentication.
- The same phrase on another server or access generation derives different material.
## 15.3 Access root derivation

v1 phrase-hardening profile:

```text
Scrypt
N = 32768
r = 8
p = 1
output = 32 bytes
```

Conceptually:

```text
access_root = Scrypt(
    normalised_access_phrase,
    salt = SHA256("access-kdf-v1" || server_id || access_generation)
)
```

Parameters are part of protocol v1 and must not change silently. Benchmark representative supported machines before release and document any profile revision through a protocol version change.

## 15.4 Access key separation

Derive independent values from access_root using fixed labels:

```text
access_auth_key  = HKDF(access_root, "access-auth-v1")
hub_master_key  = HKDF(access_root, "board-master-v1")
id_token_key    = HKDF(access_root, "display-token-v1")
```

- The server stores only access-authentication verifier/key material needed to verify challenges.
- The server does not store access_root, hub_master_key or id_token_key.
- Possession of the stored authentication key must not directly derive the sibling Hub or ID keys.
- The server owner may personally know the phrase because they generated and shared it; the protocol does not attempt to hide it from that human owner.
## 15.5 Access authentication

1. The server sends a fresh random challenge after the initial hello exchange.

2. The client derives access_auth_key from the entered phrase.

3. The client returns HMAC-SHA256 over the protocol label, server ID, access generation, challenge and a client nonce.

4. The server verifies using its stored authentication material.

5. Failure produces a generic access error and contributes to rate limiting.

This avoids transmitting the phrase itself. It is not a password-authenticated key-exchange protocol and does not prevent offline guessing after theft of verifier material; Scrypt and the four-word generated phrase are the practical v1 defence.

## 15.6 Opaque ID token

```text
id_token = HMAC-SHA256(
    id_token_key,
    uppercase_id_ascii
)[:16]
```

The token is stable for that ID during one access generation and changes when access rotates.

## 15.7 Hub encryption

1. Generate a random 16-byte message ID.

2. Derive a unique message key from hub_master_key, message ID and the label board-message-v1.

3. Serialise the plaintext object using deterministic compact UTF-8 JSON.

4. Encrypt with ChaCha20-Poly1305.

5. Send message ID, opaque ID token and ciphertext to the server.

```text
message_key = HKDF-SHA256(
    hub_master_key,
    salt = message_id,
    info = "board-message-v1"
)
```

Each unique message ID produces a separate key, so a fixed all-zero 12-byte nonce may be used for that derived key. The server rejects duplicate message IDs.

Associated authenticated data should include protocol version, server ID, access generation, message ID and opaque ID token. After decryption, the client recomputes the token from the plaintext ID and rejects a mismatch.

## 15.8 Comm phrase derivation and room ID

```text
phrase_root = Scrypt(
    normalised_comm_phrase,
    salt = SHA256("comm-kdf-v1" || server_id)
)

comm_auth_key = HKDF-SHA256(
    phrase_root,
    info = "session-auth-v1"
)

room_id = HMAC-SHA256(
    comm_auth_key,
    "room-id-v1"
)[:16]
```

The same comm phrase therefore maps to different room material on another server. Only already access-authenticated clients may create or join rooms.

## 15.9 Ephemeral comm handshake

- Each side generates one fresh X25519 key pair and one random 16-byte handshake nonce.
- The server assigns a random pair ID after matching creator and joiner.
- The canonical binary transcript includes protocol label/version, server ID, room ID, pair ID, both roles, both nonces and both public keys.
- Each client sends an HMAC phrase proof over its role and transcript hash using comm_auth_key.
- Any changed key, nonce, role or transcript field causes verification failure.
## 15.10 Comm session keys

After proof verification, both sides compute the X25519 shared secret and derive separated session material with HKDF-SHA256 over the shared secret and authenticated transcript.

```text
creator_to_joiner_key
joiner_to_creator_key
session_id
verification_code_seed
```

Separate directional keys are mandatory. The verification code should use eight unambiguous characters displayed as two groups of four.

## 15.11 Comm counters and replay handling

- Each direction has a monotonically increasing 64-bit counter starting at zero.
- The 12-byte nonce is a four-byte direction prefix followed by the eight-byte big-endian counter.
- Associated data includes protocol version, session ID, direction and counter.
- The ordered TCP receiver expects the next exact counter.
- Duplicate, lower, gapped, wrong-direction, wrong-session or invalid-tag frames end the comm.
## 15.12 Cleanup and forward secrecy

- Fresh X25519 private keys are created for every comm.
- Later disclosure of the comm phrase should not by itself reveal captured past comm content after ephemeral keys are discarded correctly.
- The Hub does not have forward secrecy because its asynchronous messages use the shared access-derived Hub key.
- On close or failure, the client discards phrase strings where practical, derived keys, ephemeral private keys, counters, verification material and message buffers.
- Python cannot guarantee forensic RAM erasure; the product promise is no intentional persistence, not guaranteed physical wiping.
# 16. Server transport and protocol

## 16.1 Transport

- Remote connections use TLS by default.
- Clients verify the certificate and hostname using the system trust store or an explicitly configured CA file.
- There is no silent downgrade to plaintext.
- Plain TCP may be enabled only for loopback/local development with an explicit --allow-plain option.
## 16.2 One connection

One asynchronous client connection carries initial hello, access authentication, ID reservation, Hub list/subscription traffic, comm waiting/pairing, encrypted comm frames, status information and heartbeats.

## 16.3 Outer frame format

Use newline-delimited UTF-8 JSON for the outer server protocol. Binary values use URL-safe Base64. The server parses only routing and limit fields, not encrypted bodies.

*conceptual outer frame*

```text
{
  "v": 1,
  "type": "session_data",
  "request_id": "kR8y1Q",
  "session_id": "base64url-data",
  "counter": 14,
  "body": "base64url-ciphertext"
}
```

## 16.4 Protocol neutrality

Machine-facing names remain neutral. Public vocabulary such as Hub, ID and /end must not determine protocol field names.

```text
access_challenge
access_proof
display_reserve
board_list
board_post
board_update
board_remove
session_wait
session_join
session_handshake
session_data
session_close
```

## 16.5 Initial exchange

| Client sends | Server returns |
| --- | --- |
| Protocol version, client version and capabilities. | Protocol version, server ID, access generation, server time, active limits, heartbeat interval and access challenge. |

Unsupported versions fail before access phrase processing. After a valid access proof, the client reserves an ID token and receives the canonical Hub list.

## 16.6 Core actions

| Client action | Purpose |
| --- | --- |
| hello | Negotiate protocol and receive server context. |
| access_proof | Authenticate knowledge of the access phrase. |
| display_reserve | Reserve the current opaque ID token. |
| board_list | Request the canonical current Hub list. |
| board_post | Submit encrypted Hub ciphertext and opaque ID token. |
| board_subscribe | Receive accepted and removed message events. |
| session_wait | Create a temporary waiting comm room. |
| session_join | Join a matching waiting room. |
| session_handshake | Forward public keys, nonces and proofs. |
| session_data | Forward encrypted comm frames. |
| session_close | Close the active comm route. |
| ping / pong | Maintain connection health. |

## 16.7 Hub update events

- board_update contains one newly accepted encrypted record.
- board_remove identifies one or more records removed by expiry, capacity or administrator reset.
- Access rotation closes existing client connections rather than attempting an in-session key transition.
- A client detecting a missed sequence requests a full board_list refresh.
## 16.8 Heartbeats, timeouts and limits

- Default heartbeat interval: 30 seconds.
- Connection considered dead after 90 seconds without valid activity.
- A dead connection releases its active ID reservation, waiting room and live comm routing state.
- Maximum complete outer frame: 16 KiB including JSON and Base64 expansion.
- Malformed or oversized frames cause a protocol error and connection closure.
- Unknown optional fields may be ignored where safe; unknown required frame types return an unsupported-frame error.
# 17. Server state and storage

## 17.1 In-memory state

- Active client connections and access-authenticated state.
- Active opaque ID reservation leases.
- Hub subscriber queues.
- Waiting comm rooms and active comm pairings.
- Bounded outbound queues.
- Rate-limit counters and temporary challenges.
## 17.2 Persistent state

- Persistent server ID.
- Current access generation and server-side authentication verifier/key material.
- Encrypted Hub records and server timestamps.
- Opaque ID posting cooldown records.
- Server configuration and TLS file references.
The server does not persist plaintext phrases, Hub plaintext, raw IDs, comm messages, comm keys, live rooms, accounts or profiles.

## 17.3 Conceptual SQLite schema

```text
CREATE TABLE board_messages (
    message_id   BLOB PRIMARY KEY,
    id_token     BLOB NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    ciphertext   BLOB NOT NULL
);

CREATE INDEX board_created
ON board_messages(created_at, message_id);

CREATE INDEX board_expiry
ON board_messages(expires_at);

CREATE TABLE board_cooldowns (
    id_token      BLOB PRIMARY KEY,
    last_post_at  INTEGER NOT NULL,
    next_post_at  INTEGER NOT NULL
);

CREATE INDEX cooldown_expiry
ON board_cooldowns(next_post_at);
```

## 17.4 Cleanup and capacity enforcement

- Natural expiry cleanup runs at startup, before every post, during list reads and on a periodic task.
- Cooldown rows are removed only after next_post_at, independently of message visibility.
- Capacity eviction happens in the same transaction as insertion.
- At most 24 current message rows remain after commit.
- A removed message ID is broadcast so connected clients update the canonical list.
## 17.5 Bounded queues

Every connection has a bounded outbound queue. A slow client is disconnected rather than allowing unbounded server memory growth. Live comm frames are never spilled to SQLite.

## 17.6 Restart and access rotation

| Event | Preserved | Cleared |
| --- | --- | --- |
| Normal server restart | Server ID, access generation, verifier, unexpired Hub ciphertext and unexpired cooldowns. | Active clients, ID reservations, waiting rooms and active comms. |
| Access rotation | Server ID and general configuration. | Current Hub records, cooldowns, active clients, ID reservations, waiting rooms and active comms. |

# 18. Default limits

| Item | v1 value |
| --- | --- |
| Access phrase words | 4 distinct words |
| Comm phrase words | 4 distinct words |
| Bundled generation list | 2,048 approved words |
| ID length | 3 characters |
| ID alphabet | A-Z and 2-9 |
| Current Hub messages | 24 maximum |
| Hub posting cooldown | 86,400 seconds per opaque ID token |
| Hub nominal message lifetime | 86,400 seconds from server acceptance |
| Hub plaintext size | 1,024 UTF-8 bytes |
| Comm plaintext size | 4,096 UTF-8 bytes |
| Active comms per client | 1 |
| Users per comm | 2 |
| Comm waiting timeout | 15 minutes |
| Comm handshake timeout | 30 seconds |
| Outer frame size | 16 KiB |
| Heartbeat interval | 30 seconds |
| Dead connection timeout | 90 seconds |
| ID reservation lease timeout | 90 seconds |
| Failed access attempts | 5 per 10 minutes per IP, then increasing delay |
| Comm join attempts | 20 per 10 minutes per IP |
| Concurrent users | 64 by default, server-configurable |

The server supplies active operational limits during the initial exchange. The four-word phrase format, 24-message Hub capacity, one-post-per-ID cooldown and 24-hour lifetime are v1 product rules rather than ordinary user settings.

# 19. Terminal interface and text safety

## 19.1 Default rendering

- Line-oriented ID + marker + text at every terminal width.
- Hub uses < for every visual line.
- Comm uses viewer-relative > and <.
- Every explicit or wrapped display line repeats the full prefix.
- No speech bubbles or wide-screen alignment mode.
## 19.2 ASCII identity

- Optional ASCII logo or wordmark at launch.
- Small ASCII separators for the Hub, private comm and status.
- Minimal ANSI colour for headings, markers and status only.
- No mouse support, panes or heavy TUI framework.
## 19.3 Wrapping algorithm

The renderer subtracts the width of ID, spaces and marker before wrapping the message body. Every resulting visual segment receives the same prefix.

```text
M8Q < this message is wrapped to the available
M8Q < body width and every line remains identified.
```

- Explicit newlines create separate logical source lines, each prefixed.
- Long unbroken tokens may be hard-wrapped after the prefix width is reserved.
- Terminal resizing affects subsequent redraws; the current Hub may be redrawn because it contains at most 24 messages.
## 19.4 Encoding and control characters

- Messages use UTF-8; phrases and IDs remain ASCII.
- Reject NUL.
- Remove or visibly escape ESC and terminal control sequences.
- Normalise CRLF and CR to LF.
- Convert tabs to a fixed number of spaces.
- Preserve ordinary printable Unicode and user-authored letter case.
- Measure text limits using UTF-8 encoded byte length.
## 19.5 Plain and no-colour modes

- Support NO_COLOR, --no-color and --plain.
- No-colour mode keeps cursor behaviour but removes ANSI colours.
- Plain mode avoids cursor positioning, live in-place redraw and optional ASCII ornament.
- The ID + marker message format remains unchanged and suitable for accessibility tools and logs.
## 19.6 Input handling

The POSIX terminal backend must preserve the current input buffer when a Hub update or comm message arrives. It may use asyncio, termios and tty behind a small platform-neutral interface.

- Enter, Backspace, Ctrl-C, Ctrl-D and pasted text are required.
- Terminal settings must be restored through finally blocks and signal handling.
- Full cursor-aware editing, selection, command history and multiline navigation are not required.
- Windows support remains deferred, but terminal I/O must not leak POSIX assumptions into protocol or client state logic.
# 20. User-facing copy and errors

System-written examples below follow the lowercase UI rule. ID values and user-authored message text are exceptions.

| Condition | User-facing response |
| --- | --- |
| Server unreachable | the server could not be reached |
| TLS verification failure | the server identity could not be verified |
| Unsupported protocol | this client and server use incompatible protocol versions |
| Access phrase malformed | enter a four-word access phrase |
| Access authentication failed | access could not be verified |
| ID invalid | an id must contain three characters from A-Z and 2-9 |
| ID active | that id is already active |
| Hub empty | you're on your own |
| Hub message too long | the hub message exceeds the 1,024-byte limit |
| Hub cooldown | you can post again in <remaining time> |
| Hub post accepted at capacity | no warning is shown; the live list updates and the oldest post is removed |
| Hub ciphertext invalid | one hub message could not be verified |
| Comm phrase malformed | enter a four-word comm phrase |
| Comm unavailable | no matching comm is available |
| Comm handshake failed | a private comm could not be established |
| Comm integrity failed | the comm failed an integrity check |
| Comm message too long | the message exceeds the 4,096-byte limit |
| Peer closes | <ID> ended the comm |
| Server disconnects | connection to the server was lost. the comm has ended |
| Slow connection | the connection could not keep up and was closed |

Detailed cryptographic failure reasons may appear only in explicit debug output and must not include phrases, keys, plaintext, ciphertext bodies or full opaque identifiers.

# 21. Logging

## 21.1 Client logging

The client writes no persistent log by default. Explicit debug mode may record versions, state transitions, frame types, sizes, timings and error categories.

- Do not log access phrases, comm phrases or derived keys.
- Do not log Hub or comm plaintext.
- Do not log ciphertext bodies or full opaque identifiers.
- Raw IDs should be omitted unless the user explicitly requests diagnostic output.
## 21.2 Server logging

- May log service start/stop, connection counts, aggregate traffic, rate-limit events, protocol errors, cleanup counts, TLS errors and software version.
- Must not log phrases, raw IDs, message bodies, keys or decrypted content.
- Opaque identifiers should be truncated or replaced with temporary log-local hashes when diagnostic correlation is needed.
- Access failures should be counted without logging candidate phrase material.
# 22. Configuration

## 22.1 Client configuration

There is no settings screen. The client may remember a small set of technical preferences through a JSON file or explicit CLI commands.

| May remember | Must not remember by default |
| --- | --- |
| Server host and port. | Access phrase or comm phrases. |
| Custom CA file path. | ID. |
| Colour and plain-mode preference. | Hub or comm plaintext. |
| Optional connection timeout preferences. | Encryption keys or recent comm history. |

*conceptual client configuration*

```text
{
  "server": {
    "host": "grid.example.net",
    "port": 7331,
    "ca_file": null
  },
  "ui": {
    "color": true,
    "plain": false
  }
}
```

## 22.2 Platform locations

| Platform | Default path |
| --- | --- |
| Linux | $XDG_CONFIG_HOME/<app>/config.json or ~/.config/<app>/config.json |
| macOS | ~/Library/Application Support/<app>/config.json |

## 22.3 Server configuration

*network and TLS*

```text
{
  "listen": {
    "host": "0.0.0.0",
    "port": 7331
  },
  "tls": {
    "certificate": "/path/to/cert.pem",
    "private_key": "/path/to/key.pem"
  }
}
```

*storage and operational limits*

```text
{
  "storage": {
    "database": "/path/to/grid.sqlite3",
    "server_id": "/path/to/server-id.bin",
    "access_state": "/path/to/access-state.bin"
  },
  "limits": {
    "max_connections": 64,
    "max_frame_bytes": 16384
  }
}
```

The one-Grid model, four-word phrase format, 24-message Hub capacity, 24-hour cooldown and 24-hour nominal lifetime are v1 product/protocol behaviour rather than ordinary configuration choices.

# 23. Installation and deployment

## 23.1 Client first run

```text
git clone <repository>
cd <repository>
./run
```

The launcher locates Python 3.11 or later, creates .venv if required, installs the local package and cryptography, then launches the client. It must not require global package installation.

## 23.2 Personal server deployment

1. Clone the repository on the home or personal server.

2. Create the virtual environment through the supplied server launcher or documented command.

3. Configure listen address, TLS certificate, private key and storage paths.

4. Run <app> server init once and save the generated access phrase.

5. Start <app> server run as a background service.

6. Give friends the server address, CA instructions if needed, and access phrase through a trusted channel.

Provide example systemd and launchd service definitions. Docker may be considered later but is not required for v1.

## 23.3 Packaging

- Use pyproject.toml and a src/ layout.
- Include grid_words.txt as a package resource.
- Validate the exact bundled file in automated tests.
- Use the standard library for CLI, networking, TLS, JSON, SQLite, terminal handling and configuration where practical.
- cryptography is the only required runtime dependency outside the standard library.
## 23.4 Windows boundary

v1 ships only the POSIX terminal backend and shell launcher. Terminal input/output remains behind a small interface so a Windows Terminal/PowerShell backend can be added later without changing client state, protocol or cryptography.

# 24. Suggested repository structure

*application package*

```text
<repository>/
├── pyproject.toml
├── README.md
├── LICENSE
├── run
└── src/
    └── <package>/
        ├── __init__.py
        ├── __main__.py
        ├── cli.py
        ├── client.py
        ├── relay.py
        ├── protocol.py
        ├── crypto.py
        ├── access.py
        ├── phrases.py
        ├── hub.py
        ├── sessions.py
        ├── terminal.py
        ├── config.py
        ├── models.py
        ├── terms.py
        ├── ui_text.py
        └── data/
            └── grid_words.txt
```

*test suite*

```text
tests/
├── test_wordlist.py
├── test_phrases.py
├── test_access.py
├── test_hub_crypto.py
├── test_comm_crypto.py
├── test_protocol.py
├── test_server_hub.py
├── test_server_sessions.py
├── test_terminal.py
└── test_integration.py
```

| Module | Responsibility |
| --- | --- |
| cli.py | Client and server-owner command routing. |
| client.py | Connection lifecycle and top-level client state. |
| relay.py | Server connections, Hub subscription, pairing, routing and limits. |
| protocol.py | Frame encoding, validation, versioning and limits. |
| crypto.py | KDFs, access proofs, Hub encryption, comm handshake and message encryption. |
| access.py | Server initialisation, access state and rotation. |
| phrases.py | Approved word-list loading, generation and normalisation. |
| hub.py | Hub models, local rendering data and client post workflow. |
| sessions.py | Comm state machine, roles, counters and control events. |
| terminal.py | Rendering, wrapping, sanitisation, input preservation and terminal restoration. |
| config.py | JSON configuration and platform paths. |
| models.py | Neutral typed data models. |
| terms.py / ui_text.py | Public vocabulary and complete system-written copy. |

The cryptographic module must not import UI code. The server relay module must not depend on public terminology. The terminal module exposes a platform-neutral boundary even though v1 supplies only POSIX behaviour.

# 25. Application state machines

## 25.1 Top-level client states

```text
STARTING
CONNECTING
AUTHENTICATING
SELECTING_ID
HUB_LOADING
HUB_ACTIVE
COMM_WAITING
COMM_HANDSHAKE
COMM_ACTIVE
DISCONNECTED
EXITING
```

Users do not see separate Portal or Grid-selection states. Public terminology does not determine internal state names.

## 25.2 Main transition

```text
STARTING
  -> CONNECTING
  -> AUTHENTICATING
  -> SELECTING_ID
  -> HUB_LOADING
  -> HUB_ACTIVE
```

## 25.3 Comm states

```text
NONE
WAITING
PAIRING
AUTHENTICATING
ACTIVE
CLOSING
CLOSED
FAILED
```

CLOSING carries an internal reason such as user_end, application_exit, peer_close, server_disconnect, timeout or integrity_failure. Only user_end is triggered by the dedicated /end command.

## 25.4 Hub states

```text
LOADING
ACTIVE
POSTING
POST_BLOCKED
REDRAWING
ERROR
```

- POST_BLOCKED includes the server-provided remaining cooldown.
- REDRAWING is a presentation state used for live accepted/removal updates; it is not a separate user-facing place.
- While COMM_ACTIVE, the client records that Hub data changed and reloads on return.
## 25.5 Server states

```text
UNINITIALISED
READY
RUNNING
ROTATING_ACCESS
STOPPING
```

# 26. Testing requirements

## 26.1 Bundled word-list and phrase tests

- grid_words.txt loads as UTF-8 from the package resource.
- It contains exactly 2,048 usable unique lowercase ASCII words and matches the approved checksum.
- Generation produces four distinct words using an injectable deterministic test source.
- Access and comm phrase examples use four words throughout.
- Space, hyphen and uppercase normalisation works.
- Non-ASCII, empty and wrong-count phrases are rejected.
- Received phrases are not required to appear in the local list.
- No custom-list path or public validation command exists.
- Phrases never appear in application-generated shell command arguments or ordinary logs.
## 26.2 Server initialisation and access tests

- Initialisation creates server ID, access generation, verifier state and database once.
- The generated access phrase is displayed but not persisted as plaintext.
- Correct access proof succeeds; wrong phrase, changed challenge or replayed proof fails.
- The server cannot derive the Hub key from stored authentication material in the implementation interface.
- Access failures trigger configured rate limits without logging candidate phrases.
- Normal restart preserves access state and Hub data.
- Access rotation generates a new phrase, changes access generation, clears Hub/cooldowns, disconnects clients and rejects the old phrase.
## 26.3 ID tests

- Valid three-character IDs are accepted.
- 0, 1, punctuation, spaces and wrong lengths are rejected; lowercase may normalise to uppercase.
- Active opaque-token collisions are rejected without sending the raw ID.
- Reservations expire after connection loss.
- The same ID maps to the same token within one access generation and a different token after rotation.
- The relay/server database and logs contain no raw ID.
## 26.4 Hub cryptography and behaviour tests

- Authorised clients decrypt the same valid Hub record.
- A wrong access phrase or old access generation cannot decrypt it.
- Ciphertext, message ID, token or authenticated metadata modification fails verification.
- Decrypted ID recomputes to the outer opaque token.
- The database contains ciphertext and metadata, not Hub plaintext or raw ID.
- One post using a token succeeds; another before next_post_at fails.
- The same ID remains blocked after reconnect and after early message eviction.
- A different ID token may post, documenting the accepted per-ID limitation.
- Natural expiry occurs at 86,400 seconds from server acceptance.
- The 25th accepted current message removes exactly the oldest current row.
- At most 24 current rows remain after each committed post.
- Two simultaneous same-token posts cannot both succeed.
- Connected clients receive accepted and removal updates without manual refresh.
- Every Hub visual line uses <, including the current ID and wrapped lines.
- The empty validated result renders exactly you're on your own.
## 26.5 Comm cryptography tests

- Matching comm phrases establish a session between two access-authenticated clients.
- Wrong phrase, changed public key, changed nonce, role swap or transcript replay fails.
- Fresh sessions derive fresh directional keys and the same verification code at both ends.
- Modified ciphertext, duplicate/lower/gapped counters, wrong direction and wrong session ID end the comm.
- A third user cannot join an active room.
- Comm plaintext is never written to SQLite or ordinary logs.
## 26.6 /end tests

- The active command list contains /end and not /leave or /derez.
- The prompt is the concise lowercase form end comm? y/n.
- n cancels without changing comm state.
- y sends an encrypted close event where possible and closes routing state.
- Both official clients discard session keys and in-app comm objects.
- The server receives only a generic session-close request.
- Help and documentation explain deletion limitations without repeating a long warning on every /end.
## 26.7 Terminal and rendering tests

- Incoming Hub updates and comm messages preserve the current input buffer.
- Every explicit and wrapped line repeats the ID and marker.
- Hub uses < for all lines; comm uses viewer-relative markers.
- Ctrl-C, Ctrl-D and common failure paths restore the terminal.
- ANSI/control sequences in user text are escaped.
- System-authored UI strings are lowercase except approved data/technical exceptions.
- NO_COLOR, --no-color and --plain work.
- The interface remains usable at approximately 40 columns.
## 26.8 Server and protocol tests

- One server instance exposes one Grid and one Hub only.
- No Grid creation, registry or selection protocol remains.
- Waiting rooms and ID leases expire.
- Slow clients cannot create unbounded queues.
- Oversized and malformed frames close the connection.
- Heartbeat timeouts release transient state.
- Restart preserves persistent access/Hub state and ends live comms.
- Access rotation clears the intended persistent and live state.
- Rate limits work without claiming persistent human identity.
# 27. Acceptance criteria

## 27.1 Product and terminology

- The ordinary user model contains the Grid, the Hub, ID, comm, access phrase, comm phrase, server, status and /end.
- No Portal, Grid chooser, multi-Grid creation or leave-the-Grid control appears.
- Quoted system UI uses lowercase; ID values remain uppercase.
- Public terminology can change without changing protocol fields or database columns.
- ASCII and line-oriented styling provide visual character without a heavy TUI framework.
## 27.2 Connection and access

- The owner can initialise one server and receive a generated four-word access phrase.
- A user with the server address and correct phrase can connect and choose an ID.
- A wrong phrase cannot enter the Grid.
- The access phrase is not stored as plaintext by the server.
- The owner can rotate access, which clears the active Hub and invalidates old sessions.
- No account or permanent identity is required.
## 27.3 Hub

- Every authorised user sees the same canonical public list.
- The list updates automatically while open.
- At most 24 current messages exist.
- A 25th accepted message removes the oldest current message.
- An ID may post once every 24 hours, even when its message was evicted early.
- The server stores ciphertext, not Hub plaintext or raw ID.
- Messages are not editable, replaceable or manually deletable.
- Every Hub line uses < and repeats the ID/marker after wrapping.
- The empty Hub displays you're on your own.
- Documentation states where the database physically lives and what removal cannot prove.
## 27.4 Comms

- A user can start a comm and receive a generated four-word phrase.
- A second authorised user can join using that phrase.
- The comm is limited to two users and end-to-end encrypted.
- Comm content is not intentionally persisted.
- /end is the only dedicated intentional comm-ending command.
- The comm returns directly to the Hub after ending.
- Interrupted comms do not resume or deliver queued messages.
## 27.5 Status, terminal and packaging

- /status shows useful connection, ID, Hub capacity/cooldown and comm verification information.
- Input remains intact when messages or Hub updates arrive.
- User content cannot inject terminal control sequences.
- Colour can be disabled and plain mode remains usable.
- Normal exit and common interruption paths restore the terminal.
- Application exit displays end of line.
- A fresh macOS or Linux checkout runs through ./run.
- The approved word list is packaged correctly and no custom-list feature is exposed.
# 28. Implementation order

## 28.1 Phase 1 - foundation

- Repository, pyproject.toml, ./run and package structure.
- Configuration loading and platform paths.
- Exact bundled word-list validation.
- Four-word generation and normalisation.
- Central terminology and UI text modules.
- Neutral typed data models.
> **Phase 1 completion gate**
> A fresh checkout installs and generates valid four-word phrases from the exact approved bundled file.

## 28.2 Phase 2 - access and cryptography

- Server initialisation and access state.
- Access challenge-response.
- Hub key separation and message encryption.
- Opaque ID tokens.
- Comm phrase derivation, X25519 handshake, phrase proofs, directional keys and counters.
- Fixed test vectors and tamper/replay tests.
> **Phase 2 completion gate**
> Access, Hub and comm cryptography pass mismatch, tamper, replay and role tests without networking.

## 28.3 Phase 3 - headless server and clients

- TLS transport and outer frames.
- One-Grid access authentication.
- ID reservations.
- Hub SQLite transaction, 24-message capacity and cooldowns.
- Live subscriptions.
- Comm waiting, pairing, forwarding, heartbeat and queue limits.
> **Phase 3 completion gate**
> Headless clients authenticate, receive live encrypted Hub updates, enforce rolling capacity/cooldowns and exchange encrypted comm messages through the server.

## 28.4 Phase 4 - terminal client

- First-launch server prompt and access flow.
- ID selection and Hub main view.
- Line wrapping with repeated prefixes.
- Live redraw and preserved input.
- /post, /start, /join, /status, /end, /help and /exit.
- ASCII identity, no-colour/plain modes and terminal restoration.
> **Phase 4 completion gate**
> The complete normal flow works in two ordinary macOS or Linux terminals and remains understandable in plain mode.

## 28.5 Phase 5 - server administration and deployment

- server init, run, status and rotate-access commands.
- TLS and custom CA documentation.
- systemd and launchd examples.
- Backup and recovery documentation.
- Metadata-only logging and hardening.
- CI on macOS and Linux.
> **Phase 5 completion gate**
> A technically capable owner can initialise, host, rotate and maintain the personal server from the documentation without source changes.

# 29. Deferred possibilities

- Windows Terminal and PowerShell client support.
- Direct comm requests to currently active IDs.
- Permanent cryptographic user identities or permanent ID ownership.
- Encrypted presence and contact discovery.
- Remembering the access phrase through operating-system credential storage.
- Hub message signatures.
- Manual Hub post replacement or deletion tokens.
- Multiple server-hosted Grids or multi-tenant operation.
- Private Hub items, offline mailboxes or multiple Hub channels.
- Group comms.
- Optional encrypted local history.
- Relay federation or direct peer-to-peer transport.
- Custom phrase lists.
- Mobile clients.
- Richer terminal editing and navigation.
None of these should complicate the v1 user model or protocol unless a small compatibility hook is clearly justified.

# 30. Final v1 definition

```text
the owner initialises one personal server and receives a
four-word access phrase.

friends launch the client, connect to that server, enter the
access phrase, choose a transient three-character ID and view
the same live Hub.

the Hub contains at most 24 encrypted public messages. an ID
may post once every 24 hours. messages normally last 24 hours
but older messages can be pushed out sooner.

a user can start or join a private two-user comm using a new
four-word comm phrase. comm messages are end-to-end encrypted
and are not intentionally persisted.

/end finishes the comm and returns to the Hub.
/exit closes the application and displays end of line.
```

This specification is the implementation baseline for v1. Changes to the one-server/one-Grid model, access phrase, phrase length, Hub capacity, posting cooldown, identity model, persistence or protocol security boundary require an explicit specification revision rather than an informal code-only change.
