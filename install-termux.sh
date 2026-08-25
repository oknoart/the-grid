#!/bin/sh

set -eu

REPO=${OKNO_REPO:-oknoart/the-grid}
BASE_URL=${OKNO_BASE_URL:-https://github.com/${REPO}/releases/latest/download}

fail() {
    printf '%s\n' "okno install: $*" >&2
    exit 1
}

cleanup() {
    [ -n "${TMPDIR_OKNO:-}" ] && rm -rf "$TMPDIR_OKNO"
}

trap cleanup EXIT HUP INT TERM

[ "${PREFIX:-}" = "/data/data/com.termux/files/usr" ] ||
    fail "this installer is for Termux on Android"

command -v pkg >/dev/null 2>&1 ||
    fail "Termux pkg command not found"

printf '%s\n' "installing Termux dependencies..."
pkg install -y python python-cryptography curl

command -v python >/dev/null 2>&1 ||
    fail "python installation failed"

command -v curl >/dev/null 2>&1 ||
    fail "curl installation failed"

command -v sha256sum >/dev/null 2>&1 ||
    fail "sha256sum is required"

command -v install >/dev/null 2>&1 ||
    fail "install is required"

python -c 'import sys; raise SystemExit(0 if sys.platform == "android" else 1)' ||
    fail "Termux Python does not report Android"

python -c 'import cryptography' >/dev/null 2>&1 ||
    fail "Termux python-cryptography installation failed"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/okno"
CA_DEST="$CONFIG_DIR/grid-ca.pem"
CONFIG_DEST="$CONFIG_DIR/config.json"

TMPDIR_OKNO=$(mktemp -d "${TMPDIR:-$PREFIX/tmp}/okno-install.XXXXXX")
cd "$TMPDIR_OKNO"

for asset in \
    okno-version.txt \
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

    actual=$(sha256sum "$asset" | awk '{print $1}')
    [ "$actual" = "$expected" ] || fail "checksum failed for $asset"
}

verify_asset okno-version.txt
verify_asset okno-grid-host.txt
verify_asset okno-grid-port.txt
verify_asset okno-grid-ca.pem

VERSION=$(tr -d '\r\n' < okno-version.txt)

case "$VERSION" in
    ''|*[!A-Za-z0-9._-]*) fail "release contains an invalid version" ;;
esac

WHEEL="okno-${VERSION}-py3-none-any.whl"

curl -fsSL "$BASE_URL/$WHEEL" -o "$WHEEL" ||
    fail "could not download $WHEEL"

verify_asset "$WHEEL"

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

python -m pip install \
    --no-deps \
    --force-reinstall \
    "$WHEEL" ||
    fail "could not install okno"

OKNO_BIN="$PREFIX/bin/okno"

[ -x "$OKNO_BIN" ] ||
    fail "okno launcher was not installed"

VERSION_OUTPUT=$("$OKNO_BIN" --version 2>/dev/null) ||
    fail "installed okno could not run"

[ "$VERSION_OUTPUT" = "okno $VERSION" ] ||
    fail "installed okno version does not match release"

mkdir -p "$CONFIG_DIR" ||
    fail "could not create okno config directory"

chmod 700 "$CONFIG_DIR"

install -m 644 okno-grid-ca.pem "$CA_DEST" ||
    fail "could not install grid certificate"

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

HOST_JSON=$(json_escape "$HOST")
CA_JSON=$(json_escape "$CA_DEST")

CONFIG_TMP="$CONFIG_DIR/.config.json.$$"

cat > "$CONFIG_TMP" <<EOF_CONFIG
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
EOF_CONFIG

chmod 600 "$CONFIG_TMP"

mv -f "$CONFIG_TMP" "$CONFIG_DEST" ||
    fail "could not install configuration"

printf '\ninstalled %s\n\n' "$VERSION_OUTPUT"
printf 'launch with:\n\n  okno\n\n'
