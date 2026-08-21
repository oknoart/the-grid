#!/bin/sh
set -eu

LABEL=com.okno.grid
PLIST="/Library/LaunchDaemons/$LABEL.plist"
USER_NAME=$(id -un)
OKNO_BINARY=${OKNO_BINARY:-$(command -v okno || true)}
[ -n "$OKNO_BINARY" ] || { echo "okno is not installed on this Mac" >&2; exit 1; }

DEFAULT_STATE="$HOME/Library/Application Support/okno/server"
STATE_DIR=${OKNO_SERVER_STATE_DIR:-$DEFAULT_STATE}
CONFIG=${OKNO_SERVER_CONFIG:-$STATE_DIR/server.json}
[ -f "$CONFIG" ] || { echo "server config not found: $CONFIG" >&2; exit 1; }

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TEMPLATE="$ROOT/deploy/macos/com.okno.grid.plist.template"
TMP=$(mktemp "${TMPDIR:-/tmp}/okno-launchd.XXXXXX")
trap 'rm -f "$TMP"' EXIT HUP INT TERM

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}
BIN_ESC=$(escape_sed "$OKNO_BINARY")
CONFIG_ESC=$(escape_sed "$CONFIG")
STATE_ESC=$(escape_sed "$STATE_DIR")
USER_ESC=$(escape_sed "$USER_NAME")
sed \
    -e "s|__OKNO_BINARY__|$BIN_ESC|g" \
    -e "s|__OKNO_SERVER_CONFIG__|$CONFIG_ESC|g" \
    -e "s|__OKNO_STATE_DIR__|$STATE_ESC|g" \
    -e "s|__OKNO_USER__|$USER_ESC|g" \
    "$TEMPLATE" > "$TMP"

plutil -lint "$TMP" >/dev/null
sudo launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
sudo install -o root -g wheel -m 644 "$TMP" "$PLIST"
sudo launchctl bootstrap system "$PLIST"
sudo launchctl kickstart -k "system/$LABEL"
printf 'okno grid service installed and started\n'
printf 'status: sudo launchctl print system/%s\n' "$LABEL"
