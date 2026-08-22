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
# Install the new service definition before touching the running service.
sudo install -o root -g wheel -m 644 "$TMP" "$PLIST"

service_loaded() {
    sudo launchctl print "system/$LABEL" >/dev/null 2>&1
}

# Stop the existing service only when launchd currently knows about it.
if service_loaded; then
    sudo launchctl bootout "system/$LABEL" ||
        { echo "could not stop existing okno grid service" >&2; exit 1; }
fi

# launchd can briefly retain state after bootout. Retry bootstrap once
# after clearing any stale registration.
if ! sudo launchctl bootstrap system "$PLIST"; then
    sleep 1
    sudo launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
    sudo launchctl bootstrap system "$PLIST" ||
        { echo "could not load okno grid service" >&2; exit 1; }
fi

sudo launchctl kickstart -k "system/$LABEL" ||
    { echo "could not start okno grid service" >&2; exit 1; }

sleep 1

service_loaded ||
    { echo "okno grid service did not remain loaded" >&2; exit 1; }

printf 'okno grid service installed and started\n'
printf 'status: sudo launchctl print system/%s\n' "$LABEL"
