# Phase 5 — server administration, deployment, and macOS distribution

**Status:** implementation guide for the Phase 5 release candidate

This document is the operational guide for the one personal Grid deployment.
The product-facing application is **okno**; **the grid** is the single environment
hosted on the owner's personal server.

The original approved v1 specification is retained unchanged. Later explicit
project decisions override historical first-run examples where they differ:

- the ordinary v1 client is distributed for macOS only;
- ordinary users connect to one deployment-provisioned Grid and do not choose a
  server inside okno;
- the installed launch command is simply `okno`;
- normal distribution is a self-contained macOS executable, not a requirement
  to install Python, Homebrew, pip, or a virtual environment;
- public source/release hosting uses GitHub, while the owner's personal identity
  and local machine names should not appear in committed configuration or
  release metadata;
- Termux/Android is a possible later distribution target; it is not a v1
  supported platform.

## 1. Server state

By default on macOS, owner server state lives under:

```text
~/Library/Application Support/okno/server/
```

The directory contains:

```text
server.json
server-id.bin
access-state.json
grid.sqlite3
tls/grid-ca.pem
tls/grid-ca-key.pem
tls/server-cert.pem
tls/server-key.pem
server.log
```

Runtime-only files such as `admin.sock` and `server.pid` may appear while the
service is running.

The server directory and secret files are owner-only. The access phrase itself
is never stored in plaintext. The private CA and server private keys are also
owner-only.

## 2. Choose the public endpoint before initialisation

The permanent Grid needs one stable public hostname and TCP port. Use a neutral
hostname that is not personally identifying. The public hostname is part of the
server certificate and is later placed into the client release profile.

The normal v1 port is:

```text
7331/tcp
```

The router should forward that public TCP port to the Mac Mini's reserved LAN
address. The server process listens on `0.0.0.0:7331` by default.

Do not commit a personal/home hostname to this repository merely for testing.
Production client endpoint material is exported separately and attached to a
GitHub Release.

## 3. Initialise the permanent Grid

From the repository on the Mac Mini during bootstrap:

```sh
./run server init --public-host GRID_HOSTNAME
```

For a non-default public port:

```sh
./run server init --public-host GRID_HOSTNAME --public-port PORT
```

The state directory must either not exist or be empty, and it must not be a symlink.
`server init` checks this before generating or displaying the access phrase so an
accidental `--state-dir` can never cause unrelated files to be removed.

Initialisation:

1. generates the persistent random server ID;
2. generates the first access generation and four-word access phrase;
3. asks the owner to confirm that the phrase has been saved;
4. stores only the access verifier state, never the plaintext phrase;
5. creates the encrypted SQLite Hub database;
6. creates a private Grid CA and a server certificate bound to the configured
   public hostname;
7. writes strict server configuration and owner-only state.

The displayed access phrase must be saved privately. It cannot later be
recovered from server storage.

## 4. Validate and run in the foreground

Show validated state without starting the service:

```sh
./run server status
```

Run the server in the foreground for the first production-network test:

```sh
./run server run
```

`server run` validates the server ID, access state, database generation, TLS
certificate/key match, certificate hostname, validity dates, private-key
permissions, and configured limits before accepting clients.

Remote plaintext transport is never enabled by this command.

## 5. Metadata-only server logging

The server writes a small rotating operational log to:

```text
~/Library/Application Support/okno/server/server.log
```

The log is limited to metadata such as service start/stop and access rotation
counts. It does not intentionally log:

- access or comm phrases;
- display IDs;
- Hub plaintext;
- comm plaintext;
- encryption keys;
- message IDs or ciphertext payloads.

The log file is owner-only and rotates at approximately 1 MiB with three old
copies retained.

## 6. launchd — automatic boot/restart

Once the `okno` executable is installed on the Mac Mini, install the supplied
system LaunchDaemon from the repository:

```sh
./deploy/macos/install-server-service.sh
```

The installer creates:

```text
/Library/LaunchDaemons/com.okno.grid.plist
```

The daemon runs `okno server run` as the current server-owner account, not as
root. `RunAtLoad` and `KeepAlive` allow it to start at boot without a graphical
login and restart after an unexpected process exit.

