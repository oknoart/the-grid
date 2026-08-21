"""Strict server deployment configuration for one personal Grid."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

from .models import DEFAULT_SERVER_PORT
from .protocol import MAX_OUTER_FRAME_BYTES
from .relay import RelayLimits
from .terms import APP_SLUG

SERVER_CONFIG_VERSION: Final = 1
SERVER_CONFIG_FILENAME: Final = "server.json"
SERVER_DIRNAME: Final = "server"


class ServerConfigError(ValueError):
    """Raised when deployment configuration is absent or invalid."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Validated server paths and network settings."""

    listen_host: str
    listen_port: int
    public_host: str
    public_port: int
    certificate: Path
    private_key: Path
    ca_certificate: Path
    database: Path
    server_id: Path
    access_state: Path
    admin_socket: Path
    pid_file: Path
    log_file: Path
    max_connections: int = RelayLimits().max_connections
    max_frame_bytes: int = MAX_OUTER_FRAME_BYTES
    version: int = SERVER_CONFIG_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SERVER_CONFIG_VERSION:
            raise ServerConfigError("unsupported server configuration version")
        for name in ("listen_host", "public_host"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ServerConfigError(f"{name} must be a non-empty trimmed string")
            _validate_host(name, value)
        for name in ("listen_port", "public_port"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 65535:
                raise ServerConfigError(f"{name} must be between 1 and 65535")
        for name in (
            "certificate",
            "private_key",
            "ca_certificate",
            "database",
            "server_id",
            "access_state",
            "admin_socket",
            "pid_file",
            "log_file",
        ):
            value = getattr(self, name)
            if not isinstance(value, Path):
                raise ServerConfigError(f"{name} must be a path")
        if type(self.max_connections) is not int or self.max_connections < 1:
            raise ServerConfigError("max_connections must be positive")
        if type(self.max_frame_bytes) is not int or self.max_frame_bytes != MAX_OUTER_FRAME_BYTES:
            raise ServerConfigError("max_frame_bytes must match protocol v1")

    @property
    def limits(self) -> RelayLimits:
        return RelayLimits(
            max_connections=self.max_connections,
            max_frame_bytes=self.max_frame_bytes,
        )


def _validate_host(name: str, value: str) -> None:
    if any(char.isspace() for char in value) or "\x00" in value:
        raise ServerConfigError(f"{name} contains invalid characters")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ServerConfigError(f"{name} must be an IP address or ASCII hostname") from exc
        if len(value) > 253 or value.startswith(".") or value.endswith("."):
            raise ServerConfigError(f"{name} must be an IP address or DNS hostname")
        labels = value.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (char.isalnum() or char == "-") for char in label)
            for label in labels
        ):
            raise ServerConfigError(f"{name} must be an IP address or DNS hostname")


