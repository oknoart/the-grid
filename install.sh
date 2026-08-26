#!/bin/sh

set -eu

REPO=${OKNO_REPO:-oknoart/the-grid}
BASE_URL="https://github.com/${REPO}/releases/latest/download"

DEFAULT_INSTALL_DIR="$HOME/.local/bin"
INSTALL_DIR=${OKNO_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}

APP_SUPPORT="$HOME/Library/Application Support/okno"
CA_DEST="$APP_SUPPORT/grid-ca.pem"
CONFIG_DEST="$APP_SUPPORT/config.json"

fail() {
    printf '%s\n' "okno install: $*" >&2
    exit 1
}

cleanup() {
    [ -n "${TMPDIR_OKNO:-}" ] && rm -rf "$TMPDIR_OKNO"
}

trap cleanup EXIT HUP INT TERM

[ "$(uname -s)" = "Darwin" ] || fail "this release currently supports macOS only"

command -v sw_vers >/dev/null 2>&1 || fail "could not determine macOS version"

MACOS_VERSION=$(sw_vers -productVersion 2>/dev/null) ||
    fail "could not determine macOS version"

MACOS_MAJOR=${MACOS_VERSION%%.*}

case "$MACOS_MAJOR" in
    ''|*[!0-9]*) fail "could not determine macOS version" ;;
esac

[ "$MACOS_MAJOR" -ge 12 ] ||
    fail "macOS 12 or newer is required (found $MACOS_VERSION)"

MACHINE=$(uname -m)

if [ "$MACHINE" = "x86_64" ] &&
    [ "$(sysctl -in sysctl.proc_translated 2>/dev/null || printf 0)" = "1" ]; then
    MACHINE=arm64
fi

case "$MACHINE" in
    arm64) ARCH=arm64 ;;
    x86_64) ARCH=x86_64 ;;
    *) fail "unsupported mac architecture: $MACHINE" ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
command -v shasum >/dev/null 2>&1 || fail "shasum is required"
command -v install >/dev/null 2>&1 || fail "install is required"

TMPDIR_OKNO=$(mktemp -d "${TMPDIR:-/tmp}/okno-install.XXXXXX")
cd "$TMPDIR_OKNO"

ARCHIVE="okno-macos-${ARCH}.tar.gz"

for asset in \
    "$ARCHIVE" \
    okno-grid-host.txt \
    okno-grid-port.txt \
    okno-grid-ca.pem \
    SHA256SUMS.txt

do
    curl -fsSL "$BASE_URL/$asset" -o "$asset" ||
        fail "could not download $asset"
done

verify_asset() {
    asset=$1
    expected=$(awk -v name="$asset" '$2 == name { print $1; exit }' SHA256SUMS.txt)
    [ -n "$expected" ] || fail "checksum missing for $asset"

    actual=$(shasum -a 256 "$asset" | awk '{print $1}')
    [ "$actual" = "$expected" ] || fail "checksum failed for $asset"
}

verify_asset "$ARCHIVE"
verify_asset okno-grid-host.txt
verify_asset okno-grid-port.txt
verify_asset okno-grid-ca.pem

tar -xzf "$ARCHIVE"

[ -x ./okno ] || fail "release archive does not contain okno"

# Prove that this release can actually run on this Mac before installing it.
if VERSION_OUTPUT=$(./okno --version 2>/dev/null); then
    :
else
    fail "this okno release cannot run on macOS $MACOS_VERSION"
fi

case "$VERSION_OUTPUT" in
    "okno "*) ;;
    *) fail "downloaded okno returned an invalid version" ;;
esac

HOST=$(tr -d '\r\n' < okno-grid-host.txt)
PORT=$(tr -d '\r\n' < okno-grid-port.txt)

case "$HOST" in
    ''|*[!A-Za-z0-9._:-]*) fail "release contains an invalid grid host" ;;
esac

case "$PORT" in
    ''|*[!0-9]*) fail "release contains an invalid grid port" ;;
esac

[ "$PORT" -ge 1 ] 2>/dev/null &&
    [ "$PORT" -le 65535 ] 2>/dev/null ||
    fail "release contains an invalid grid port"

mkdir -p "$INSTALL_DIR" ||
    fail "could not create install directory"

[ -w "$INSTALL_DIR" ] ||
    fail "install directory is not writable: $INSTALL_DIR"

BINARY_TMP="$INSTALL_DIR/.okno-install.$$"

install -m 755 ./okno "$BINARY_TMP" ||
    fail "could not install okno"

mv -f "$BINARY_TMP" "$INSTALL_DIR/okno" ||
    fail "could not finish installing okno"

mkdir -p "$APP_SUPPORT" ||
    fail "could not create okno application support"

chmod 700 "$APP_SUPPORT"

install -m 644 okno-grid-ca.pem "$CA_DEST" ||
    fail "could not install grid certificate"

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

HOST_JSON=$(json_escape "$HOST")
CA_JSON=$(json_escape "$CA_DEST")

CONFIG_TMP="$APP_SUPPORT/.config.json.$$"

cat > "$CONFIG_TMP" <<EOF
{
  "server": {
    "host": "$HOST_JSON",
    "port": $PORT,
    "ca_file": "$CA_JSON"
  },
  "ui": {
    "color": true,
    "plain": false
  }
}
EOF

chmod 600 "$CONFIG_TMP"

mv -f "$CONFIG_TMP" "$CONFIG_DEST" ||
    fail "could not install configuration"

PATH_ADDED=0

# Normal installs are entirely inside the user's home folder. Ensure
# the executable directory takes precedence in future shells.
if [ -z "${OKNO_INSTALL_DIR+x}" ]; then
    case "${SHELL:-}" in
        */zsh) PROFILE="$HOME/.zprofile" ;;
        */bash) PROFILE="$HOME/.bash_profile" ;;
        *) PROFILE="$HOME/.profile" ;;
    esac

    PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

    if [ ! -f "$PROFILE" ]; then
        : > "$PROFILE" 2>/dev/null || true
    fi

    if [ -w "$PROFILE" ] &&
        ! grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1; then
        printf '\n# okno\n%s\n' "$PATH_LINE" >> "$PROFILE"
        PATH_ADDED=1
    fi
fi

printf '\ninstalled %s\n\n' "$VERSION_OUTPUT"

if [ "$PATH_ADDED" -eq 1 ]; then
    printf 'open a new terminal, then:\n\n  okno\n\n'
else
    printf 'launch with:\n\n  okno\n\n'
fi
