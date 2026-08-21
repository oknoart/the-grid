"""Private-CA TLS provisioning and validation for the personal Grid server."""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .server_config import ServerConfig

CA_COMMON_NAME: Final = "okno grid private ca"
SERVER_COMMON_NAME: Final = "okno grid server"
CA_VALID_DAYS: Final = 3650
SERVER_VALID_DAYS: Final = 825
TLS_RENEW_WARNING_DAYS: Final = 30


class ServerTlsError(ValueError):
    """Raised when TLS material cannot be created or safely used."""


@dataclass(frozen=True, slots=True)
class ServerTlsStatus:
    expires_at: dt.datetime
    days_remaining: int
    ca_sha256: str


def ca_private_key_path(config: ServerConfig) -> Path:
    return config.ca_certificate.with_name("grid-ca-key.pem")


def initialise_private_ca_tls(
    config: ServerConfig,
    *,
    now: dt.datetime | None = None,
    overwrite: bool = False,
) -> ServerTlsStatus:
    """Create a private CA and one server certificate for the configured public host."""

    if not isinstance(config, ServerConfig):
        raise TypeError("config must be ServerConfig")
    current = _utc_now() if now is None else _require_aware_utc(now)
    targets = (
        config.ca_certificate,
        ca_private_key_path(config),
        config.certificate,
        config.private_key,
    )
    if not overwrite and any(path.exists() for path in targets):
        raise ServerTlsError("tls material already exists")

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - dt.timedelta(minutes=5))
        .not_valid_after(current + dt.timedelta(days=CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    server_key = ec.generate_private_key(ec.SECP256R1())
    server_certificate = _build_server_certificate(
        config,
        ca_key=ca_key,
        ca_certificate=ca_certificate,
        server_key=server_key,
        now=current,
    )

    _write_private_key(ca_private_key_path(config), ca_key, overwrite=overwrite)
    _write_certificate(config.ca_certificate, ca_certificate, overwrite=overwrite)
    _write_private_key(config.private_key, server_key, overwrite=overwrite)
    _write_certificate(config.certificate, server_certificate, overwrite=overwrite)
    return validate_server_tls(config, now=current)


def renew_server_certificate(
    config: ServerConfig,
    *,
    now: dt.datetime | None = None,
) -> ServerTlsStatus:
    """Issue a fresh server key/certificate from the retained private CA."""

    if not isinstance(config, ServerConfig):
        raise TypeError("config must be ServerConfig")
    current = _utc_now() if now is None else _require_aware_utc(now)
    try:
        ca_certificate = x509.load_pem_x509_certificate(config.ca_certificate.read_bytes())
        ca_key = serialization.load_pem_private_key(
            ca_private_key_path(config).read_bytes(),
            password=None,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ServerTlsError("private ca material is unavailable") from exc
    if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
        raise ServerTlsError("private ca key has an unsupported type")
    if not _same_public_key(ca_key.public_key(), ca_certificate.public_key()):
        raise ServerTlsError("private ca key does not match its certificate")

    server_key = ec.generate_private_key(ec.SECP256R1())
    server_certificate = _build_server_certificate(
        config,
        ca_key=ca_key,
        ca_certificate=ca_certificate,
        server_key=server_key,
        now=current,
    )
    _write_private_key(config.private_key, server_key, overwrite=True)
    _write_certificate(config.certificate, server_certificate, overwrite=True)
    return validate_server_tls(config, now=current)


def validate_server_tls(
    config: ServerConfig,
    *,
    now: dt.datetime | None = None,
) -> ServerTlsStatus:
    """Validate certificate chain, host binding, key match, validity and key permissions."""

    if not isinstance(config, ServerConfig):
        raise TypeError("config must be ServerConfig")
    current = _utc_now() if now is None else _require_aware_utc(now)
    try:
        certificate = x509.load_pem_x509_certificate(config.certificate.read_bytes())
        ca_certificate = x509.load_pem_x509_certificate(config.ca_certificate.read_bytes())
        private_key = serialization.load_pem_private_key(config.private_key.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise ServerTlsError("tls files could not be loaded") from exc

    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ServerTlsError("server private key has an unsupported type")
    if not _same_public_key(private_key.public_key(), certificate.public_key()):
        raise ServerTlsError("server certificate does not match the private key")
    try:
        basic = ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound as exc:
        raise ServerTlsError("ca certificate is not a certificate authority") from exc
    if not basic.ca:
        raise ServerTlsError("ca certificate is not a certificate authority")
    if certificate.issuer != ca_certificate.subject:
        raise ServerTlsError("server certificate was not issued by the configured ca")
    try:
        ca_certificate.public_key().verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(certificate.signature_hash_algorithm),
        )
    except Exception as exc:  # cryptography exposes backend-specific signature errors
        raise ServerTlsError("server certificate signature is invalid") from exc

    not_before = _cert_not_before(certificate)
    expires_at = _cert_not_after(certificate)
    ca_expires = _cert_not_after(ca_certificate)
    if current < not_before or current >= expires_at:
        raise ServerTlsError("server certificate is not currently valid")
    if current >= ca_expires:
        raise ServerTlsError("ca certificate has expired")

    try:
        sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise ServerTlsError("server certificate has no subject alternative name") from exc
    if not _san_contains_host(sans, config.public_host):
        raise ServerTlsError("server certificate does not cover the configured public host")

    try:
        mode = os.stat(config.private_key).st_mode & 0o777
        ca_mode = os.stat(ca_private_key_path(config)).st_mode & 0o777
    except OSError as exc:
        raise ServerTlsError("tls private key permissions could not be checked") from exc
    if mode & 0o077 or ca_mode & 0o077:
        raise ServerTlsError("tls private keys must not be group/world accessible")

    fingerprint = ca_certificate.fingerprint(hashes.SHA256()).hex()
    days = max(0, int((expires_at - current).total_seconds() // 86400))
    return ServerTlsStatus(expires_at=expires_at, days_remaining=days, ca_sha256=fingerprint)


def _build_server_certificate(
    config: ServerConfig,
    *,
    ca_key: ec.EllipticCurvePrivateKey,
    ca_certificate: x509.Certificate,
    server_key: ec.EllipticCurvePrivateKey,
    now: dt.datetime,
) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_COMMON_NAME)])
    try:
        ip = ipaddress.ip_address(config.public_host)
    except ValueError:
        san: x509.GeneralName = x509.DNSName(config.public_host)
    else:
        san = x509.IPAddress(ip)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_certificate.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=SERVER_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _write_private_key(path: Path, key: ec.EllipticCurvePrivateKey, *, overwrite: bool) -> None:
    payload = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_file(path, payload, mode=0o600, overwrite=overwrite)


def _write_certificate(path: Path, certificate: x509.Certificate, *, overwrite: bool) -> None:
    _write_file(
        path,
        certificate.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
        overwrite=overwrite,
    )


def _write_file(path: Path, payload: bytes, *, mode: int, overwrite: bool) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not overwrite and target.exists():
        raise ServerTlsError("tls material already exists")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if not overwrite and target.exists():
            raise ServerTlsError("tls material already exists")
        os.replace(temporary, target)
        os.chmod(target, mode)
    except OSError as exc:
        raise ServerTlsError("tls material could not be saved") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _same_public_key(first: object, second: object) -> bool:
    try:
        a = first.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        b = second.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    except (AttributeError, TypeError, ValueError):
        return False
    return a == b


def _san_contains_host(sans: x509.SubjectAlternativeName, host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() in {name.lower() for name in sans.get_values_for_type(x509.DNSName)}
    return ip in set(sans.get_values_for_type(x509.IPAddress))


def _cert_not_before(certificate: x509.Certificate) -> dt.datetime:
    value = getattr(certificate, "not_valid_before_utc", None)
    if value is None:
        value = certificate.not_valid_before.replace(tzinfo=dt.timezone.utc)
    return value


def _cert_not_after(certificate: x509.Certificate) -> dt.datetime:
    value = getattr(certificate, "not_valid_after_utc", None)
    if value is None:
        value = certificate.not_valid_after.replace(tzinfo=dt.timezone.utc)
    return value


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _require_aware_utc(value: dt.datetime) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise TypeError("now must be a timezone-aware datetime")
    return value.astimezone(dt.timezone.utc)
