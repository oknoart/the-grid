#!/bin/sh

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

VERSION=$(PYTHONPATH=src python3 -c 'from the_grid import __version__; print(__version__)')

if [ -n "${OKNO_EXPECTED_VERSION:-}" ] &&
    [ "$VERSION" != "$OKNO_EXPECTED_VERSION" ]; then
    printf '%s\n' "package version does not match expected version" >&2
    exit 1
fi

mkdir -p build/release
rm -f build/release/okno-*-py3-none-any.whl

python3 -m pip wheel \
    --no-deps \
    --wheel-dir build/release \
    .

WHEEL="build/release/okno-${VERSION}-py3-none-any.whl"

[ -f "$WHEEL" ] || {
    printf '%s\n' "expected wheel was not produced: $WHEEL" >&2
    exit 1
}

printf '%s\n' "$WHEEL"
