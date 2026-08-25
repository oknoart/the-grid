# Android / Termux

okno can run as a client on Android through Termux.

Android server hosting is not supported.

## install

In Termux:

```sh
curl -fsSL https://raw.githubusercontent.com/oknoart/the-grid/main/install-termux.sh | sh
```

then:

```sh
okno
```

The installer uses Termux's own Python and cryptography packages, installs the
current okno release, and provisions the public Grid connection settings.

No manual Python environment or PATH setup is required.

## notes

The normal terminal interface works in portrait and landscape and responds to
Android keyboard and viewport changes.

The cat animation may pause when the visible terminal becomes too short, for
example while the keyboard is open or in landscape. It resumes automatically
when enough terminal space is available.

Changing between Wi-Fi and mobile data can end the current connection. Once the
new network is active, launch `okno` again.

An ID remains reserved for up to 90 seconds after an unexpected disconnect. If
the same ID is temporarily unavailable after a network change, wait for the
reservation to expire and try it again.

## tested

The 0.8.0 development release has been tested on a Google Pixel 9a running
Android 17 with Termux and Termux Python 3.13.
