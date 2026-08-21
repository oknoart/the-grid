from __future__ import annotations

import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from the_grid.access import create_initial_access, save_initial_access
from the_grid.hub import BoardStore
from the_grid.server_admin import (
    BACKUP_MANIFEST_NAME,
    ServerAdminError,
    backup_server,
    export_client_profile,
)
from the_grid.server_config import make_server_config, save_server_config
from the_grid.server_tls import initialise_private_ca_tls


class ServerAdminTests(unittest.TestCase):
    def _initialised(self, root: Path):
        config = make_server_config(
            root,
            public_host="grid.example.net",
            listen_host="127.0.0.1",
        )
        config_path = root / "server.json"
        save_server_config(config, config_path)
        initialise_private_ca_tls(config)
        setup = create_initial_access()
        save_initial_access(
            setup,
            server_id_path=config.server_id,
            access_state_path=config.access_state,
        )
        store = BoardStore(config.database, access_generation=setup.context.access_generation)
        store.close()
        os.chmod(config.database, 0o600)
        return config, config_path

    def test_client_export_contains_only_public_deployment_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            config, config_path = self._initialised(root)
            output = Path(directory) / "client"
            paths = export_client_profile(output, config_path)
            self.assertEqual({path.name for path in paths}, {
                "okno-grid-host.txt",
                "okno-grid-port.txt",
                "okno-grid-ca.pem",
                "okno-grid-profile.json",
            })
            self.assertEqual((output / "okno-grid-host.txt").read_text(), "grid.example.net\n")
            manifest = json.loads((output / "okno-grid-profile.json").read_text())
            self.assertEqual(manifest["host"], "grid.example.net")
            self.assertNotIn("private", json.dumps(manifest).lower())
            exported_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore") for path in paths
            )
            self.assertNotIn("PRIVATE KEY", exported_text)
            self.assertNotIn(config.access_state.read_text(), exported_text)

    def test_backup_is_owner_only_and_excludes_runtime_files_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            config, config_path = self._initialised(root)
            config.pid_file.write_text("123\n", encoding="ascii")
            config.log_file.write_text("metadata log\n", encoding="utf-8")
            backup = Path(directory) / "okno-backup.tar.gz"
            result = backup_server(backup, config_path)
            self.assertEqual(result, backup)
            self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ServerAdminError, "backup output already exists"):
                backup_server(backup, config_path)
            with tarfile.open(backup, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn(BACKUP_MANIFEST_NAME, names)
                self.assertIn("server.json", names)
                self.assertIn("server-id.bin", names)
                self.assertIn("access-state.json", names)
                self.assertIn("grid.sqlite3", names)
                self.assertIn("tls/grid-ca-key.pem", names)
                self.assertIn("tls/server-key.pem", names)
                self.assertNotIn("server.pid", names)
                self.assertNotIn("server.log", names)
                self.assertNotIn("admin.sock", names)
                manifest = json.loads(archive.extractfile(BACKUP_MANIFEST_NAME).read())
                self.assertEqual(manifest["v"], 1)
                self.assertEqual(manifest["public_host"], "grid.example.net")
                self.assertNotIn("verifier_key", manifest)


if __name__ == "__main__":
    unittest.main()