Inspect launchd state with:

```sh
sudo launchctl print system/com.okno.grid
```

Remove only the service definition, without deleting Grid data, with:

```sh
./deploy/macos/remove-server-service.sh
```

## 7. Server status

When the service is running:

```sh
okno server status
```

reports only operational metadata, for example:

```text
server: running
public: grid.example.net:7331
listen: 0.0.0.0:7331
tls: valid (824 days remaining)
connections: 3
hub messages: 5
cooldowns: 4
```

When stopped, the same command validates on-disk state and reports `server:
stopped`. If the PID lock proves the server is running but its local admin socket
is unavailable, status fails explicitly rather than falsely reporting a stopped
server.

## 8. Rotate access

Run locally on the server:

```sh
okno server rotate-access
```

The command requires explicit confirmation. If the server is running, the
owner-only local admin socket coordinates the rotation with the live process.
The operation:

1. creates a fresh four-word phrase and access generation;
2. persists the new verifier state;
3. disconnects all old-generation clients;
4. clears Hub messages, posting cooldowns, spent message IDs, active ID leases,
   waiting comms, and live comm routes;
5. keeps the persistent server ID and TLS identity;
6. displays the new phrase once.

If the server is genuinely stopped, the same command safely rotates the on-disk
state. A held server PID lock prevents fallback to offline mutation when a live
server merely has an unavailable admin socket. The database is generation-bound,
so a crash between persistence steps cannot cause old-generation Hub/cooldown
state to become active again after restart. An older/unbound database with
ambiguous existing Hub state is cleared when it is first bound to a generation
rather than guessing ownership.

## 9. TLS model and renewal

The default v1 deployment uses a private Grid certificate authority. This
avoids requiring friends to install or understand certificate tooling while
still providing hostname-verifying TLS.

The public CA certificate is bundled into the okno client release. The CA
private key never leaves the server/backup set.

Renew the server certificate with:

```sh
okno server renew-tls
```

The private CA is retained and a fresh server key/certificate is issued for the
same configured public hostname. Existing clients do not need a new CA after a
normal renewal.

Restart the service so the running TLS listener loads the renewed certificate:

```sh
sudo launchctl kickstart -k system/com.okno.grid
```

The current generated server certificate lifetime is 825 days. The CA lifetime
is ten years. `okno server status` reports remaining server-certificate days.

## 10. Operational backup

Create an owner-only backup archive with:

```sh
okno server backup --output ~/okno-server-backup.tar.gz
```

The database is copied through SQLite's backup API rather than by copying an
open database file. The archive contains the operational identity, access
verifier, database, server config, CA/private CA key, and server certificate/key.
It intentionally excludes runtime sockets, PID files, and logs.

The archive contains secrets and must be protected like the server itself. A
backup is an operational recovery aid, not a message-privacy guarantee.

### Recovery

For recovery on the same deployment:

1. stop the launchd service;
2. preserve the damaged state directory separately until recovery is verified;
3. extract the backup into a private temporary directory;
4. restore `server.json`, `server-id.bin`, `access-state.json`, `grid.sqlite3`
   and the `tls/` directory to the configured server state directory;
5. restore directory mode `0700`, private files `0600`, public certificates
   `0644`;
6. run `okno server status` while stopped;
7. restart with `sudo launchctl kickstart -k system/com.okno.grid`;
8. test a real client connection.

If the backup's Hub database is absent or unusable, the server identity/access
and TLS material remain the important operational state; losing short-lived Hub
messages is preferable to trying to reconstruct plaintext.

## 11. Export the public client profile

On the server:

```sh
okno server export-client --output ~/okno-client-profile
```

This exports only public distribution material:

```text
okno-grid-host.txt
okno-grid-port.txt
okno-grid-ca.pem
okno-grid-profile.json
```

No private key, verifier, access phrase, ID, Hub content, or comm content is
exported.

## 12. GitHub release configuration

The repository's macOS release workflow expects three GitHub repository
secrets:

```text
OKNO_GRID_HOST
OKNO_GRID_PORT
OKNO_GRID_CA_B64
```

