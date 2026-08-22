#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

[ "$(uname -s)" = "Darwin" ] || {
    echo "macOS is required to build the macOS release" >&2
    exit 1
}

ARCH=$(uname -m)
case "$ARCH" in
    arm64|x86_64) ;;
    *) echo "unsupported mac architecture: $ARCH" >&2; exit 1 ;;
esac

python3 -c 'import PyInstaller, cryptography, the_grid' >/dev/null 2>&1 || {
    echo "install release build dependencies first: python3 -m pip install -e '.[release]'" >&2
    exit 1
}

rm -rf build/pyinstaller "dist/okno"
python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name okno \
    --collect-data the_grid \
    --distpath dist \
    --workpath build/pyinstaller/work \
    --specpath build/pyinstaller \
    scripts/okno_entry.py

VERSION_OUTPUT=$("dist/okno" --version)

case "$VERSION_OUTPUT" in
    "okno "*) ;;
    *)
        echo "frozen okno returned an invalid version" >&2
        exit 1
        ;;
esac

if [ -n "${OKNO_EXPECTED_VERSION:-}" ] &&
    [ "$VERSION_OUTPUT" != "okno $OKNO_EXPECTED_VERSION" ]; then
    echo "frozen version does not match release tag" >&2
    exit 1
fi

printf '%s\n' "$VERSION_OUTPUT"
mkdir -p build/release
ARCHIVE="build/release/okno-macos-${ARCH}.tar.gz"
rm -f "$ARCHIVE"
tar -C dist -czf "$ARCHIVE" okno
printf '%s\n' "$ARCHIVE"
