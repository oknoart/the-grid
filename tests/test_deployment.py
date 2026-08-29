from __future__ import annotations

import hashlib
import os
import plistlib
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentFilesTests(unittest.TestCase):
    def test_one_line_installer_is_macos_native_and_does_not_require_python_or_homebrew(self) -> None:
        installer = ROOT / "install.sh"
        self.assertTrue(installer.is_file())
        self.assertTrue(os.access(installer, os.X_OK))
        text = installer.read_text(encoding="utf-8")
        self.assertIn("oknoart/the-grid", text)
        self.assertIn("releases/latest/download", text)
        self.assertIn("uname -m", text)
        self.assertIn("sysctl.proc_translated", text)
        self.assertIn("arm64", text)
        self.assertIn("x86_64", text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn('.local/bin', text)
        self.assertIn("sw_vers -productVersion", text)
        self.assertIn("MACOS_MAJOR", text)
        self.assertNotIn("sudo ", text)

        # The downloaded executable must run successfully before it is installed.
        version_check = text.index("VERSION_OUTPUT=$(./okno --version")
        binary_install = text.index("install -m 755 ./okno")
        self.assertLess(version_check, binary_install)
        self.assertNotIn("brew install", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("python3", text)
        subprocess.run(["sh", "-n", installer], check=True)

    def test_one_line_installer_provisions_a_clean_mac_without_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            payload = root / "payload"
            payload.mkdir()
            okno = payload / "okno"
            okno.write_text(
                "#!/bin/sh\n[ \"${1:-}\" = \"--version\" ] && { echo 'okno 0.5.0'; exit 0; }\nexit 0\n",
                encoding="utf-8",
            )
            okno.chmod(0o755)
            archive = fixtures / "okno-macos-arm64.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(okno, arcname="okno", recursive=False)
            (fixtures / "okno-grid-host.txt").write_text("grid.example.net\n", encoding="utf-8")
            (fixtures / "okno-grid-port.txt").write_text("7331\n", encoding="ascii")
            (fixtures / "okno-grid-ca.pem").write_text(
                "-----BEGIN CERTIFICATE-----\npublic-test-ca\n-----END CERTIFICATE-----\n",
                encoding="ascii",
            )
            assets = (
                "okno-macos-arm64.tar.gz",
                "okno-grid-host.txt",
                "okno-grid-port.txt",
                "okno-grid-ca.pem",
            )
            checksums = "".join(
                f"{hashlib.sha256((fixtures / name).read_bytes()).hexdigest()}  {name}\n"
                for name in assets
            )
            (fixtures / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")

            fakebin = root / "fakebin"
            fakebin.mkdir()
            uname = fakebin / "uname"
            uname.write_text(
                "#!/bin/sh\ncase \"${1:-}\" in -s) echo Darwin ;; -m) echo arm64 ;; *) echo Darwin ;; esac\n",
                encoding="utf-8",
            )
            uname.chmod(0o755)
            sysctl = fakebin / "sysctl"
            sysctl.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            sysctl.chmod(0o755)
            sw_vers = fakebin / "sw_vers"
            sw_vers.write_text(
                "#!/bin/sh\n[ \"${1:-}\" = \"-productVersion\" ] && echo 12.7.6\n",
                encoding="utf-8",
            )
            sw_vers.chmod(0o755)
            curl = fakebin / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "url=''\nout=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    http*) url=$1 ;;\n"
                "    -o) shift; out=$1 ;;\n"
                "  esac\n"
                "  shift\n"
                "done\n"
                "[ -n \"$url\" ] && [ -n \"$out\" ] || exit 2\n"
                "cp \"$OKNO_FIXTURES/${url##*/}\" \"$out\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            home = root / "home"
            home.mkdir()
            install_dir = root / "bin"
            install_dir.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "OKNO_INSTALL_DIR": str(install_dir),
                    "OKNO_FIXTURES": str(fixtures),
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                }
            )
            completed = subprocess.run(
                ["sh", str(ROOT / "install.sh")],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("installed okno 0.5.0", completed.stdout)
            self.assertTrue((install_dir / "okno").is_file())
            config = home / "Library" / "Application Support" / "okno" / "config.json"
            self.assertTrue(config.is_file())
            text = config.read_text(encoding="utf-8")
            self.assertIn('"host": "grid.example.net"', text)
            self.assertIn('"port": 7331', text)
            ca = home / "Library" / "Application Support" / "okno" / "grid-ca.pem"
            self.assertTrue(ca.is_file())

    def test_one_line_installer_prepends_default_bin_when_it_is_late_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            payload = root / "payload"
            payload.mkdir()
            okno = payload / "okno"
            okno.write_text(
                "#!/bin/sh\n[ \"${1:-}\" = \"--version\" ] && { echo 'okno 0.5.0'; exit 0; }\nexit 0\n",
                encoding="utf-8",
            )
            okno.chmod(0o755)
            archive = fixtures / "okno-macos-arm64.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(okno, arcname="okno", recursive=False)
            (fixtures / "okno-grid-host.txt").write_text("grid.example.net\n", encoding="utf-8")
            (fixtures / "okno-grid-port.txt").write_text("7331\n", encoding="ascii")
            (fixtures / "okno-grid-ca.pem").write_text(
                "-----BEGIN CERTIFICATE-----\npublic-test-ca\n-----END CERTIFICATE-----\n",
                encoding="ascii",
            )
            assets = (
                "okno-macos-arm64.tar.gz",
                "okno-grid-host.txt",
                "okno-grid-port.txt",
                "okno-grid-ca.pem",
            )
            checksums = "".join(
                f"{hashlib.sha256((fixtures / name).read_bytes()).hexdigest()}  {name}\n"
                for name in assets
            )
            (fixtures / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")

            fakebin = root / "fakebin"
            fakebin.mkdir()
            uname = fakebin / "uname"
            uname.write_text(
                "#!/bin/sh\ncase \"${1:-}\" in -s) echo Darwin ;; -m) echo arm64 ;; *) echo Darwin ;; esac\n",
                encoding="utf-8",
            )
            uname.chmod(0o755)
            sysctl = fakebin / "sysctl"
            sysctl.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            sysctl.chmod(0o755)
            sw_vers = fakebin / "sw_vers"
            sw_vers.write_text(
                "#!/bin/sh\n[ \"${1:-}\" = \"-productVersion\" ] && echo 12.7.6\n",
                encoding="utf-8",
            )
            sw_vers.chmod(0o755)
            curl = fakebin / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "url=''\nout=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    http*) url=$1 ;;\n"
                "    -o) shift; out=$1 ;;\n"
                "  esac\n"
                "  shift\n"
                "done\n"
                "[ -n \"$url\" ] && [ -n \"$out\" ] || exit 2\n"
                "cp \"$OKNO_FIXTURES/${url##*/}\" \"$out\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            home = root / "home"
            home.mkdir()
            install_dir = home / ".local" / "bin"
            profile = home / ".zprofile"
            profile.write_text(
                'export PATH="$PATH:$HOME/.local/bin"\n',
                encoding="utf-8",
            )

            # Simulate an older system-wide okno which would otherwise win.
            legacybin = root / "legacybin"
            legacybin.mkdir()
            legacy_okno = legacybin / "okno"
            legacy_okno.write_text(
                "#!/bin/sh\necho 'okno legacy'\n",
                encoding="utf-8",
            )
            legacy_okno.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "OKNO_FIXTURES": str(fixtures),
                    "SHELL": "/bin/zsh",
                    "PATH": f"{fakebin}:{legacybin}:/usr/bin:/bin:{install_dir}",
                }
            )
            completed = subprocess.run(
                ["sh", str(ROOT / "install.sh")],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("installed okno 0.5.0", completed.stdout)
            self.assertTrue((install_dir / "okno").is_file())
            profile_text = profile.read_text(encoding="utf-8")
            self.assertIn('export PATH="$HOME/.local/bin:$PATH"', profile_text)
            self.assertIn("open a new terminal", completed.stdout)
            config = home / "Library" / "Application Support" / "okno" / "config.json"
            self.assertTrue(config.is_file())
            text = config.read_text(encoding="utf-8")
            self.assertIn('"host": "grid.example.net"', text)
            self.assertIn('"port": 7331', text)
            ca = home / "Library" / "Application Support" / "okno" / "grid-ca.pem"
            self.assertTrue(ca.is_file())

            # The public website command prepares ~/.local/bin in the current
            # shell before invoking the installer. Even if the installer also
            # repairs the profile, no Terminal restart should then be needed.
            profile.write_text(
                'export PATH="$PATH:$HOME/.local/bin"\n',
                encoding="utf-8",
            )
            env["PATH"] = f"{install_dir}:{fakebin}:{legacybin}:/usr/bin:/bin"

            immediate = subprocess.run(
                ["sh", str(ROOT / "install.sh")],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("launch with:", immediate.stdout)
            self.assertNotIn("open a new terminal", immediate.stdout)

            current_shell = subprocess.run(
                ["sh", "-c", "command -v okno; okno --version"],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                current_shell.stdout.splitlines(),
                [str(install_dir / "okno"), "okno 0.5.0"],
            )

    def test_release_builder_freezes_one_terminal_executable(self) -> None:
        script = ROOT / "scripts" / "build-macos-release.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("--onefile", text)
        self.assertIn("--name okno", text)
        self.assertIn("--collect-data the_grid", text)
        self.assertIn("okno-macos-${ARCH}.tar.gz", text)
        self.assertIn("OKNO_EXPECTED_VERSION", text)
        self.assertIn('VERSION_OUTPUT=$("dist/okno" --version)', text)
        subprocess.run(["sh", "-n", script], check=True)

    def test_pyinstaller_is_release_only_not_runtime_dependency(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["dependencies"], ["cryptography"])
        self.assertIn("pyinstaller", project["optional-dependencies"]["release"][0].lower())

    def test_launchd_service_runs_as_server_owner_at_boot_and_keeps_alive(self) -> None:
        template = ROOT / "deploy" / "macos" / "com.okno.grid.plist.template"
        raw = template.read_bytes()
        # Substitute placeholders with XML-safe simple values before parsing.
        rendered = (
            raw.replace(b"__OKNO_BINARY__", b"/usr/local/bin/okno")
            .replace(b"__OKNO_SERVER_CONFIG__", b"/tmp/server.json")
            .replace(b"__OKNO_USER__", b"example")
            .replace(b"__OKNO_STATE_DIR__", b"/tmp/okno-server")
        )
        parsed = plistlib.loads(rendered)
        self.assertEqual(parsed["Label"], "com.okno.grid")
        self.assertEqual(parsed["ProgramArguments"][:3], ["/usr/local/bin/okno", "server", "run"])
        self.assertEqual(parsed["UserName"], "example")
        self.assertTrue(parsed["RunAtLoad"])
        self.assertTrue(parsed["KeepAlive"])
        self.assertNotEqual(parsed["UserName"], "root")

        for script_name in ("install-server-service.sh", "remove-server-service.sh"):
            script = ROOT / "deploy" / "macos" / script_name
            self.assertTrue(os.access(script, os.X_OK))
            subprocess.run(["sh", "-n", script], check=True)

    def test_server_service_installer_is_idempotent_and_verifies_launchd(self) -> None:
        script = ROOT / "deploy" / "macos" / "install-server-service.sh"
        text = script.read_text(encoding="utf-8")

        self.assertIn('service_loaded()', text)
        self.assertIn('launchctl print "system/$LABEL"', text)
        self.assertIn('if service_loaded; then', text)
        self.assertIn('launchctl bootout "system/$LABEL"', text)
        self.assertGreaterEqual(text.count("launchctl bootstrap system"), 2)
        self.assertIn('launchctl kickstart -k "system/$LABEL"', text)
        self.assertIn("sleep 1", text)

        # The replacement plist is safely installed before the existing
        # service is stopped.
        plist_install = text.index(
            'sudo install -o root -g wheel -m 644 "$TMP" "$PLIST"'
        )
        service_stop = text.index('if service_loaded; then')
        self.assertLess(plist_install, service_stop)

        subprocess.run(["sh", "-n", script], check=True)

    def test_ci_covers_macos_and_linux_and_release_builds_both_mac_architectures(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("ubuntu-latest", ci)
        self.assertIn("macos-15", ci)
        self.assertIn("3.11", ci)
        self.assertIn("3.14", ci)

        release = (ROOT / ".github" / "workflows" / "release-macos.yml").read_text(encoding="utf-8")
        self.assertIn("macos-15-intel", release)
        self.assertIn("arm64", release)
        self.assertIn("x86_64", release)
        self.assertIn("OKNO_GRID_HOST", release)
        self.assertIn("OKNO_GRID_CA_B64", release)
        self.assertIn("SHA256SUMS.txt", release)


if __name__ == "__main__":
    unittest.main()
