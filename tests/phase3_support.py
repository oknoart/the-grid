from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from the_grid.access import AccessSetup, create_initial_access
from the_grid.client import create_client_ssl_context
from the_grid.relay import RelayLimits, RelayServer, create_server_ssl_context


class FakeClock:
    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def access_setup() -> AccessSetup:
    words = iter(["velvet", "orbit", "cabin", "cedar"])
    return create_initial_access(phrase_sampler=lambda _words, _count: tuple(words))


def make_tls_files(directory: Path) -> tuple[Path, Path]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def start_tls_server(
    directory: Path,
    *,
    limits: RelayLimits | None = None,
    clock=None,
):
    setup = access_setup()
    cert, key = make_tls_files(directory)
    server = RelayServer(
        context=setup.context,
        verifier_state=setup.verifier_state,
        database=directory / "grid.sqlite3",
        host="127.0.0.1",
        port=0,
        ssl_context=create_server_ssl_context(cert, key),
        limits=limits,
        **({} if clock is None else {"clock": clock}),
    )
    await server.start()
    host, port = server.address
    client_context = create_client_ssl_context(cert)
    return setup, server, host, port, client_context


async def start_plain_server(
    directory: Path,
    *,
    limits: RelayLimits | None = None,
    clock=None,
):
    setup = access_setup()
    server = RelayServer(
        context=setup.context,
        verifier_state=setup.verifier_state,
        database=directory / "grid.sqlite3",
        host="127.0.0.1",
        port=0,
        allow_plain=True,
        limits=limits,
        **({} if clock is None else {"clock": clock}),
    )
    await server.start()
    host, port = server.address
    return setup, server, host, port
