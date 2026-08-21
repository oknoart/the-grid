from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from cryptography import x509

from the_grid.server_config import make_server_config
from the_grid.server_tls import (
    SERVER_VALID_DAYS,
    ServerTlsError,
    ca_private_key_path,
    initialise_private_ca_tls,
    renew_server_certificate,
    validate_server_tls,
)


class ServerTlsTests(unittest.TestCase):
    def test_private_ca_tls_is_host_bound_and_private_keys_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_server_config(root, public_host="grid.example.net")
            now = dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)
            status = initialise_private_ca_tls(config, now=now)

            self.assertEqual(os.stat(config.private_key).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(ca_private_key_path(config)).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(config.certificate).st_mode & 0o777, 0o644)
            self.assertEqual(len(status.ca_sha256), 64)
            self.assertGreaterEqual(status.days_remaining, SERVER_VALID_DAYS - 1)

            certificate = x509.load_pem_x509_certificate(config.certificate.read_bytes())
            sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(sans.get_values_for_type(x509.DNSName), ["grid.example.net"])

    def test_ip_public_host_is_placed_in_ip_san(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_server_config(Path(directory), public_host="203.0.113.7")
            initialise_private_ca_tls(config)
            status = validate_server_tls(config)
            self.assertGreater(status.days_remaining, 0)
            certificate = x509.load_pem_x509_certificate(config.certificate.read_bytes())
            sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(str(sans.get_values_for_type(x509.IPAddress)[0]), "203.0.113.7")

    def test_validation_rejects_group_readable_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_server_config(Path(directory), public_host="grid.example.net")
            initialise_private_ca_tls(config)
            os.chmod(config.private_key, 0o640)
            with self.assertRaisesRegex(ServerTlsError, "group/world"):
                validate_server_tls(config)

    def test_renewal_changes_server_certificate_but_preserves_ca(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_server_config(Path(directory), public_host="grid.example.net")
            first_now = dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)
            initialise_private_ca_tls(config, now=first_now)
            ca_before = config.ca_certificate.read_bytes()
            cert_before = config.certificate.read_bytes()
            status = renew_server_certificate(config, now=first_now + dt.timedelta(days=10))
            self.assertEqual(config.ca_certificate.read_bytes(), ca_before)
            self.assertNotEqual(config.certificate.read_bytes(), cert_before)
            self.assertGreater(status.days_remaining, 800)

    def test_validation_rejects_wrong_public_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = make_server_config(root, public_host="grid.example.net")
            initialise_private_ca_tls(original)
            changed = make_server_config(root, public_host="other.example.net")
            with self.assertRaisesRegex(ServerTlsError, "public host"):
                validate_server_tls(changed)


if __name__ == "__main__":
    unittest.main()
