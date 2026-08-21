from __future__ import annotations

import os
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_runtime_dependency_is_only_cryptography(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        self.assertEqual(project["version"], "0.3.0")
        self.assertEqual(project["dependencies"], ["cryptography"])
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["scripts"], {"grid": "the_grid.cli:main"})

    @unittest.skipUnless(os.name == "posix", "POSIX launcher required")
    def test_launcher_is_executable(self) -> None:
        launcher = ROOT / "run"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn(
            "import setuptools.build_meta",
            launcher.read_text(encoding="utf-8"),
        )

    def test_approved_package_structure_exists(self) -> None:
        modules = {
            "__init__.py",
            "__main__.py",
            "cli.py",
            "client.py",
            "relay.py",
            "protocol.py",
            "crypto.py",
            "access.py",
            "phrases.py",
            "hub.py",
            "sessions.py",
            "terminal.py",
            "config.py",
            "models.py",
            "terms.py",
            "ui_text.py",
        }
        package = ROOT / "src" / "the_grid"
        self.assertTrue(modules <= {path.name for path in package.iterdir()})
        self.assertTrue((package / "data" / "grid_words.txt").is_file())

    def test_approved_specification_is_retained(self) -> None:
        text = (ROOT / "docs" / "approved-specification.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Status:** Approved v1 specification", text)
        self.assertIn("# 28. Implementation order", text)

    def test_phase_two_protocol_documents_and_vectors_are_retained(self) -> None:
        required = [
            ROOT / "docs" / "protocol-encodings-v1.md",
            ROOT / "docs" / "cryptographic-test-vectors-v1.md",
            ROOT / "docs" / "phase-2-report.md",
            ROOT / "tests" / "vectors" / "phase2-v1.json",
        ]
        for path in required:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
        protocol = required[0].read_text(encoding="utf-8")
        self.assertIn("Status:** frozen for implementation protocol v1", protocol)
        self.assertIn("session-data-aad-v1", protocol)

    def test_phase_three_protocol_and_report_are_present(self) -> None:
        protocol = ROOT / "docs" / "protocol-transport-v1.md"
        report = ROOT / "docs" / "phase-3-report.md"
        self.assertTrue(protocol.is_file())
        self.assertTrue(report.is_file())
        protocol_text = protocol.read_text(encoding="utf-8")
        self.assertIn("Status:** frozen for implementation protocol v1", protocol_text)
        self.assertIn("board list pagination", protocol_text.lower())
        self.assertIn("board_seen_ids", protocol_text)

    def test_security_and_relay_modules_do_not_import_user_interface_copy(self) -> None:
        package = ROOT / "src" / "the_grid"
        for name in (
            "access.py",
            "crypto.py",
            "hub.py",
            "protocol.py",
            "sessions.py",
            "relay.py",
        ):
            text = (package / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn("ui_text", text)
                self.assertNotIn("from . import terms", text)



if __name__ == "__main__":
    unittest.main()
