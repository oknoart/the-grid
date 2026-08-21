"""Local owner administration for the one personal Grid server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from .access import (
    AccessSetup,
    AccessContext,
    AccessError,
    AccessVerifierState,
    create_initial_access,
    load_access_state,
    load_server_id,
    rotate_access,
    save_access_state,
    save_initial_access,
)
from .hub import BoardStore
from .protocol import b64url_encode
from .server_config import (
    ServerConfig,
    ServerConfigError,
    default_server_config_path,
    make_server_config,
    load_server_config,
    save_server_config,
)
from .server_runtime import (
    ServerRuntime,
    ServerRuntimeError,
    admin_request,
    pid_file_process,
    server_pid_lock_held,
)
from .server_tls import (
    ServerTlsError,
    initialise_private_ca_tls,
    renew_server_certificate,
    validate_server_tls,
)

CLIENT_EXPORT_CA_NAME: Final = "okno-grid-ca.pem"
CLIENT_EXPORT_HOST_NAME: Final = "okno-grid-host.txt"
CLIENT_EXPORT_PORT_NAME: Final = "okno-grid-port.txt"
CLIENT_EXPORT_MANIFEST_NAME: Final = "okno-grid-profile.json"
BACKUP_MANIFEST_NAME: Final = "backup-manifest.json"
BACKUP_VERSION: Final = 1


class ServerAdminError(RuntimeError):
    """Raised for safe user-facing server administration failures."""


@dataclass(frozen=True, slots=True)
class InitialisedServer:
    config: ServerConfig
    phrase: str


@dataclass(frozen=True, slots=True)
class RotatedAccess:
    phrase: str
    disconnected: int
    cleared_messages: int
    cleared_cooldowns: int


def check_server_init_target(state_dir: Path) -> None:
    """Reject a state root that is unsafe to initialise into."""

    root = Path(state_dir)
    if root.is_symlink():
        raise ServerAdminError("server state directory must not be a symlink")
    if not root.exists():
        return
    if not root.is_dir():
        raise ServerAdminError("server state path is not a directory")
    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        raise ServerAdminError("server state directory could not be inspected") from exc
    if not entries:
        return
    recognised = {
        "server.json",
        "server-id.bin",
        "access-state.json",
        "grid.sqlite3",
        "grid.sqlite3-wal",
        "grid.sqlite3-shm",
        "tls",
        "admin.sock",
        "server.pid",
        "server.log",
    }
    if any(entry.name in recognised for entry in entries):
        raise ServerAdminError("server is already initialised")
    raise ServerAdminError("server state directory is not empty")


def _clean_partial_initialisation(root: Path) -> None:
    """Remove only files/directories owned by server initialisation."""

    for name in (
        "server.json",
        "server-id.bin",
        "access-state.json",
        "grid.sqlite3",
        "grid.sqlite3-wal",
        "grid.sqlite3-shm",
    ):
        try:
            (root / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    tls = root / "tls"
    try:
        shutil.rmtree(tls)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def initialise_server(
    *,
    state_dir: Path,
    public_host: str,
    public_port: int,
    listen_host: str,
    listen_port: int,
    setup: AccessSetup | None = None,
) -> InitialisedServer:
    """Initialise one Grid after the owner has confirmed the phrase is saved."""

    root = Path(state_dir).expanduser().resolve(strict=False)
    config_path = root / "server.json"
    check_server_init_target(root)

    try:
        config = make_server_config(
            root,
            public_host=public_host,
            public_port=public_port,
            listen_host=listen_host,
            listen_port=listen_port,
        )
        setup = create_initial_access() if setup is None else setup
        if not isinstance(setup, AccessSetup):
            raise TypeError("setup must be AccessSetup")
        save_server_config(config, config_path)
        initialise_private_ca_tls(config)
        save_initial_access(
            setup,
            server_id_path=config.server_id,
            access_state_path=config.access_state,
        )
        store = BoardStore(config.database, access_generation=setup.context.access_generation)
        store.close()
        os.chmod(config.database, 0o600)
    except (AccessError, ServerConfigError, ServerTlsError, OSError, ValueError) as exc:
        # Remove only deployment files this operation owns. Never recursively
        # delete an arbitrary caller-supplied state directory.
        _clean_partial_initialisation(root)
        raise ServerAdminError(str(exc) or "server initialisation failed") from exc
    return InitialisedServer(config=config, phrase=setup.phrase)


async def run_server(config_path: Path | None = None) -> None:
    config = _load(config_path)
    runtime = ServerRuntime(config)
    try:
        await runtime.start()
        await runtime.serve()
    except (AccessError, ServerTlsError, ServerRuntimeError, OSError, ValueError) as exc:
        raise ServerAdminError(str(exc) or "server could not start") from exc


async def server_status(config_path: Path | None = None) -> dict[str, object]:
    """Return live status when running, otherwise validated on-disk status."""

    config = _load(config_path)
    try:
        response = await admin_request(config, "status", timeout=0.5)
    except ServerRuntimeError as admin_error:
        try:
            if server_pid_lock_held(config):
                raise ServerAdminError(
                    "server is running but the local admin channel is unavailable"
                ) from admin_error
        except ServerRuntimeError as exc:
            raise ServerAdminError(str(exc)) from exc
        try:
            state = load_access_state(config.access_state)
            server_id = load_server_id(config.server_id)
            AccessContext(server_id=server_id, access_generation=state.access_generation)
            tls = validate_server_tls(config)
            store = BoardStore(config.database, access_generation=state.access_generation)
            try:
                messages, cooldowns = store.counts()
            finally:
                store.close()
        except (AccessError, ServerTlsError, OSError, ValueError) as exc:
            raise ServerAdminError(str(exc) or "server state is invalid") from exc
        return {
            "running": False,
            "pid": pid_file_process(config),
            "listen_host": config.listen_host,
            "listen_port": config.listen_port,
            "public_host": config.public_host,
            "public_port": config.public_port,
            "connections": 0,
            "messages": messages,
            "cooldowns": cooldowns,
            "tls_days_remaining": tls.days_remaining,
        }
    status = response.get("status")
    if not isinstance(status, dict):
        raise ServerAdminError("server returned invalid status")
    return status


async def rotate_server_access(config_path: Path | None = None) -> RotatedAccess:
    """Rotate access live when possible, or safely on disk while stopped."""

    config = _load(config_path)
    try:
        server_id = load_server_id(config.server_id)
        old_state = load_access_state(config.access_state)
        setup = rotate_access(server_id)
        if setup.verifier_state.access_generation == old_state.access_generation:
            raise ServerAdminError("rotation did not create a fresh generation")
    except (AccessError, OSError, ValueError) as exc:
        if isinstance(exc, ServerAdminError):
            raise
        raise ServerAdminError(str(exc) or "access rotation failed") from exc

    try:
        response = await admin_request(config, "rotate", state=setup.verifier_state, timeout=5.0)
    except ServerRuntimeError as admin_error:
        try:
            if server_pid_lock_held(config):
                raise ServerAdminError(
                    "server is running but the local admin channel is unavailable"
                ) from admin_error
        except ServerRuntimeError as exc:
            raise ServerAdminError(str(exc)) from exc
        try:
            store = BoardStore(config.database, access_generation=old_state.access_generation)
            before_messages, before_cooldowns = store.counts()
            save_access_state(config.access_state, setup.verifier_state, overwrite=True)
            store.bind_access_generation(setup.context.access_generation)
            store.close()
        except Exception as exc:
            raise ServerAdminError(str(exc) or "access rotation failed") from exc
        return RotatedAccess(
            phrase=setup.phrase,
            disconnected=0,
            cleared_messages=before_messages,
            cleared_cooldowns=before_cooldowns,
        )

    rotation = response.get("rotation")
    if not isinstance(rotation, dict):
        raise ServerAdminError("server returned an invalid rotation result")
    try:
        return RotatedAccess(
            phrase=setup.phrase,
            disconnected=int(rotation["disconnected"]),
            cleared_messages=int(rotation["cleared_messages"]),
            cleared_cooldowns=int(rotation["cleared_cooldowns"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ServerAdminError("server returned an invalid rotation result") from exc


def renew_server_tls(config_path: Path | None = None) -> int:
    config = _load(config_path)
    try:
        status = renew_server_certificate(config)
    except ServerTlsError as exc:
        raise ServerAdminError(str(exc)) from exc
    return status.days_remaining


def export_client_profile(
    output_dir: Path,
    config_path: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    """Export only the public client deployment material needed by release packaging."""

    config = _load(config_path)
    try:
        tls = validate_server_tls(config)
    except ServerTlsError as exc:
        raise ServerAdminError(str(exc)) from exc
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    host_path = target / CLIENT_EXPORT_HOST_NAME
    port_path = target / CLIENT_EXPORT_PORT_NAME
    ca_path = target / CLIENT_EXPORT_CA_NAME
    manifest_path = target / CLIENT_EXPORT_MANIFEST_NAME
    host_path.write_text(config.public_host + "\n", encoding="utf-8")
    port_path.write_text(str(config.public_port) + "\n", encoding="ascii")
    shutil.copyfile(config.ca_certificate, ca_path)
    manifest = {
        "v": 1,
        "host": config.public_host,
        "port": config.public_port,
        "ca_sha256": tls.ca_sha256,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(host_path, 0o644)
    os.chmod(port_path, 0o644)
    os.chmod(ca_path, 0o644)
    os.chmod(manifest_path, 0o644)
    return host_path, port_path, ca_path, manifest_path


def backup_server(
    output_file: Path,
    config_path: Path | None = None,
) -> Path:
    """Create one owner-only operational backup without copying runtime/log files."""

    config = _load(config_path)
    try:
        state = load_access_state(config.access_state)
        server_id = load_server_id(config.server_id)
        AccessContext(server_id=server_id, access_generation=state.access_generation)
        validate_server_tls(config)
    except (AccessError, ServerTlsError, OSError, ValueError) as exc:
        raise ServerAdminError(str(exc) or "server state is invalid") from exc

    target = Path(output_file)
    if target.exists():
        raise ServerAdminError("backup output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="okno-backup-") as temporary_directory:
        temporary = Path(temporary_directory)
        database_copy = temporary / "grid.sqlite3"
        try:
            source = sqlite3.connect(config.database)
            destination = sqlite3.connect(database_copy)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        except sqlite3.Error as exc:
            raise ServerAdminError("server database could not be backed up") from exc

        files = {
            "server.json": (config_path if config_path is not None else default_server_config_path()),
            "server-id.bin": config.server_id,
            "access-state.json": config.access_state,
            "grid.sqlite3": database_copy,
            "tls/grid-ca.pem": config.ca_certificate,
            "tls/grid-ca-key.pem": config.ca_certificate.with_name("grid-ca-key.pem"),
            "tls/server-cert.pem": config.certificate,
            "tls/server-key.pem": config.private_key,
        }
        for logical, source_path in files.items():
            if not Path(source_path).is_file():
                raise ServerAdminError(f"backup source is missing: {logical}")

        manifest = {
            "v": BACKUP_VERSION,
            "created_at": int(time.time()),
            "public_host": config.public_host,
            "public_port": config.public_port,
            "access_generation": b64url_encode(state.access_generation),
            "files": sorted(files),
        }
        manifest_path = temporary / BACKUP_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            with tarfile.open(target, "w:gz") as archive:
                archive.add(manifest_path, arcname=BACKUP_MANIFEST_NAME, recursive=False)
                for logical, source_path in files.items():
                    archive.add(Path(source_path), arcname=logical, recursive=False)
            os.chmod(target, 0o600)
        except (OSError, tarfile.TarError) as exc:
            try:
                target.unlink()
            except OSError:
                pass
            raise ServerAdminError("server backup could not be written") from exc
    return target


def format_server_status(status: dict[str, object]) -> str:
    running = bool(status.get("running"))
    lines = [
        f"server: {'running' if running else 'stopped'}",
        f"public: {status.get('public_host')}:{status.get('public_port')}",
        f"listen: {status.get('listen_host')}:{status.get('listen_port')}",
        f"tls: valid ({status.get('tls_days_remaining')} days remaining)",
        f"connections: {status.get('connections', 0)}",
        f"hub messages: {status.get('messages', 0)}",
        f"cooldowns: {status.get('cooldowns', 0)}",
    ]
    return "\n".join(lines) + "\n"


def _load(config_path: Path | None) -> ServerConfig:
    try:
        return load_server_config(default_server_config_path() if config_path is None else config_path)
    except ServerConfigError as exc:
        raise ServerAdminError(str(exc)) from exc