`OKNO_GRID_PORT` may be omitted/empty to use 7331.

`OKNO_GRID_CA_B64` is the Base64 encoding of the public `grid-ca.pem`, not a
private key. On the Mac Mini it can be prepared for pasting into GitHub with a
local command such as:

```sh
base64 < "$HOME/Library/Application Support/okno/server/tls/grid-ca.pem" | tr -d '\n' | pbcopy
```

Do not paste private keys or the access phrase into GitHub or ChatGPT.

A tag matching `v*` triggers the release workflow. It builds separate native
macOS archives for Apple Silicon and Intel, creates the public Grid profile
assets, computes SHA-256 checksums, and attaches them to the GitHub Release.

The release builder uses PyInstaller as a build-only dependency. `cryptography`
remains the only third-party runtime dependency in the Python project itself.
The produced friend-facing executable contains its Python runtime, so users do
not install Python, pip, a virtual environment, or Homebrew.

## 13. One-line friend installation

Once the repository is public and a release exists, the approved ordinary macOS
installation is:

```sh
curl -fsSL https://raw.githubusercontent.com/oknoart/the-grid/main/install.sh | sh
```

The script:

1. checks that the host is macOS;
2. detects `arm64` versus `x86_64`;
3. downloads the corresponding pre-built release archive plus the fixed Grid
   hostname/port and public CA;
4. verifies every downloaded asset against the release SHA-256 list;
5. installs the executable as `/usr/local/bin/okno` (asking for the local Mac
   password through `sudo` only when needed);
6. writes the fixed server configuration and CA under the user's Application
   Support directory;
7. prints the installed version and launch command.

After installation, normal use is simply:

```sh
okno
```

The user is not asked to choose a server and does not need a GitHub account.

## 14. Updates

For v1, running the same one-line installer again installs the latest GitHub
Release and refreshes the fixed public Grid profile. It does not rely on a
system-wide Python installation.

A dedicated `okno --update` command is not required for v1.

## 15. macOS architecture and minimum-version boundary

The release process produces separate Apple Silicon (`arm64`) and Intel
(`x86_64`) archives. The one-line installer hides that distinction from users.

The minimum macOS version of a frozen Python build is determined by the OS and
binary dependencies used to build it. The release-candidate build is not yet
claimed as Developer-ID signed/notarized; the clean-Mac installation gate will
show whether that is needed for a frictionless friend-facing release.

 Before publishing the first friend-facing
release, both artifacts must therefore be tested on the oldest macOS versions
we intend to claim as supported. The owner's Intel Monterey Mac Mini is useful
for validating an older Intel build; the exact public minimum is not claimed
until the real frozen binaries have been built and tested.

## 16. Public-repository anonymity checklist

Before changing the repository from private to public:

- set repository Git author name/email to the pseudonymous identity and GitHub
  noreply address;
- rewrite old commit author metadata if it still contains local personal names
  or addresses;
- search committed files/history for personal names, home hostnames, usernames,
  IP addresses, screenshots, and local paths;
- keep production server state, private keys, access phrases, and backups out of
  Git;
- use a neutral public Grid hostname;
- remember that a public client release necessarily reveals the server endpoint
  to anyone who downloads it; the access phrase remains the authorisation gate.

## 17. Phase 5 acceptance sequence

Before Phase 5 is called complete on the real deployment:

1. full automated test suite passes on the MacBook;
2. `server init`, stopped status, foreground run, live status and live access
   rotation are exercised with temporary state;
3. the Mac Mini is initialised with the chosen neutral public hostname;
4. router/DDNS/public TCP reachability is tested from outside the home LAN;
5. launchd starts the server at boot and restarts it after a forced process stop;
6. server state survives a Mac Mini reboot;
7. backup is created and inspected;
8. Apple Silicon and Intel self-contained release artifacts are built and their
   exact download/installed sizes recorded;
9. a clean Mac installation through the one-line GitHub command launches with
   `okno` and connects without Python/Homebrew setup;
10. access rotation disconnects an installed client and the new phrase works.

Only after those real-machine checks should the v1 deployment be tagged as a
finished Phase 5 release.