def default_server_state_dir(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default per-owner server state directory."""

    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)

    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APP_SLUG / SERVER_DIRNAME
    if platform_name.startswith("linux"):
        xdg_home = environ.get("XDG_STATE_HOME", "").strip()
        base = Path(xdg_home).expanduser() if xdg_home else home / ".local" / "state"
        return base / APP_SLUG / SERVER_DIRNAME
    raise ServerConfigError("server deployment is supported on POSIX macOS/Linux hosts")


def default_server_config_path(**kwargs: Any) -> Path:
    return default_server_state_dir(**kwargs) / SERVER_CONFIG_FILENAME


def make_server_config(
    state_dir: Path,
    *,
    public_host: str,
    public_port: int = DEFAULT_SERVER_PORT,
    listen_host: str = "0.0.0.0",
    listen_port: int = DEFAULT_SERVER_PORT,
) -> ServerConfig:
    """Build the canonical one-Grid server configuration under one state root."""

    root = Path(state_dir)
    tls = root / "tls"
    return ServerConfig(
        listen_host=listen_host,
        listen_port=listen_port,
        public_host=public_host,
        public_port=public_port,
        certificate=tls / "server-cert.pem",
        private_key=tls / "server-key.pem",
        ca_certificate=tls / "grid-ca.pem",
        database=root / "grid.sqlite3",
        server_id=root / "server-id.bin",
        access_state=root / "access-state.json",
        admin_socket=root / "admin.sock",
        pid_file=root / "server.pid",
        log_file=root / "server.log",
    )


def load_server_config(path: Path | None = None) -> ServerConfig:
    target = default_server_config_path() if path is None else Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ServerConfigError("server is not initialised") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServerConfigError("server configuration is invalid") from exc
    return server_config_from_mapping(raw)


def server_config_from_mapping(raw: object) -> ServerConfig:
    if not isinstance(raw, dict):
        raise ServerConfigError("server configuration is invalid")
    _reject_unknown(raw, {"v", "listen", "public", "tls", "storage", "runtime", "limits"})
    if raw.get("v") != SERVER_CONFIG_VERSION:
        raise ServerConfigError("unsupported server configuration version")

    listen = _mapping(raw, "listen")
    public = _mapping(raw, "public")
    tls = _mapping(raw, "tls")
    storage = _mapping(raw, "storage")
    runtime = _mapping(raw, "runtime")
    limits = _mapping(raw, "limits")

    _reject_unknown(listen, {"host", "port"})
    _reject_unknown(public, {"host", "port"})
    _reject_unknown(tls, {"certificate", "private_key", "ca_certificate"})
    _reject_unknown(storage, {"database", "server_id", "access_state"})
    _reject_unknown(runtime, {"admin_socket", "pid_file", "log_file"})
    _reject_unknown(limits, {"max_connections", "max_frame_bytes"})

    try:
        return ServerConfig(
            listen_host=_string(listen, "host"),
            listen_port=_integer(listen, "port"),
            public_host=_string(public, "host"),
            public_port=_integer(public, "port"),
            certificate=Path(_string(tls, "certificate")),
            private_key=Path(_string(tls, "private_key")),
            ca_certificate=Path(_string(tls, "ca_certificate")),
            database=Path(_string(storage, "database")),
            server_id=Path(_string(storage, "server_id")),
            access_state=Path(_string(storage, "access_state")),
            admin_socket=Path(_string(runtime, "admin_socket")),
            pid_file=Path(_string(runtime, "pid_file")),
            log_file=Path(_string(runtime, "log_file")),
            max_connections=_integer(limits, "max_connections"),
            max_frame_bytes=_integer(limits, "max_frame_bytes"),
        )
    except (TypeError, ValueError, ServerConfigError) as exc:
        if isinstance(exc, ServerConfigError):
            raise
        raise ServerConfigError("server configuration is invalid") from exc


def server_config_to_mapping(config: ServerConfig) -> dict[str, object]:
    if not isinstance(config, ServerConfig):
        raise TypeError("config must be ServerConfig")
    return {
        "v": config.version,
        "listen": {"host": config.listen_host, "port": config.listen_port},
        "public": {"host": config.public_host, "port": config.public_port},
        "tls": {
            "certificate": str(config.certificate),
            "private_key": str(config.private_key),
            "ca_certificate": str(config.ca_certificate),
        },
        "storage": {
            "database": str(config.database),
            "server_id": str(config.server_id),
            "access_state": str(config.access_state),
        },
        "runtime": {
            "admin_socket": str(config.admin_socket),
            "pid_file": str(config.pid_file),
            "log_file": str(config.log_file),
        },
        "limits": {
            "max_connections": config.max_connections,
            "max_frame_bytes": config.max_frame_bytes,
        },
    }


def format_server_config(config: ServerConfig) -> str:
    return json.dumps(
        server_config_to_mapping(config),
        ensure_ascii=True,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def save_server_config(
    config: ServerConfig,
    path: Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    target = default_server_config_path() if path is None else Path(path)
    payload = format_server_config(config)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(target.parent, 0o700)
    if target.exists() and not overwrite:
        raise ServerConfigError("server is already initialised")

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        if target.exists() and not overwrite:
            raise ServerConfigError("server is already initialised")
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except OSError as exc:
        raise ServerConfigError("server configuration could not be saved") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ServerConfigError("server configuration is invalid")
    return value


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ServerConfigError("server configuration is invalid")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if type(value) is not int:
        raise ServerConfigError("server configuration is invalid")
    return value


def _reject_unknown(raw: Mapping[str, object], allowed: set[str]) -> None:
    if any(key not in allowed for key in raw):
        raise ServerConfigError("server configuration contains an unknown setting")
