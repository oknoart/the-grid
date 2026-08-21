#!/bin/sh
set -eu
LABEL=com.okno.grid
PLIST="/Library/LaunchDaemons/$LABEL.plist"
sudo launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
if [ -f "$PLIST" ]; then
    sudo rm -f "$PLIST"
fi
printf 'okno grid service removed (server data was not deleted)\n'
