# okno

the internet got too big.

okno is a small terminal messenger for people you know.

the grid is the network
the hub is the shared room
a comm is private, two-person and end-to-end encrypted.

## install

macOS — apple silicon or intel.

```sh
curl -fsSL https://raw.githubusercontent.com/oknoart/the-grid/main/install.sh | sh
```

then:

```sh
okno
```

you’ll need the four-word access phrase from whoever runs the grid.

## the grid

hub messages are encrypted before they’re stored and disappear after a while.

comms are end-to-end encrypted and aren’t intentionally persisted by the server.

your id is three characters and lasts for the session.

okno isn’t tor. the grid operator can still see normal network metadata.

## run your own

the server is included.

```text
okno server init --public-host HOST
okno server run
okno server status
okno server rotate-access
okno server backup --output FILE
```

see [`docs/phase-5-deployment.md`](docs/phase-5-deployment.md).

## source

python 3.11+.

```sh
./run
```

tests:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

protocol, crypto and deployment docs are in [`docs/`](docs/).

## v0.5.1

macOS for now.

do it yourself.
