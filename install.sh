#!/bin/sh
set -eu

REPO=${OKNO_REPO:-oknoart/the-grid}
BASE_URL="https://github.com/${REPO}/releases/latest/download"
INSTALL_DIR=${OKNO_INSTALL_DIR:-/usr/local/bin}
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
MACHINE=$(uname -m)
if [ "$MACHINE" = "x86_64" ] && [ "$(sysctl -in sysctl.proc_translated 2>/dev/null || printf 0)" = "1" ]; then
    # A translated x86 shell on Apple Silicon should still install the native build.
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
    curl -fsSL "$BASE_URL/$asset" -o "$asset" || fail "could not download $asset"
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

HOST=$(tr -d '\r\n' < okno-grid-host.txt)
PORT=$(tr -d '\r\n' < okno-grid-port.txt)
case "$HOST" in
    ''|*[!A-Za-z0-9._:-]*) fail "release contains an invalid grid host" ;;
esac
case "$PORT" in
    ''|*[!0-9]*) fail "release contains an invalid grid port" ;;
esac
[ "$PORT" -ge 1 ] 2>/dev/null && [ "$PORT" -le 65535 ] 2>/dev/null || fail "release contains an invalid grid port"

if [ ! -d "$INSTALL_DIR" ]; then
    sudo mkdir -p "$INSTALL_DIR"
fi
if [ -w "$INSTALL_DIR" ]; then
    install -m 755 ./okno "$INSTALL_DIR/okno"
else
    sudo install -m 755 ./okno "$INSTALL_DIR/okno"
fi

mkdir -p "$APP_SUPPORT"
chmod 700 "$APP_SUPPORT"
install -m 644 okno-grid-ca.pem "$CA_DEST"

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}
HOST_JSON=$(json_escape "$HOST")
CA_JSON=$(json_escape "$CA_DEST")
cat > "$CONFIG_DEST" <<EOF
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
chmod 600 "$CONFIG_DEST"

printf '\ninstalled %s\n\n' "$("$INSTALL_DIR/okno" --version)"
printf 'launch with:\n\n  okno\n\n'
