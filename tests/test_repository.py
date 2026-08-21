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
        self.assertEqual(project["dependencies"], ["cryptography"])
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["scripts"], {"grid": "the_grid.cli:main"})

    @unittest.skipUnless(os.name == "posix", "POSIX launcher required")
    def test_launcher_is_executable(self) -> None:
        self.assertTrue(os.access(ROOT / "run", os.X_OK))

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


if __name__ == "__main__":
    unittest.main()
