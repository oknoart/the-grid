"""Production server runtime, local admin socket, PID lock, and metadata logging."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import logging.handlers
import os
import signal
from pathlib import Path
from typing import Any, Final, Mapping

from .access import AccessContext, AccessVerifierState, load_access_state, load_server_id, save_access_state
from .protocol import b64url_decode, b64url_encode
from .relay import RelayServer, create_server_ssl_context
from .server_config import ServerConfig
from .server_tls import ServerTlsStatus, validate_server_tls

ADMIN_MAX_BYTES: Final = 8192
ADMIN_PROTOCOL_VERSION: Final = 1


class ServerRuntimeError(RuntimeError):
    """Raised when the production server runtime cannot safely operate."""


class ServerRuntime:
    """One foreground server process intended to be supervised by launchd."""

    def __init__(self, config: ServerConfig) -> None:
        if not isinstance(config, ServerConfig):
            raise TypeError("config must be ServerConfig")
        self.config = config
        self.relay: RelayServer | None = None
        self.tls_status: ServerTlsStatus | None = None
        self._admin_server: asyncio.AbstractServer | None = None
        self._pid_fd: int | None = None
        self._stop = asyncio.Event()
        self._logger: logging.Logger | None = None
        self._closed = False

    async def start(self) -> "ServerRuntime":
        if self.relay is not None:
            raise ServerRuntimeError("server is already started")
        self._prepare_runtime_paths()
        self._acquire_pid_lock()
        self._logger = _server_logger(self.config)
        try:
            server_id = load_server_id(self.config.server_id)
            state = load_access_state(self.config.access_state)
            context = AccessContext(server_id=server_id, access_generation=state.access_generation)
            self.tls_status = validate_server_tls(self.config)
            ssl_context = create_server_ssl_context(self.config.certificate, self.config.private_key)
            relay = RelayServer(
                context=context,
                verifier_state=state,
                database=self.config.database,
                host=self.config.listen_host,
                port=self.config.listen_port,
                ssl_context=ssl_context,
                limits=self.config.limits,
            )
            await relay.start()
            self.relay = relay
            await self._start_admin_socket()
            os.chmod(self.config.database, 0o600)
            self._log(
                "server started listen=%s:%s public=%s:%s",
                self.config.listen_host,
                self.config.listen_port,
                self.config.public_host,
                self.config.public_port,
            )
            return self
        except Exception:
            await self.close()
            raise

    async def serve(self) -> None:
        if self.relay is None:
            await self.start()
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stop.set)
            except (NotImplementedError, RuntimeError):
                continue
            installed.append(signum)
        try:
            await self._stop.wait()
        finally:
            for signum in installed:
                try:
                    loop.remove_signal_handler(signum)
                except (NotImplementedError, RuntimeError):
                    pass
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._admin_server is not None:
            self._admin_server.close()
            await self._admin_server.wait_closed()
            self._admin_server = None
        if self.relay is not None:
            await self.relay.close()
            self.relay = None
        try:
            self.config.admin_socket.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self._log("server stopped")
        _close_logger(self._logger)
        self._logger = None
        self._release_pid_lock()

    def status(self) -> dict[str, object]:
        if self.relay is None:
            raise ServerRuntimeError("server is not running")
        messages, cooldowns = self.relay.board.counts()
        return {
            "running": True,
            "pid": os.getpid(),
            "listen_host": self.config.listen_host,
            "listen_port": self.config.listen_port,
            "public_host": self.config.public_host,
            "public_port": self.config.public_port,
            "connections": self.relay.connection_count,
            "messages": messages,
            "cooldowns": cooldowns,
            "tls_days_remaining": None if self.tls_status is None else self.tls_status.days_remaining,
        }

    async def apply_access_rotation(self, state: AccessVerifierState) -> dict[str, int]:
        if self.relay is None:
            raise ServerRuntimeError("server is not running")
        if not isinstance(state, AccessVerifierState):
            raise TypeError("state must be AccessVerifierState")
        server_id = load_server_id(self.config.server_id)
        if state.access_generation == self.relay.context.access_generation:
            raise ServerRuntimeError("rotation requires a fresh access generation")

        before_messages, before_cooldowns = self.relay.board.counts()
        disconnected = self.relay.connection_count
        # Persist first. If the process dies after this point, startup binds the
        # database to the new generation and clears any old-generation state.
        save_access_state(self.config.access_state, state, overwrite=True)
        context = AccessContext(server_id=server_id, access_generation=state.access_generation)
        try:
            await self.relay.rotate_access(context, state)
        except Exception:
            self._stop.set()
            raise
        self._log(
            "access rotated disconnected=%s cleared_messages=%s cleared_cooldowns=%s",
            disconnected,
            before_messages,
            before_cooldowns,
        )
        return {
            "disconnected": disconnected,
            "cleared_messages": before_messages,
            "cleared_cooldowns": before_cooldowns,
        }

    async def _start_admin_socket(self) -> None:
        try:
            self.config.admin_socket.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ServerRuntimeError("admin socket could not be prepared") from exc
        self._admin_server = await asyncio.start_unix_server(
            self._handle_admin,
            path=str(self.config.admin_socket),
            limit=ADMIN_MAX_BYTES,
        )
        os.chmod(self.config.admin_socket, 0o600)

    async def _handle_admin(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            encoded = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not encoded or len(encoded) > ADMIN_MAX_BYTES or not encoded.endswith(b"\n"):
                raise ServerRuntimeError("invalid admin request")
            request = json.loads(encoded.decode("utf-8"))
            if not isinstance(request, dict) or request.get("v") != ADMIN_PROTOCOL_VERSION:
                raise ServerRuntimeError("invalid admin request")
            action = request.get("action")
            if action == "status":
                response: dict[str, object] = {"ok": True, "status": self.status()}
            elif action == "rotate":
                encoded_state = request.get("state")
                if not isinstance(encoded_state, str):
                    raise ServerRuntimeError("invalid rotation request")
                state = AccessVerifierState.from_bytes(b64url_decode(encoded_state))
                result = await self.apply_access_rotation(state)
                response = {"ok": True, "rotation": result}
            else:
                raise ServerRuntimeError("unsupported admin action")
        except Exception as exc:
            response = {"ok": False, "error": str(exc) or "admin request failed"}
        try:
            writer.write(_admin_encode(response))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def _prepare_runtime_paths(self) -> None:
        root = self.config.pid_file.parent
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        self.config.log_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _acquire_pid_lock(self) -> None:
        try:
            fd = os.open(self.config.pid_file, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
            os.chmod(self.config.pid_file, 0o600)
        except BlockingIOError as exc:
            try:
                os.close(fd)
            except (UnboundLocalError, OSError):
                pass
            raise ServerRuntimeError("server is already running") from exc
        except OSError as exc:
            try:
                os.close(fd)
            except (UnboundLocalError, OSError):
                pass
            raise ServerRuntimeError("server pid lock could not be created") from exc
        self._pid_fd = fd

    def _release_pid_lock(self) -> None:
        if self._pid_fd is None:
            return
        try:
            fcntl.flock(self._pid_fd, fcntl.LOCK_UN)
            os.close(self._pid_fd)
        except OSError:
            pass
        self._pid_fd = None
        try:
            self.config.pid_file.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _log(self, message: str, *args: object) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)


async def admin_request(
    config: ServerConfig,
    action: str,
    *,
    state: AccessVerifierState | None = None,
    timeout: float = 5.0,
) -> Mapping[str, object]:
    """Send one local owner-only admin request to a running server."""

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(config.admin_socket)),
            timeout=timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ServerRuntimeError("server is not running") from exc
    request: dict[str, object] = {"v": ADMIN_PROTOCOL_VERSION, "action": action}
    if state is not None:
        request["state"] = b64url_encode(state.to_bytes())
    try:
        writer.write(_admin_encode(request))
        await writer.drain()
        encoded = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not encoded or len(encoded) > ADMIN_MAX_BYTES:
            raise ServerRuntimeError("invalid admin response")
        response = json.loads(encoded.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = response.get("error") if isinstance(response, dict) else None
            raise ServerRuntimeError(message if isinstance(message, str) else "admin request failed")
        return response
    except (OSError, UnicodeError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
        if isinstance(exc, ServerRuntimeError):
            raise
        raise ServerRuntimeError("admin request failed") from exc
    finally:
        writer.close()
        await writer.wait_closed()


def server_pid_lock_held(config: ServerConfig) -> bool:
    """Return whether another process currently holds the server PID lock."""

    try:
        fd = os.open(config.pid_file, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ServerRuntimeError("server pid lock could not be inspected") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def pid_file_process(config: ServerConfig) -> int | None:
    """Return a live PID from the runtime file, or none for stale/absent state."""

    try:
        text = config.pid_file.read_text(encoding="ascii").strip()
        pid = int(text, 10)
        if pid <= 0:
            return None
        os.kill(pid, 0)
    except (OSError, UnicodeError, ValueError):
        return None
    return pid


def _admin_encode(value: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(encoded) > ADMIN_MAX_BYTES:
        raise ServerRuntimeError("admin frame is too large")
    return encoded


def _server_logger(config: ServerConfig) -> logging.Logger:
    logger = logging.getLogger(f"okno.server.{id(config)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    config.log_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        config.log_file,
        maxBytes=1_048_576,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    os.chmod(config.log_file, 0o600)
    return logger


def _close_logger(logger: logging.Logger | None) -> None:
    if logger is None:
        return
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
