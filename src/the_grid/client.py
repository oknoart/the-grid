"""Headless protocol-v1 client used before the terminal interface is added."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import ssl
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from .access import (
    AccessContext,
    AccessKeys,
    create_access_proof,
    derive_access_keys,
    derive_display_token,
    normalise_display_id,
)
from .hub import (
    BOARD_COOLDOWN_SECONDS,
    BoardCryptoError,
    BoardMessage,
    EncryptedBoardRecord,
    StoredBoardRecord,
    decrypt_board_record,
    encrypt_board_message,
)
from .models import CloseReason
from .phrases import generate_phrase
from .protocol import (
    ACCESS_CHALLENGE_BYTES,
    ACCESS_GENERATION_BYTES,
    BOARD_MESSAGE_ID_BYTES,
    DISPLAY_TOKEN_BYTES,
    HANDSHAKE_NONCE_BYTES,
    HMAC_BYTES,
    MAX_OUTER_FRAME_BYTES,
    PAIR_ID_BYTES,
    PROTOCOL_VERSION,
    ROOM_ID_BYTES,
    SERVER_ID_BYTES,
    SESSION_ID_BYTES,
    X25519_PUBLIC_BYTES,
    FrameError,
    FrameErrorCode,
    b64url_encode,
    make_frame,
    read_outer_frame,
    require_frame_bool,
    require_frame_bytes,
    require_frame_int,
    require_frame_string,
    write_outer_frame,
)
from .sessions import (
    EncryptedSessionFrame,
    HandshakeHello,
    HandshakeParticipant,
    LiveSessionChannel,
    SessionDirection,
    SessionEvent,
    SessionEventType,
    SessionIntegrityError,
    SessionPhraseMaterial,
    SessionRole,
    derive_session_phrase_material,
)

DEFAULT_CLIENT_REQUEST_TIMEOUT: Final = 30.0
MAX_BUFFERED_SESSION_FRAMES: Final = 16


class ClientErrorCode(StrEnum):
    INVALID_STATE = "invalid_state"
    CONNECTION = "connection_error"
    ACCESS = "access_denied"
    DISPLAY_UNAVAILABLE = "display_unavailable"
    BOARD = "board_error"
    SESSION_UNAVAILABLE = "session_unavailable"
    SESSION = "session_error"
    PROTOCOL = "protocol_error"
    TIMEOUT = "timeout"


class ClientError(RuntimeError):
    def __init__(
        self,
        code: ClientErrorCode,
        message: str | None = None,
        *,
        retry_after: int = 0,
    ) -> None:
        self.code = code
        self.retry_after = retry_after
        super().__init__(code.value if message is None else message)


class RemoteProtocolError(ClientError):
    def __init__(self, remote_code: str) -> None:
        self.remote_code = remote_code
        super().__init__(ClientErrorCode.PROTOCOL, remote_code)


@dataclass(frozen=True, slots=True)
class BoardViewRecord:
    stored: StoredBoardRecord
    message: BoardMessage


@dataclass(frozen=True, slots=True)
class BoardClientEvent:
    kind: str
    sequence: int
    message_ids: tuple[bytes, ...] = field(default_factory=tuple)
    record: BoardViewRecord | None = None


@dataclass(frozen=True, slots=True)
class BoardPostOutcome:
    accepted: bool
    remaining_seconds: int = 0
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SessionPair:
    pair_id: bytes
    role: SessionRole
    peer_hello: HandshakeHello


@dataclass(frozen=True, slots=True)
class SessionClosed:
    reason: str


class HeadlessClient:
    """Async headless client proving the complete Phase 3 protocol flow."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext | None = None,
        allow_plain: bool = False,
        client_version: str = "0.4.0",
        request_timeout: float = DEFAULT_CLIENT_REQUEST_TIMEOUT,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("host must be non-empty")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if ssl_context is None:
            if not allow_plain:
                raise ClientError(
                    ClientErrorCode.CONNECTION,
                    "TLS is required unless explicit loopback plain mode is enabled",
                )
            if not _is_loopback_host(host):
                raise ClientError(
                    ClientErrorCode.CONNECTION,
                    "plain transport is restricted to loopback development",
                )
        elif allow_plain:
            raise ValueError("allow_plain must not be combined with TLS")
        if not isinstance(client_version, str) or not client_version or len(client_version) > 64:
            raise ValueError("client_version is invalid")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")

        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.allow_plain = allow_plain
        self.client_version = client_version
        self.request_timeout = float(request_timeout)

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._board_sync_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Mapping[str, object]]] = {}
        self._closed = True
        self._connection_closed = asyncio.Event()
        self._connection_closed.set()
        self._max_frame_bytes = MAX_OUTER_FRAME_BYTES
        self._heartbeat_interval = 30.0
        self._dead_timeout = 90.0
        self.server_limits: dict[str, object] = {}
        self.server_time: int | None = None

        self.context: AccessContext | None = None
        self._access_challenge: bytes | None = None
        self.access_keys: AccessKeys | None = None
        self.display_id: str | None = None
        self.display_token: bytes | None = None
        self._post_available_at = 0.0

        self._board_sequence = 0
        self._board_records: dict[bytes, BoardViewRecord] = {}
        self.board_events: asyncio.Queue[BoardClientEvent] = asyncio.Queue()
        self.board_warnings: asyncio.Queue[str] = asyncio.Queue()
        self._board_resync_task: asyncio.Task[None] | None = None

        self._session_role: SessionRole | None = None
        self._session_phrase_material: SessionPhraseMaterial | None = None
        self._handshake_participant: HandshakeParticipant | None = None
        self._pair_id: bytes | None = None
        self._pair_queue: asyncio.Queue[SessionPair] = asyncio.Queue()
        self._proof_queue: asyncio.Queue[tuple[bytes, SessionRole, bytes]] = asyncio.Queue()
        self._buffered_session_frames: deque[EncryptedSessionFrame] = deque()
        self._session_channel: LiveSessionChannel | None = None
        self.session_events: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self.session_closed_events: asyncio.Queue[SessionClosed] = asyncio.Queue()
        self._peer_identity_ready = asyncio.Event()

    @property
    def connected(self) -> bool:
        return not self._closed and self.writer is not None

    @property
    def board_sequence(self) -> int:
        return self._board_sequence

    @property
    def board_records(self) -> tuple[BoardViewRecord, ...]:
        return tuple(
            sorted(
                self._board_records.values(),
                key=lambda item: (
                    item.stored.created_at,
                    item.stored.record.message_id,
                ),
            )
        )

    @property
    def post_remaining_seconds(self) -> int:
        return max(0, int(self._post_available_at - time.monotonic() + 0.999))

    @property
    def session_channel(self) -> LiveSessionChannel | None:
        return self._session_channel

    @property
    def pair_id(self) -> bytes | None:
        return self._pair_id

    async def connect(self) -> None:
        if self.connected:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host,
                self.port,
                ssl=self.ssl_context,
                server_hostname=(self.host if self.ssl_context is not None else None),
                limit=MAX_OUTER_FRAME_BYTES,
            )
        except (OSError, ssl.SSLError) as exc:
            raise ClientError(ClientErrorCode.CONNECTION) from exc
        self._closed = False
        self._connection_closed.clear()
        self._reader_task = asyncio.create_task(self._reader_loop(), name="grid-client-reader")
        try:
            response = await self._request(
                "hello",
                client_version=self.client_version,
                capabilities=["board_live_v1", "session_route_v1"],
            )
            if response.get("type") != "hello":
                raise ClientError(ClientErrorCode.PROTOCOL)
            server_id = require_frame_bytes(
                response, "server_id", expected_length=SERVER_ID_BYTES
            )
            generation = require_frame_bytes(
                response,
                "access_generation",
                expected_length=ACCESS_GENERATION_BYTES,
            )
            self.context = AccessContext(server_id, generation)
            self._access_challenge = require_frame_bytes(
                response,
                "access_challenge",
                expected_length=ACCESS_CHALLENGE_BYTES,
            )
            self.server_time = require_frame_int(response, "server_time")
            limits = response.get("limits")
            if not isinstance(limits, dict):
                raise FrameError(FrameErrorCode.INVALID)
            self.server_limits = dict(limits)
            max_frame = limits.get("max_frame_bytes")
            if type(max_frame) is not int or not 128 <= max_frame <= MAX_OUTER_FRAME_BYTES:
                raise FrameError(FrameErrorCode.INVALID)
            self._max_frame_bytes = max_frame
            heartbeat = response.get("heartbeat_interval")
            dead = limits.get("dead_timeout_seconds")
            if (
                isinstance(heartbeat, bool)
                or not isinstance(heartbeat, (int, float))
                or heartbeat <= 0
                or isinstance(dead, bool)
                or not isinstance(dead, (int, float))
                or dead <= heartbeat
            ):
                raise FrameError(FrameErrorCode.INVALID)
            self._heartbeat_interval = float(heartbeat)
            self._dead_timeout = float(dead)
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="grid-client-heartbeat"
            )
        except Exception:
            await self.close()
            raise

    async def authenticate(self, phrase: str) -> None:
        if self.context is None or self._access_challenge is None or not self.connected:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        keys = await asyncio.to_thread(derive_access_keys, phrase, self.context)
        client_nonce = secrets.token_bytes(16)
        proof = create_access_proof(
            keys.authentication_key,
            self.context,
            challenge=self._access_challenge,
            client_nonce=client_nonce,
        )
        response = await self._request(
            "access_proof",
            client_nonce=b64url_encode(client_nonce),
            proof=b64url_encode(proof),
        )
        ok = require_frame_bool(response, "ok")
        retry = require_frame_int(response, "retry_after", maximum=86_400)
        if not ok:
            raise ClientError(ClientErrorCode.ACCESS, retry_after=retry)
        self.access_keys = keys
        self._access_challenge = None

    async def reserve_display(self, display_id: str) -> None:
        if self.context is None or self.access_keys is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        checked = normalise_display_id(display_id)
        token = derive_display_token(self.access_keys.display_token_key, checked)
        response = await self._request(
            "display_reserve",
            display_token=b64url_encode(token),
        )
        if not require_frame_bool(response, "ok"):
            raise ClientError(ClientErrorCode.DISPLAY_UNAVAILABLE)
        remaining = require_frame_int(
            response, "post_remaining", maximum=BOARD_COOLDOWN_SECONDS
        )
        self.display_id = checked
        self.display_token = token
        self._post_available_at = time.monotonic() + remaining
        await self.synchronise_board()

    async def connect_ready(self, phrase: str, display_id: str) -> None:
        await self.connect()
        await self.authenticate(phrase)
        await self.reserve_display(display_id)

    async def synchronise_board(self) -> None:
        if self.display_token is None or self.access_keys is None or self.context is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        async with self._board_sync_lock:
            for _ in range(5):
                records: dict[bytes, BoardViewRecord] = {}
                sequence: int | None = None
                offset = 0
                restart = False
                while True:
                    fields: dict[str, object] = {"offset": offset}
                    if sequence is not None:
                        fields["snapshot_sequence"] = sequence
                    response = await self._request("board_list", **fields)
                    current_sequence = require_frame_int(response, "sequence")
                    if sequence is None:
                        sequence = current_sequence
                    elif current_sequence != sequence:
                        restart = True
                        break
                    restart_value = response.get("restart")
                    if not isinstance(restart_value, bool):
                        raise ClientError(ClientErrorCode.PROTOCOL)
                    if restart_value:
                        restart = True
                        break
                    returned_offset = require_frame_int(
                        response, "offset", maximum=24
                    )
                    next_offset = require_frame_int(
                        response, "next_offset", maximum=24
                    )
                    done = require_frame_bool(response, "done")
                    if returned_offset != offset or next_offset < offset:
                        raise ClientError(ClientErrorCode.PROTOCOL)
                    raw_records = response.get("records")
                    if not isinstance(raw_records, list) or len(raw_records) > 24:
                        raise ClientError(ClientErrorCode.PROTOCOL)
                    if next_offset - offset != len(raw_records):
                        raise ClientError(ClientErrorCode.PROTOCOL)
                    for raw in raw_records:
                        if not isinstance(raw, dict):
                            raise ClientError(ClientErrorCode.PROTOCOL)
                        view = self._decode_board_view(raw)
                        if view is not None:
                            records[view.stored.record.message_id] = view
                    offset = next_offset
                    if done:
                        break
                    if not raw_records:
                        raise ClientError(ClientErrorCode.PROTOCOL)
                if restart or sequence is None:
                    continue
                subscribe = await self._request(
                    "board_subscribe",
                    after_sequence=sequence,
                )
                current = require_frame_int(subscribe, "sequence")
                if require_frame_bool(subscribe, "ok") and current == sequence:
                    self._board_records = records
                    self._board_sequence = sequence
                    return
            raise ClientError(ClientErrorCode.BOARD, "board could not be synchronised")

    async def post_board(self, text: str) -> BoardPostOutcome:
        if (
            self.display_id is None
            or self.display_token is None
            or self.access_keys is None
            or self.context is None
        ):
            raise ClientError(ClientErrorCode.INVALID_STATE)
        record = encrypt_board_message(
            BoardMessage(self.display_id, text),
            self.context,
            self.access_keys,
        )
        response = await self._request(
            "board_post",
            message_id=b64url_encode(record.message_id),
            display_token=b64url_encode(record.display_token),
            ciphertext=b64url_encode(record.ciphertext),
        )
        ok = require_frame_bool(response, "ok")
        remaining = require_frame_int(response, "remaining", maximum=BOARD_COOLDOWN_SECONDS)
        reason = response.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.isascii()):
            raise ClientError(ClientErrorCode.PROTOCOL)
        self._post_available_at = time.monotonic() + remaining
        return BoardPostOutcome(ok, remaining, reason)

    async def start_session(self) -> str:
        self._require_ready_for_session()
        phrase = generate_phrase()
        material = await asyncio.to_thread(
            derive_session_phrase_material,
            phrase,
            server_id=self._require_context().server_id,
        )
        participant = HandshakeParticipant.generate(SessionRole.CREATOR)
        self._prepare_session_state(SessionRole.CREATOR, material, participant)
        response = await self._request(
            "session_wait",
            room_id=b64url_encode(material.room_id),
            role=SessionRole.CREATOR.value,
            handshake_nonce=b64url_encode(participant.hello.nonce),
            public_key=b64url_encode(participant.hello.public_key),
        )
        if not require_frame_bool(response, "ok"):
            self._discard_session_state()
            raise ClientError(ClientErrorCode.SESSION_UNAVAILABLE)
        return phrase

    async def cancel_waiting_session(self) -> bool:
        """Cancel this client's unpaired creator waiting room, if still waiting.

        A false result means pairing won the race; callers should continue the
        existing session handshake rather than reporting a successful cancel.
        """

        if self._session_role is not SessionRole.CREATOR or self._pair_id is not None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        response = await self._request("session_cancel")
        cancelled = require_frame_bool(response, "ok")
        if cancelled:
            self._discard_session_state()
        return cancelled

    async def join_session(self, phrase: str) -> None:
        self._require_ready_for_session()
        material = await asyncio.to_thread(
            derive_session_phrase_material,
            phrase,
            server_id=self._require_context().server_id,
        )
        participant = HandshakeParticipant.generate(SessionRole.JOINER)
        self._prepare_session_state(SessionRole.JOINER, material, participant)
        response = await self._request(
            "session_join",
            room_id=b64url_encode(material.room_id),
            role=SessionRole.JOINER.value,
            handshake_nonce=b64url_encode(participant.hello.nonce),
            public_key=b64url_encode(participant.hello.public_key),
        )
        if not require_frame_bool(response, "ok"):
            self._discard_session_state()
            raise ClientError(ClientErrorCode.SESSION_UNAVAILABLE)

    async def complete_session(self) -> LiveSessionChannel:
        if (
            self._session_role is None
            or self._session_phrase_material is None
            or self._handshake_participant is None
            or self.display_id is None
        ):
            raise ClientError(ClientErrorCode.INVALID_STATE)
        handshake_timeout = float(
            self.server_limits.get("session_handshake_timeout_seconds", 30)
        )
        pair_timeout = (
            float(self.server_limits.get("session_wait_timeout_seconds", 900))
            if self._session_role is SessionRole.CREATOR
            else handshake_timeout
        )
        try:
            pair = await asyncio.wait_for(self._pair_queue.get(), timeout=pair_timeout)
        except asyncio.TimeoutError as exc:
            self._discard_session_state()
            raise ClientError(ClientErrorCode.TIMEOUT) from exc
        timeout = handshake_timeout
        if pair.role is not self._session_role:
            self._discard_session_state()
            raise ClientError(ClientErrorCode.SESSION)
        self._pair_id = pair.pair_id
        participant = self._handshake_participant
        material = self._session_phrase_material
        proof = participant.create_proof(
            material,
            server_id=self._require_context().server_id,
            pair_id=pair.pair_id,
            peer_hello=pair.peer_hello,
        )
        await self._request(
            "session_handshake",
            pair_id=b64url_encode(pair.pair_id),
            proof=b64url_encode(proof),
        )
        try:
            peer_pair_id, peer_role, peer_proof = await asyncio.wait_for(
                self._proof_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            participant.destroy()
            self._discard_session_state()
            raise ClientError(ClientErrorCode.TIMEOUT) from exc
        if peer_pair_id != pair.pair_id or peer_role is self._session_role:
            participant.destroy()
            await self._abort_session_route(pair.pair_id)
            self._discard_session_state()
            raise ClientError(ClientErrorCode.SESSION)
        try:
            session_material = participant.finalise(
                material,
                server_id=self._require_context().server_id,
                pair_id=pair.pair_id,
                peer_hello=pair.peer_hello,
                peer_proof=peer_proof,
            )
        except Exception as exc:
            await self._abort_session_route(pair.pair_id)
            self._discard_session_state()
            raise ClientError(ClientErrorCode.SESSION) from exc
        channel = LiveSessionChannel(session_material, self._session_role)
        identity = channel.encrypt_identity(self.display_id)
        self._session_channel = channel
        try:
            await self._drain_buffered_session_frames()
            await self._send_session_frame(identity)
            await self._wait_for_peer_identity(timeout)
        except Exception:
            await self._abort_session_route(pair.pair_id)
            self._discard_session_state()
            raise
        return channel

    async def send_session_text(self, text: str) -> None:
        channel = self._session_channel
        if channel is None or not channel.active:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        await self._send_session_frame(channel.encrypt_text(text))

    async def end_session(self) -> None:
        channel = self._session_channel
        pair_id = self._pair_id
        if channel is None or pair_id is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        frame = channel.encrypt_close(CloseReason.USER_END)
        try:
            await self._send_session_frame(frame)
            await self._request("session_close", pair_id=b64url_encode(pair_id))
        finally:
            self._discard_session_state()

    async def wait_closed(self) -> None:
        await self._connection_closed.wait()

    async def close(self) -> None:
        if self._closed:
            self._connection_closed.set()
            return
        self._closed = True
        self._connection_closed.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await _await_cancelled(self._heartbeat_task)
            self._heartbeat_task = None
        if self._board_resync_task is not None:
            self._board_resync_task.cancel()
            await _await_cancelled(self._board_resync_task)
            self._board_resync_task = None
        if self._session_channel is not None and not self._session_channel.discarded:
            self._session_channel.discard()
        if self._handshake_participant is not None:
            self._handshake_participant.destroy()
        writer = self.writer
        self.writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
            await _await_cancelled(self._reader_task)
            self._reader_task = None
        self.reader = None
        self._fail_pending(ClientError(ClientErrorCode.CONNECTION))

    async def __aenter__(self) -> HeadlessClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _request(self, frame_type: str, **fields: object) -> Mapping[str, object]:
        if not self.connected:
            raise ClientError(ClientErrorCode.CONNECTION)
        request_id = secrets.token_urlsafe(12).rstrip("=")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send_frame(make_frame(frame_type, request_id=request_id, **fields))
            try:
                response = await asyncio.wait_for(future, timeout=self.request_timeout)
            except asyncio.TimeoutError as exc:
                raise ClientError(ClientErrorCode.TIMEOUT) from exc
        finally:
            self._pending.pop(request_id, None)
        response_type = response.get("type")
        if response_type == "error":
            code = require_frame_string(response, "code", max_chars=64)
            raise RemoteProtocolError(code)
        if response_type != frame_type:
            raise ClientError(ClientErrorCode.PROTOCOL)
        return response

    async def _send_frame(self, frame: Mapping[str, object]) -> None:
        writer = self.writer
        if writer is None or self._closed:
            raise ClientError(ClientErrorCode.CONNECTION)
        try:
            async with self._write_lock:
                await write_outer_frame(writer, frame, max_bytes=self._max_frame_bytes)
        except (ConnectionError, OSError, ssl.SSLError, FrameError) as exc:
            raise ClientError(ClientErrorCode.CONNECTION) from exc

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                reader = self.reader
                if reader is None:
                    return
                try:
                    frame = await asyncio.wait_for(
                        read_outer_frame(reader, max_bytes=self._max_frame_bytes),
                        timeout=self._dead_timeout,
                    )
                except asyncio.TimeoutError:
                    raise ClientError(ClientErrorCode.TIMEOUT)
                except FrameError as exc:
                    if exc.code is FrameErrorCode.EOF:
                        return
                    raise ClientError(ClientErrorCode.PROTOCOL) from exc
                request_id = frame.get("request_id")
                if isinstance(request_id, str):
                    pending = self._pending.get(request_id)
                    if pending is not None and not pending.done():
                        pending.set_result(frame)
                        continue
                await self._handle_event(frame)
        except (ClientError, ConnectionError, OSError, ssl.SSLError) as exc:
            self._fail_pending(
                exc if isinstance(exc, ClientError) else ClientError(ClientErrorCode.CONNECTION)
            )
        finally:
            if not self._closed:
                self._closed = True
                self._connection_closed.set()
                if self._session_channel is not None and not self._session_channel.discarded:
                    self._session_channel.discard()
                await self.session_closed_events.put(SessionClosed("server_disconnect"))

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._heartbeat_interval)
                await self._send_frame(
                    make_frame("ping", nonce=b64url_encode(secrets.token_bytes(8)))
                )
        except (asyncio.CancelledError, ClientError):
            return

    async def _handle_event(self, frame: Mapping[str, object]) -> None:
        if frame.get("v") != PROTOCOL_VERSION:
            raise ClientError(ClientErrorCode.PROTOCOL)
        frame_type = require_frame_string(frame, "type", max_chars=64)
        if frame_type == "ping":
            nonce = require_frame_bytes(frame, "nonce", max_length=64)
            await self._send_frame(make_frame("pong", nonce=b64url_encode(nonce)))
            return
        if frame_type == "pong":
            require_frame_bytes(frame, "nonce", max_length=64)
            return
        if frame_type == "board_update":
            await self._handle_board_update(frame)
            return
        if frame_type == "board_remove":
            await self._handle_board_remove(frame)
            return
        if frame_type == "session_pair":
            await self._handle_session_pair(frame)
            return
        if frame_type == "session_handshake":
            await self._handle_session_handshake(frame)
            return
        if frame_type == "session_data":
            await self._handle_session_data(frame)
            return
        if frame_type == "session_close":
            await self._handle_session_close(frame)
            return
        if frame_type == "error":
            return
        raise ClientError(ClientErrorCode.PROTOCOL)

    async def _handle_board_update(self, frame: Mapping[str, object]) -> None:
        sequence = require_frame_int(frame, "sequence")
        if sequence != self._board_sequence + 1:
            self._schedule_board_resync()
            return
        raw = frame.get("record")
        if not isinstance(raw, dict):
            raise ClientError(ClientErrorCode.PROTOCOL)
        view = self._decode_board_view(raw)
        self._board_sequence = sequence
        if view is not None:
            self._board_records[view.stored.record.message_id] = view
        await self.board_events.put(
            BoardClientEvent("update", sequence, record=view)
        )

    async def _handle_board_remove(self, frame: Mapping[str, object]) -> None:
        sequence = require_frame_int(frame, "sequence")
        if sequence != self._board_sequence + 1:
            self._schedule_board_resync()
            return
        raw_ids = frame.get("message_ids")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 24:
            raise ClientError(ClientErrorCode.PROTOCOL)
        identifiers: list[bytes] = []
        for value in raw_ids:
            if not isinstance(value, str):
                raise ClientError(ClientErrorCode.PROTOCOL)
            identifiers.append(
                require_frame_bytes(
                    {"value": value},
                    "value",
                    expected_length=BOARD_MESSAGE_ID_BYTES,
                )
            )
        self._board_sequence = sequence
        for identifier in identifiers:
            self._board_records.pop(identifier, None)
        await self.board_events.put(
            BoardClientEvent("remove", sequence, tuple(identifiers))
        )

    async def _handle_session_pair(self, frame: Mapping[str, object]) -> None:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        role_text = require_frame_string(frame, "role", max_chars=16)
        peer_role_text = require_frame_string(frame, "peer_role", max_chars=16)
        try:
            role = SessionRole(role_text)
            peer_role = SessionRole(peer_role_text)
        except ValueError as exc:
            raise ClientError(ClientErrorCode.PROTOCOL) from exc
        if role is peer_role:
            raise ClientError(ClientErrorCode.PROTOCOL)
        peer_hello = HandshakeHello(
            role=peer_role,
            nonce=require_frame_bytes(
                frame, "peer_nonce", expected_length=HANDSHAKE_NONCE_BYTES
            ),
            public_key=require_frame_bytes(
                frame, "peer_public_key", expected_length=X25519_PUBLIC_BYTES
            ),
        )
        await self._pair_queue.put(SessionPair(pair_id, role, peer_hello))

    async def _handle_session_handshake(self, frame: Mapping[str, object]) -> None:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        role_text = require_frame_string(frame, "role", max_chars=16)
        try:
            role = SessionRole(role_text)
        except ValueError as exc:
            raise ClientError(ClientErrorCode.PROTOCOL) from exc
        proof = require_frame_bytes(frame, "proof", expected_length=HMAC_BYTES)
        await self._proof_queue.put((pair_id, role, proof))

    async def _handle_session_data(self, frame: Mapping[str, object]) -> None:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        if self._pair_id is not None and pair_id != self._pair_id:
            raise ClientError(ClientErrorCode.PROTOCOL)
        session_id = require_frame_bytes(
            frame, "session_id", expected_length=SESSION_ID_BYTES
        )
        direction_text = require_frame_string(frame, "direction", max_chars=64)
        try:
            direction = SessionDirection(direction_text)
        except ValueError as exc:
            raise ClientError(ClientErrorCode.PROTOCOL) from exc
        encrypted = EncryptedSessionFrame(
            session_id=session_id,
            direction=direction,
            counter=require_frame_int(frame, "counter", maximum=(1 << 64) - 1),
            body=require_frame_bytes(frame, "body", max_length=8 * 1024),
        )
        if self._session_channel is None:
            if len(self._buffered_session_frames) >= MAX_BUFFERED_SESSION_FRAMES:
                raise ClientError(ClientErrorCode.SESSION)
            self._buffered_session_frames.append(encrypted)
            return
        await self._decrypt_session_frame(encrypted)

    async def _handle_session_close(self, frame: Mapping[str, object]) -> None:
        reason = require_frame_string(frame, "reason", max_chars=64)
        pair_value = frame.get("pair_id")
        if pair_value is not None:
            pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
            if self._pair_id is None and self._session_role is None:
                return
            if self._pair_id is not None and pair_id != self._pair_id:
                return
        if self._session_channel is not None and not self._session_channel.discarded:
            self._session_channel.discard()
        if self._handshake_participant is not None:
            self._handshake_participant.destroy()
        await self.session_closed_events.put(SessionClosed(reason))
        self._discard_session_state()

    def _decode_board_view(self, raw: Mapping[str, object]) -> BoardViewRecord | None:
        if self.context is None or self.access_keys is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        record = EncryptedBoardRecord(
            message_id=require_frame_bytes(
                raw, "message_id", expected_length=BOARD_MESSAGE_ID_BYTES
            ),
            display_token=require_frame_bytes(
                raw, "display_token", expected_length=DISPLAY_TOKEN_BYTES
            ),
            ciphertext=require_frame_bytes(raw, "ciphertext", max_length=8 * 1024),
        )
        created = require_frame_int(raw, "created_at")
        expires = require_frame_int(raw, "expires_at")
        stored = StoredBoardRecord(record, created, expires)
        try:
            message = decrypt_board_record(record, self.context, self.access_keys)
        except BoardCryptoError:
            self.board_warnings.put_nowait("invalid_board_record")
            return None
        return BoardViewRecord(stored, message)

    def _schedule_board_resync(self) -> None:
        if self._board_resync_task is None or self._board_resync_task.done():
            self._board_resync_task = asyncio.create_task(
                self._resync_board_task(), name="grid-board-resync"
            )

    async def _resync_board_task(self) -> None:
        try:
            await self.synchronise_board()
            await self.board_events.put(
                BoardClientEvent("resync", self._board_sequence)
            )
        except (ClientError, FrameError):
            return

    def _prepare_session_state(
        self,
        role: SessionRole,
        material: SessionPhraseMaterial,
        participant: HandshakeParticipant,
    ) -> None:
        self._session_role = role
        self._session_phrase_material = material
        self._handshake_participant = participant
        self._pair_id = None
        self._session_channel = None
        self._buffered_session_frames.clear()
        self._peer_identity_ready = asyncio.Event()
        self._drain_queue(self._pair_queue)
        self._drain_queue(self._proof_queue)
        self._drain_queue(self.session_events)
        self._drain_queue(self.session_closed_events)

    def _require_ready_for_session(self) -> None:
        if (
            not self.connected
            or self.display_id is None
            or self.access_keys is None
            or self._session_role is not None
            or self._session_channel is not None
        ):
            raise ClientError(ClientErrorCode.INVALID_STATE)

    async def _send_session_frame(self, frame: EncryptedSessionFrame) -> None:
        if self._pair_id is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        response = await self._request(
            "session_data",
            pair_id=b64url_encode(self._pair_id),
            session_id=b64url_encode(frame.session_id),
            direction=frame.direction.value,
            counter=frame.counter,
            body=b64url_encode(frame.body),
        )
        if not require_frame_bool(response, "ok"):
            raise ClientError(ClientErrorCode.SESSION)

    async def _decrypt_session_frame(self, frame: EncryptedSessionFrame) -> None:
        channel = self._session_channel
        if channel is None:
            self._buffered_session_frames.append(frame)
            return
        try:
            event = channel.decrypt_frame(frame)
        except SessionIntegrityError:
            pair_id = self._pair_id
            await self.session_closed_events.put(SessionClosed("integrity_failure"))
            self._discard_session_state()
            if pair_id is not None:
                asyncio.create_task(self._close_route_after_failure(pair_id))
            return
        if event.event_type is SessionEventType.IDENTITY:
            self._peer_identity_ready.set()
            return
        await self.session_events.put(event)

    async def _drain_buffered_session_frames(self) -> None:
        while self._buffered_session_frames:
            frame = self._buffered_session_frames.popleft()
            await self._decrypt_session_frame(frame)

    async def _wait_for_peer_identity(self, timeout: float) -> None:
        channel = self._session_channel
        if channel is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        if channel.peer_display_id is not None:
            return
        try:
            await asyncio.wait_for(self._peer_identity_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ClientError(ClientErrorCode.TIMEOUT) from exc
        if channel.peer_display_id is None:
            raise ClientError(ClientErrorCode.SESSION)

    async def _abort_session_route(self, pair_id: bytes) -> None:
        try:
            await self._request("session_close", pair_id=b64url_encode(pair_id))
        except ClientError:
            pass

    async def _close_route_after_failure(self, pair_id: bytes) -> None:
        await self._abort_session_route(pair_id)

    def _discard_session_state(self) -> None:
        if self._handshake_participant is not None:
            self._handshake_participant.destroy()
        self._handshake_participant = None
        self._session_phrase_material = None
        self._session_role = None
        self._pair_id = None
        self._buffered_session_frames.clear()
        if self._session_channel is not None and not self._session_channel.discarded:
            self._session_channel.discard()
        self._session_channel = None

    def _require_context(self) -> AccessContext:
        if self.context is None:
            raise ClientError(ClientErrorCode.INVALID_STATE)
        return self.context

    def _fail_pending(self, error: ClientError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    @staticmethod
    def _drain_queue(queue: asyncio.Queue[object]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


def create_client_ssl_context(ca_file: Path | str | None = None) -> ssl.SSLContext:
    """Create the v1 hostname-verifying client TLS context."""

    context = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH,
        cafile=(None if ca_file is None else str(ca_file)),
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _await_cancelled(task: asyncio.Task[object]) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
