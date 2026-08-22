"""Headless v1 server relay, persistent board state, and live-session routing."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import ssl
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .access import (
    AccessChallengeVerifier,
    AccessContext,
    AccessVerifierState,
)
from .hub import (
    BOARD_CAPACITY,
    BOARD_COOLDOWN_SECONDS,
    BOARD_LIFETIME_SECONDS,
    BOARD_TEXT_MAX_BYTES,
    BoardPostResult,
    BoardStore,
    BoardStoreError,
    BoardStoreErrorCode,
    EncryptedBoardRecord,
    StoredBoardRecord,
)
from .protocol import (
    ACCESS_CLIENT_NONCE_BYTES,
    BOARD_MESSAGE_ID_BYTES,
    DISPLAY_TOKEN_BYTES,
    HMAC_BYTES,
    HANDSHAKE_NONCE_BYTES,
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
    encode_outer_frame,
    make_frame,
    read_outer_frame,
    require_frame_bytes,
    require_frame_int,
    require_frame_string,
    require_request_id,
    write_outer_frame,
)
from .sessions import (
    SESSION_TEXT_MAX_BYTES,
    HandshakeHello,
    SessionDirection,
    SessionRole,
)

DEFAULT_MAX_CONNECTIONS: Final = 64
DEFAULT_OUTBOUND_QUEUE: Final = 64
DEFAULT_HEARTBEAT_INTERVAL: Final = 30.0
DEFAULT_DEAD_TIMEOUT: Final = 90.0
DEFAULT_DISPLAY_LEASE_TIMEOUT: Final = 90.0
DEFAULT_SESSION_WAIT_TIMEOUT: Final = 15 * 60.0
DEFAULT_SESSION_HANDSHAKE_TIMEOUT: Final = 30.0
DEFAULT_ACCESS_FAILURE_LIMIT: Final = 5
DEFAULT_ACCESS_WINDOW: Final = 10 * 60.0
DEFAULT_SESSION_JOIN_LIMIT: Final = 20
DEFAULT_SESSION_JOIN_WINDOW: Final = 10 * 60.0
DEFAULT_MAINTENANCE_INTERVAL: Final = 1.0
MAX_SESSION_CIPHERTEXT_BYTES: Final = 8 * 1024


class RelayError(RuntimeError):
    """Raised for local server configuration or lifecycle failures."""


@dataclass(frozen=True, slots=True)
class RelayLimits:
    """Operational server limits; v1 product rules remain fixed constants."""

    max_connections: int = DEFAULT_MAX_CONNECTIONS
    max_frame_bytes: int = MAX_OUTER_FRAME_BYTES
    outbound_queue: int = DEFAULT_OUTBOUND_QUEUE
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    dead_timeout: float = DEFAULT_DEAD_TIMEOUT
    display_lease_timeout: float = DEFAULT_DISPLAY_LEASE_TIMEOUT
    session_wait_timeout: float = DEFAULT_SESSION_WAIT_TIMEOUT
    session_handshake_timeout: float = DEFAULT_SESSION_HANDSHAKE_TIMEOUT
    maintenance_interval: float = DEFAULT_MAINTENANCE_INTERVAL

    def __post_init__(self) -> None:
        for name in ("max_connections", "max_frame_bytes", "outbound_queue"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not 12 * 1024 <= self.max_frame_bytes <= MAX_OUTER_FRAME_BYTES:
            raise ValueError(
                "max_frame_bytes must be between 12288 and the protocol v1 maximum"
            )
        for name in (
            "heartbeat_interval",
            "dead_timeout",
            "display_lease_timeout",
            "session_wait_timeout",
            "session_handshake_timeout",
            "maintenance_interval",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.dead_timeout <= self.heartbeat_interval:
            raise ValueError("dead_timeout must exceed heartbeat_interval")

    def public_mapping(self) -> dict[str, object]:
        return {
            "max_connections": self.max_connections,
            "max_frame_bytes": self.max_frame_bytes,
            "board_capacity": BOARD_CAPACITY,
            "board_lifetime_seconds": BOARD_LIFETIME_SECONDS,
            "board_cooldown_seconds": BOARD_COOLDOWN_SECONDS,
            "board_text_max_bytes": BOARD_TEXT_MAX_BYTES,
            "session_text_max_bytes": SESSION_TEXT_MAX_BYTES,
            "session_wait_timeout_seconds": int(self.session_wait_timeout),
            "session_handshake_timeout_seconds": int(self.session_handshake_timeout),
            "display_lease_timeout_seconds": int(self.display_lease_timeout),
            "heartbeat_interval_seconds": self.heartbeat_interval,
            "dead_timeout_seconds": self.dead_timeout,
        }


class _WindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.time,
        increasing_delay: bool = False,
    ) -> None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self.clock = clock
        self.increasing_delay = increasing_delay
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def retry_after(self, key: str) -> int:
        now = self.clock()
        blocked = self._blocked_until.get(key, 0.0)
        if blocked <= now:
            self._blocked_until.pop(key, None)
            return 0
        return max(1, int(blocked - now + 0.999))

    def record(self, key: str) -> int:
        now = self.clock()
        bucket = self._events[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        bucket.append(now)
        if len(bucket) < self.limit:
            return 0
        if self.increasing_delay:
            excess = max(0, len(bucket) - self.limit)
            delay = min(60.0, float(2**excess))
        else:
            delay = max(1.0, self.window_seconds - (now - bucket[0]))
        self._blocked_until[key] = max(self._blocked_until.get(key, 0.0), now + delay)
        return self.retry_after(key)

    def clear(self, key: str) -> None:
        self._events.pop(key, None)
        self._blocked_until.pop(key, None)

    def prune(self) -> None:
        now = self.clock()
        cutoff = now - self.window_seconds
        for key in list(self._events):
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._events[key]
        for key, blocked in list(self._blocked_until.items()):
            if blocked <= now:
                del self._blocked_until[key]


@dataclass(slots=True)
class _DisplayReservation:
    token: bytes
    connection: _RelayConnection | None
    expires_at: float


@dataclass(slots=True)
class _WaitingRoom:
    room_id: bytes
    creator: _RelayConnection
    hello: HandshakeHello
    expires_at: float


@dataclass(slots=True)
class _SessionRoute:
    pair_id: bytes
    room_id: bytes
    creator: _RelayConnection
    joiner: _RelayConnection
    creator_hello: HandshakeHello
    joiner_hello: HandshakeHello
    handshake_deadline: float
    creator_proof_sent: bool = False
    joiner_proof_sent: bool = False
    session_id: bytes | None = None

    @property
    def active(self) -> bool:
        return self.creator_proof_sent and self.joiner_proof_sent

    def peer_of(self, connection: _RelayConnection) -> _RelayConnection:
        if connection is self.creator:
            return self.joiner
        if connection is self.joiner:
            return self.creator
        raise RelayError("connection is not part of session route")

    def role_of(self, connection: _RelayConnection) -> SessionRole:
        if connection is self.creator:
            return SessionRole.CREATOR
        if connection is self.joiner:
            return SessionRole.JOINER
        raise RelayError("connection is not part of session route")


class RelayServer:
    """One-Grid headless server with one encrypted board and temporary sessions."""

    def __init__(
        self,
        *,
        context: AccessContext,
        verifier_state: AccessVerifierState,
        database: Path | str,
        host: str = "127.0.0.1",
        port: int = 0,
        ssl_context: ssl.SSLContext | None = None,
        allow_plain: bool = False,
        limits: RelayLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(context, AccessContext):
            raise TypeError("context must be AccessContext")
        if not isinstance(verifier_state, AccessVerifierState):
            raise TypeError("verifier_state must be AccessVerifierState")
        if context.access_generation != verifier_state.access_generation:
            raise RelayError("access context and verifier generation differ")
        if not isinstance(host, str) or not host:
            raise ValueError("host must be non-empty")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if ssl_context is None:
            if not allow_plain:
                raise RelayError("TLS is required unless explicit loopback plain mode is enabled")
            if not _is_loopback_host(host):
                raise RelayError("plain transport is restricted to loopback development")
        elif allow_plain:
            raise RelayError("allow_plain must not be combined with TLS")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self.context = context
        self.verifier_state = verifier_state
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.allow_plain = allow_plain
        self.limits = RelayLimits() if limits is None else limits
        self.clock = clock
        self.board = BoardStore(database, access_generation=context.access_generation, clock=clock)
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[_RelayConnection] = set()
        self._reservations: dict[bytes, _DisplayReservation] = {}
        self._waiting_rooms: dict[bytes, _WaitingRoom] = {}
        self._routes: dict[bytes, _SessionRoute] = {}
        self._board_sequence = 0
        self._maintenance_task: asyncio.Task[None] | None = None
        self._closing = False
        self._rotating = False
        self._access_limiter = _WindowRateLimiter(
            DEFAULT_ACCESS_FAILURE_LIMIT,
            DEFAULT_ACCESS_WINDOW,
            clock=clock,
            increasing_delay=True,
        )
        self._join_limiter = _WindowRateLimiter(
            DEFAULT_SESSION_JOIN_LIMIT,
            DEFAULT_SESSION_JOIN_WINDOW,
            clock=clock,
        )

    async def start(self) -> RelayServer:
        if self._server is not None:
            raise RelayError("server is already started")
        self._closing = False
        self._server = await asyncio.start_server(
            self._accept,
            host=self.host,
            port=self.port,
            ssl=self.ssl_context,
            limit=self.limits.max_frame_bytes,
        )
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(), name="grid-relay-maintenance"
        )
        return self

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            raise RelayError("server is not started")
        address = self._server.sockets[0].getsockname()
        return str(address[0]), int(address[1])

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def board_sequence(self) -> int:
        return self._board_sequence

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            await _await_cancelled(self._maintenance_task)
            self._maintenance_task = None
        connections = list(self._connections)
        await asyncio.gather(*(connection.close() for connection in connections), return_exceptions=True)
        self._connections.clear()
        self._waiting_rooms.clear()
        self._routes.clear()
        self._reservations.clear()
        self.board.close()

    async def __aenter__(self) -> RelayServer:
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing or self._rotating or len(self._connections) >= self.limits.max_connections:
            writer.close()
            await writer.wait_closed()
            return
        connection = _RelayConnection(self, reader, writer)
        self._connections.add(connection)
        await connection.run()

    async def rotate_access(
        self,
        context: AccessContext,
        verifier_state: AccessVerifierState,
    ) -> None:
        """Apply one persisted access rotation and disconnect old-generation clients."""

        if not isinstance(context, AccessContext):
            raise TypeError("context must be AccessContext")
        if not isinstance(verifier_state, AccessVerifierState):
            raise TypeError("verifier_state must be AccessVerifierState")
        if context.server_id != self.context.server_id:
            raise RelayError("access rotation cannot change server identity")
        if context.access_generation != verifier_state.access_generation:
            raise RelayError("access context and verifier generation differ")
        if context.access_generation == self.context.access_generation:
            raise RelayError("access rotation must use a fresh generation")

        self._rotating = True
        try:
            connections = list(self._connections)
            await asyncio.gather(
                *(connection.close() for connection in connections),
                return_exceptions=True,
            )
            self._connections.clear()
            self._waiting_rooms.clear()
            self._routes.clear()
            self._reservations.clear()
            self.board.bind_access_generation(context.access_generation)
            self.context = context
            self.verifier_state = verifier_state
            self._board_sequence += 1
            self._access_limiter = _WindowRateLimiter(
                DEFAULT_ACCESS_FAILURE_LIMIT,
                DEFAULT_ACCESS_WINDOW,
                clock=self.clock,
                increasing_delay=True,
            )
            self._join_limiter = _WindowRateLimiter(
                DEFAULT_SESSION_JOIN_LIMIT,
                DEFAULT_SESSION_JOIN_WINDOW,
                clock=self.clock,
            )
        finally:
            self._rotating = False

    async def _connection_closed(self, connection: _RelayConnection) -> None:
        self._connections.discard(connection)
        now = self.clock()
        if connection.display_token is not None:
            reservation = self._reservations.get(connection.display_token)
            if reservation is not None and reservation.connection is connection:
                reservation.connection = None
                reservation.expires_at = now + self.limits.display_lease_timeout
        if connection.waiting_room_id is not None:
            room = self._waiting_rooms.get(connection.waiting_room_id)
            if room is not None and room.creator is connection:
                self._waiting_rooms.pop(connection.waiting_room_id, None)
        if connection.pair_id is not None:
            await self._close_route(connection.pair_id, reason="peer_disconnect", source=connection)

    def reserve_display(self, connection: _RelayConnection, token: bytes) -> bool:
        now = self.clock()
        self._prune_reservations(now)
        existing = self._reservations.get(token)
        if existing is not None:
            if existing.connection is connection:
                existing.expires_at = now + self.limits.display_lease_timeout
                return True
            return False
        self._reservations[token] = _DisplayReservation(
            token=token,
            connection=connection,
            expires_at=now + self.limits.display_lease_timeout,
        )
        return True

    def touch_display(self, connection: _RelayConnection) -> None:
        if connection.display_token is None:
            return
        reservation = self._reservations.get(connection.display_token)
        if reservation is not None and reservation.connection is connection:
            reservation.expires_at = self.clock() + self.limits.display_lease_timeout

    async def board_list(self) -> tuple[tuple[StoredBoardRecord, ...], int]:
        records, removed = self.board.list_current()
        if removed:
            await self._broadcast_board_remove(removed)
        return records, self._board_sequence

    async def board_post(
        self,
        connection: _RelayConnection,
        record: EncryptedBoardRecord,
    ) -> BoardPostResult:
        if connection.display_token is None or record.display_token != connection.display_token:
            raise FrameError(FrameErrorCode.INVALID)
        result = self.board.post(record)
        if result.removed_message_ids:
            await self._broadcast_board_remove(result.removed_message_ids)
        if result.accepted and result.stored is not None:
            await self._broadcast_board_update(result.stored)
        return result

    async def subscribe_board(self, connection: _RelayConnection, after_sequence: int) -> bool:
        if after_sequence != self._board_sequence:
            return False
        connection.board_subscribed = True
        return True

    async def create_waiting_room(
        self,
        connection: _RelayConnection,
        room_id: bytes,
        hello: HandshakeHello,
    ) -> bool:
        self._cleanup_sessions(self.clock())
        if connection.waiting_room_id is not None or connection.pair_id is not None:
            return False
        if room_id in self._waiting_rooms or any(
            route.room_id == room_id for route in self._routes.values()
        ):
            return False
        room = _WaitingRoom(
            room_id=room_id,
            creator=connection,
            hello=hello,
            expires_at=self.clock() + self.limits.session_wait_timeout,
        )
        self._waiting_rooms[room_id] = room
        connection.waiting_room_id = room_id
        return True

    async def cancel_waiting_room(self, connection: _RelayConnection) -> bool:
        """Remove one unpaired waiting room owned by the connection."""

        room_id = connection.waiting_room_id
        if room_id is None:
            return False
        room = self._waiting_rooms.get(room_id)
        if room is None or room.creator is not connection:
            connection.waiting_room_id = None
            return False
        self._waiting_rooms.pop(room_id, None)
        connection.waiting_room_id = None
        return True

    async def join_waiting_room(
        self,
        connection: _RelayConnection,
        room_id: bytes,
        hello: HandshakeHello,
    ) -> _SessionRoute | None:
        retry = self._join_limiter.retry_after(connection.peer_key)
        if retry:
            return None
        self._join_limiter.record(connection.peer_key)
        self._cleanup_sessions(self.clock())
        if connection.waiting_room_id is not None or connection.pair_id is not None:
            return None
        room = self._waiting_rooms.pop(room_id, None)
        if room is None or room.creator.closed or room.creator.pair_id is not None:
            return None
        room.creator.waiting_room_id = None
        pair_id = secrets.token_bytes(PAIR_ID_BYTES)
        route = _SessionRoute(
            pair_id=pair_id,
            room_id=room_id,
            creator=room.creator,
            joiner=connection,
            creator_hello=room.hello,
            joiner_hello=hello,
            handshake_deadline=self.clock() + self.limits.session_handshake_timeout,
        )
        self._routes[pair_id] = route
        room.creator.pair_id = pair_id
        connection.pair_id = pair_id
        return route

    def route_for(self, connection: _RelayConnection, pair_id: bytes) -> _SessionRoute:
        route = self._routes.get(pair_id)
        if route is None or connection not in (route.creator, route.joiner):
            raise FrameError(FrameErrorCode.INVALID)
        return route

    async def mark_handshake_proof(
        self,
        connection: _RelayConnection,
        pair_id: bytes,
        proof: bytes,
    ) -> None:
        route = self.route_for(connection, pair_id)
        if self.clock() > route.handshake_deadline:
            await self._close_route(pair_id, reason="handshake_timeout")
            raise FrameError(FrameErrorCode.INVALID)
        role = route.role_of(connection)
        if role is SessionRole.CREATOR:
            if route.creator_proof_sent:
                raise FrameError(FrameErrorCode.INVALID)
            route.creator_proof_sent = True
        else:
            if route.joiner_proof_sent:
                raise FrameError(FrameErrorCode.INVALID)
            route.joiner_proof_sent = True
        peer = route.peer_of(connection)
        await peer.send_event(
            make_frame(
                "session_handshake",
                pair_id=b64url_encode(pair_id),
                role=role.value,
                proof=b64url_encode(proof),
            )
        )

    async def route_session_data(
        self,
        connection: _RelayConnection,
        *,
        pair_id: bytes,
        session_id: bytes,
        direction: SessionDirection,
        counter: int,
        body: bytes,
    ) -> None:
        route = self.route_for(connection, pair_id)
        if not route.active:
            raise FrameError(FrameErrorCode.INVALID)
        expected_direction = (
            SessionDirection.CREATOR_TO_JOINER
            if route.role_of(connection) is SessionRole.CREATOR
            else SessionDirection.JOINER_TO_CREATOR
        )
        if direction is not expected_direction:
            raise FrameError(FrameErrorCode.INVALID)
        if route.session_id is None:
            route.session_id = session_id
        elif route.session_id != session_id:
            raise FrameError(FrameErrorCode.INVALID)
        peer = route.peer_of(connection)
        await peer.send_event(
            make_frame(
                "session_data",
                pair_id=b64url_encode(pair_id),
                session_id=b64url_encode(session_id),
                direction=direction.value,
                counter=counter,
                body=b64url_encode(body),
            )
        )

    async def close_session_route(
        self,
        connection: _RelayConnection,
        pair_id: bytes,
    ) -> None:
        self.route_for(connection, pair_id)
        await self._close_route(pair_id, reason="peer_close", source=connection)

    async def _broadcast_board_update(self, stored: StoredBoardRecord) -> None:
        self._board_sequence += 1
        frame = make_frame(
            "board_update",
            sequence=self._board_sequence,
            record=_stored_record_mapping(stored),
        )
        await self._broadcast_to_subscribers(frame)

    async def _broadcast_board_remove(self, message_ids: tuple[bytes, ...] | list[bytes]) -> None:
        if not message_ids:
            return
        self._board_sequence += 1
        frame = make_frame(
            "board_remove",
            sequence=self._board_sequence,
            message_ids=[b64url_encode(item) for item in message_ids],
        )
        await self._broadcast_to_subscribers(frame)

    async def _broadcast_to_subscribers(self, frame: Mapping[str, object]) -> None:
        subscribers = [
            connection
            for connection in self._connections
            if connection.board_subscribed and not connection.closed
        ]
        if subscribers:
            await asyncio.gather(
                *(connection.send_event(frame) for connection in subscribers),
                return_exceptions=True,
            )

    async def _close_route(
        self,
        pair_id: bytes,
        *,
        reason: str,
        source: _RelayConnection | None = None,
    ) -> None:
        route = self._routes.pop(pair_id, None)
        if route is None:
            return
        route.creator.pair_id = None
        route.joiner.pair_id = None
        recipients = [route.creator, route.joiner]
        if source is not None:
            recipients = [item for item in recipients if item is not source]
        for connection in recipients:
            if not connection.closed:
                try:
                    await connection.send_event(
                        make_frame(
                            "session_close",
                            pair_id=b64url_encode(pair_id),
                            reason=reason,
                        )
                    )
                except Exception:
                    pass

    async def _maintenance_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.limits.maintenance_interval)
                now = self.clock()
                self._prune_reservations(now)
                self._access_limiter.prune()
                self._join_limiter.prune()
                removed = self.board.cleanup(now=int(now))
                if removed:
                    await self._broadcast_board_remove(removed)
                await self._expire_sessions(now)
        except asyncio.CancelledError:
            raise

    def _prune_reservations(self, now: float) -> None:
        for token, reservation in list(self._reservations.items()):
            if reservation.connection is None and reservation.expires_at <= now:
                del self._reservations[token]

    def _cleanup_sessions(self, now: float) -> None:
        for room_id, room in list(self._waiting_rooms.items()):
            if room.expires_at <= now or room.creator.closed:
                self._waiting_rooms.pop(room_id, None)
                if room.creator.waiting_room_id == room_id:
                    room.creator.waiting_room_id = None

    async def _expire_sessions(self, now: float) -> None:
        expired_rooms: list[_WaitingRoom] = []
        for room_id, room in list(self._waiting_rooms.items()):
            if room.expires_at <= now or room.creator.closed:
                self._waiting_rooms.pop(room_id, None)
                if room.creator.waiting_room_id == room_id:
                    room.creator.waiting_room_id = None
                if not room.creator.closed:
                    expired_rooms.append(room)
        for room in expired_rooms:
            await room.creator.send_event(
                make_frame("session_close", reason="wait_timeout")
            )
        for pair_id, route in list(self._routes.items()):
            if not route.active and route.handshake_deadline <= now:
                await self._close_route(pair_id, reason="handshake_timeout")


class _RelayConnection:
    def __init__(
        self,
        server: RelayServer,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        self.outbound: asyncio.Queue[Mapping[str, object] | None] = asyncio.Queue(
            maxsize=server.limits.outbound_queue
        )
        self.writer_task: asyncio.Task[None] | None = None
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.hello_seen = False
        self.authenticated = False
        self.challenge: AccessChallengeVerifier | None = None
        self.display_token: bytes | None = None
        self.board_subscribed = False
        self.waiting_room_id: bytes | None = None
        self.pair_id: bytes | None = None
        self.closed = False
        self._close_lock = asyncio.Lock()
        self.peer_key = _peer_key(writer)

    async def run(self) -> None:
        self.writer_task = asyncio.create_task(self._writer_loop(), name="grid-relay-writer")
        try:
            while not self.closed:
                try:
                    frame = await asyncio.wait_for(
                        read_outer_frame(
                            self.reader,
                            max_bytes=self.server.limits.max_frame_bytes,
                        ),
                        timeout=self.server.limits.dead_timeout,
                    )
                except asyncio.TimeoutError:
                    break
                except FrameError as exc:
                    if exc.code is FrameErrorCode.EOF:
                        break
                    break
                self.server.touch_display(self)
                keep_open = await self._handle_frame(frame)
                if not keep_open:
                    break
        except (ConnectionError, OSError, ssl.SSLError):
            pass
        finally:
            await self.close()
            await self.server._connection_closed(self)

    async def close(self) -> None:
        async with self._close_lock:
            if self.closed:
                return
            self.closed = True
            if self.heartbeat_task is not None:
                self.heartbeat_task.cancel()
                await _await_cancelled(self.heartbeat_task)
                self.heartbeat_task = None
            try:
                self.outbound.put_nowait(None)
            except asyncio.QueueFull:
                pass
            if self.writer_task is not None and self.writer_task is not asyncio.current_task():
                try:
                    await asyncio.wait_for(self.writer_task, timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    self.writer_task.cancel()
                    await _await_cancelled(self.writer_task)
                self.writer_task = None
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass

    async def send_event(self, frame: Mapping[str, object]) -> None:
        if self.closed:
            raise ConnectionError("connection is closed")
        try:
            self.outbound.put_nowait(dict(frame))
        except asyncio.QueueFull as exc:
            asyncio.create_task(self.close())
            raise ConnectionError("outbound queue is full") from exc

    async def _send_response(
        self,
        request_id: str,
        frame_type: str,
        **fields: object,
    ) -> None:
        await self.send_event(make_frame(frame_type, request_id=request_id, **fields))

    async def _send_error(
        self,
        request_id: str | None,
        code: str,
    ) -> None:
        fields: dict[str, object] = {"code": code}
        if request_id is not None:
            fields["request_id"] = request_id
        await self.send_event(make_frame("error", **fields))

    async def _writer_loop(self) -> None:
        try:
            while True:
                frame = await self.outbound.get()
                if frame is None:
                    return
                await write_outer_frame(
                    self.writer,
                    frame,
                    max_bytes=self.server.limits.max_frame_bytes,
                )
        except (ConnectionError, OSError, ssl.SSLError, FrameError):
            return

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.server.limits.heartbeat_interval)
                await self.send_event(
                    make_frame("ping", nonce=b64url_encode(secrets.token_bytes(8)))
                )
        except (asyncio.CancelledError, ConnectionError):
            return

    async def _handle_frame(self, frame: Mapping[str, object]) -> bool:
        request_id: str | None = None
        try:
            frame_type = require_frame_string(frame, "type", max_chars=64)
            if frame.get("v") != PROTOCOL_VERSION:
                request_id = _optional_request_id(frame)
                await self._send_error(request_id, "unsupported_protocol")
                return False
            if frame_type == "ping":
                nonce = require_frame_bytes(frame, "nonce", max_length=64)
                await self.send_event(make_frame("pong", nonce=b64url_encode(nonce)))
                return True
            if frame_type == "pong":
                require_frame_bytes(frame, "nonce", max_length=64)
                return True

            request_id = require_request_id(frame)
            if not self.hello_seen:
                if frame_type != "hello":
                    await self._send_error(request_id, "protocol_state")
                    return False
                return await self._handle_hello(frame, request_id)
            if not self.authenticated:
                if frame_type != "access_proof":
                    await self._send_error(request_id, "protocol_state")
                    return False
                return await self._handle_access_proof(frame, request_id)
            if self.display_token is None:
                if frame_type != "display_reserve":
                    await self._send_error(request_id, "protocol_state")
                    return False
                return await self._handle_display_reserve(frame, request_id)

            handlers = {
                "board_list": self._handle_board_list,
                "board_subscribe": self._handle_board_subscribe,
                "board_post": self._handle_board_post,
                "session_wait": self._handle_session_wait,
                "session_cancel": self._handle_session_cancel,
                "session_join": self._handle_session_join,
                "session_handshake": self._handle_session_handshake,
                "session_data": self._handle_session_data,
                "session_close": self._handle_session_close,
            }
            handler = handlers.get(frame_type)
            if handler is None:
                await self._send_error(request_id, "unsupported_frame")
                return True
            return await handler(frame, request_id)
        except (FrameError, ValueError, TypeError):
            try:
                await self._send_error(request_id, "invalid_frame")
            except Exception:
                pass
            return False
        except BoardStoreError:
            try:
                await self._send_error(request_id, "server_state")
            except Exception:
                pass
            return False

    async def _handle_hello(self, frame: Mapping[str, object], request_id: str) -> bool:
        require_frame_string(frame, "client_version", max_chars=64)
        capabilities = frame.get("capabilities")
        if not isinstance(capabilities, list) or len(capabilities) > 32:
            raise FrameError(FrameErrorCode.INVALID)
        for item in capabilities:
            if not isinstance(item, str) or not item or len(item) > 64 or not item.isascii():
                raise FrameError(FrameErrorCode.INVALID)
        self.challenge = AccessChallengeVerifier.generate(
            self.server.context,
            self.server.verifier_state,
        )
        self.hello_seen = True
        await self._send_response(
            request_id,
            "hello",
            server_id=b64url_encode(self.server.context.server_id),
            access_generation=b64url_encode(self.server.context.access_generation),
            server_time=int(self.server.clock()),
            limits=self.server.limits.public_mapping(),
            heartbeat_interval=self.server.limits.heartbeat_interval,
            access_challenge=b64url_encode(self.challenge.challenge),
        )
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="grid-relay-heartbeat"
        )
        return True

    async def _handle_access_proof(self, frame: Mapping[str, object], request_id: str) -> bool:
        retry = self.server._access_limiter.retry_after(self.peer_key)
        if retry:
            await self._send_response(
                request_id,
                "access_proof",
                ok=False,
                retry_after=retry,
            )
            return False
        if self.challenge is None:
            raise FrameError(FrameErrorCode.INVALID)
        nonce = require_frame_bytes(
            frame,
            "client_nonce",
            expected_length=ACCESS_CLIENT_NONCE_BYTES,
        )
        proof = require_frame_bytes(frame, "proof", expected_length=HMAC_BYTES)
        if not self.challenge.verify(client_nonce=nonce, proof=proof):
            retry = self.server._access_limiter.record(self.peer_key)
            await self._send_response(
                request_id,
                "access_proof",
                ok=False,
                retry_after=retry,
            )
            return False
        self.server._access_limiter.clear(self.peer_key)
        self.authenticated = True
        await self._send_response(request_id, "access_proof", ok=True, retry_after=0)
        return True

    async def _handle_display_reserve(self, frame: Mapping[str, object], request_id: str) -> bool:
        token = require_frame_bytes(
            frame,
            "display_token",
            expected_length=DISPLAY_TOKEN_BYTES,
        )
        if not self.server.reserve_display(self, token):
            await self._send_response(request_id, "display_reserve", ok=False)
            return True
        self.display_token = token
        await self._send_response(
            request_id,
            "display_reserve",
            ok=True,
            post_remaining=self.server.board.cooldown_remaining(token),
        )
        return True

    async def _handle_board_list(self, frame: Mapping[str, object], request_id: str) -> bool:
        offset = 0 if "offset" not in frame else require_frame_int(
            frame, "offset", maximum=BOARD_CAPACITY
        )
        snapshot_sequence = None
        if "snapshot_sequence" in frame:
            snapshot_sequence = require_frame_int(frame, "snapshot_sequence")
        records, sequence = await self.server.board_list()
        if snapshot_sequence is not None and snapshot_sequence != sequence:
            await self._send_response(
                request_id,
                "board_list",
                sequence=sequence,
                restart=True,
                offset=0,
                next_offset=0,
                done=False,
                records=[],
            )
            return True
        if offset > len(records):
            raise FrameError(FrameErrorCode.INVALID)
        mappings = [_stored_record_mapping(item) for item in records]
        page: list[dict[str, object]] = []
        next_offset = offset
        while next_offset < len(mappings):
            candidate_page = [*page, mappings[next_offset]]
            candidate_next = next_offset + 1
            candidate = make_frame(
                "board_list",
                request_id=request_id,
                sequence=sequence,
                restart=False,
                offset=offset,
                next_offset=candidate_next,
                done=candidate_next >= len(mappings),
                records=candidate_page,
            )
            try:
                encode_outer_frame(
                    candidate, max_bytes=self.server.limits.max_frame_bytes
                )
            except FrameError as exc:
                if exc.code is not FrameErrorCode.TOO_LARGE:
                    raise
                break
            page = candidate_page
            next_offset = candidate_next
        if next_offset < len(mappings) and not page:
            raise FrameError(FrameErrorCode.TOO_LARGE)
        await self._send_response(
            request_id,
            "board_list",
            sequence=sequence,
            restart=False,
            offset=offset,
            next_offset=next_offset,
            done=next_offset >= len(mappings),
            records=page,
        )
        return True

    async def _handle_board_subscribe(self, frame: Mapping[str, object], request_id: str) -> bool:
        after = require_frame_int(frame, "after_sequence")
        ok = await self.server.subscribe_board(self, after)
        await self._send_response(
            request_id,
            "board_subscribe",
            ok=ok,
            sequence=self.server.board_sequence,
        )
        return True

    async def _handle_board_post(self, frame: Mapping[str, object], request_id: str) -> bool:
        record = EncryptedBoardRecord(
            message_id=require_frame_bytes(
                frame, "message_id", expected_length=BOARD_MESSAGE_ID_BYTES
            ),
            display_token=require_frame_bytes(
                frame, "display_token", expected_length=DISPLAY_TOKEN_BYTES
            ),
            ciphertext=require_frame_bytes(
                frame,
                "ciphertext",
                max_length=8 * 1024,
            ),
        )
        try:
            result = await self.server.board_post(self, record)
        except BoardStoreError as exc:
            if exc.code is BoardStoreErrorCode.DUPLICATE_MESSAGE:
                await self._send_response(
                    request_id,
                    "board_post",
                    ok=False,
                    reason="duplicate_message",
                    remaining=0,
                )
                return True
            raise
        if not result.accepted:
            remaining = max(0, int(result.next_post_at or 0) - int(self.server.clock()))
            await self._send_response(
                request_id,
                "board_post",
                ok=False,
                reason="cooldown",
                remaining=remaining,
            )
            return True
        await self._send_response(
            request_id,
            "board_post",
            ok=True,
            remaining=BOARD_COOLDOWN_SECONDS,
        )
        return True

    async def _handle_session_wait(self, frame: Mapping[str, object], request_id: str) -> bool:
        room_id = require_frame_bytes(frame, "room_id", expected_length=ROOM_ID_BYTES)
        hello = _hello_from_frame(frame, expected_role=SessionRole.CREATOR)
        ok = await self.server.create_waiting_room(self, room_id, hello)
        await self._send_response(request_id, "session_wait", ok=ok)
        return True

    async def _handle_session_cancel(self, frame: Mapping[str, object], request_id: str) -> bool:
        ok = await self.server.cancel_waiting_room(self)
        await self._send_response(request_id, "session_cancel", ok=ok)
        return True

    async def _handle_session_join(self, frame: Mapping[str, object], request_id: str) -> bool:
        room_id = require_frame_bytes(frame, "room_id", expected_length=ROOM_ID_BYTES)
        hello = _hello_from_frame(frame, expected_role=SessionRole.JOINER)
        route = await self.server.join_waiting_room(self, room_id, hello)
        if route is None:
            await self._send_response(request_id, "session_join", ok=False)
            return True
        await self._send_response(
            request_id,
            "session_join",
            ok=True,
            pair_id=b64url_encode(route.pair_id),
        )
        await route.creator.send_event(
            make_frame(
                "session_pair",
                pair_id=b64url_encode(route.pair_id),
                role=SessionRole.CREATOR.value,
                peer_role=SessionRole.JOINER.value,
                peer_nonce=b64url_encode(route.joiner_hello.nonce),
                peer_public_key=b64url_encode(route.joiner_hello.public_key),
            )
        )
        await route.joiner.send_event(
            make_frame(
                "session_pair",
                pair_id=b64url_encode(route.pair_id),
                role=SessionRole.JOINER.value,
                peer_role=SessionRole.CREATOR.value,
                peer_nonce=b64url_encode(route.creator_hello.nonce),
                peer_public_key=b64url_encode(route.creator_hello.public_key),
            )
        )
        return True

    async def _handle_session_handshake(self, frame: Mapping[str, object], request_id: str) -> bool:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        proof = require_frame_bytes(frame, "proof", expected_length=HMAC_BYTES)
        await self.server.mark_handshake_proof(self, pair_id, proof)
        await self._send_response(request_id, "session_handshake", ok=True)
        return True

    async def _handle_session_data(self, frame: Mapping[str, object], request_id: str) -> bool:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        session_id = require_frame_bytes(
            frame, "session_id", expected_length=SESSION_ID_BYTES
        )
        direction_text = require_frame_string(frame, "direction", max_chars=64)
        try:
            direction = SessionDirection(direction_text)
        except ValueError as exc:
            raise FrameError(FrameErrorCode.INVALID) from exc
        counter = require_frame_int(frame, "counter", maximum=(1 << 64) - 1)
        body = require_frame_bytes(
            frame,
            "body",
            max_length=MAX_SESSION_CIPHERTEXT_BYTES,
        )
        await self.server.route_session_data(
            self,
            pair_id=pair_id,
            session_id=session_id,
            direction=direction,
            counter=counter,
            body=body,
        )
        await self._send_response(request_id, "session_data", ok=True)
        return True

    async def _handle_session_close(self, frame: Mapping[str, object], request_id: str) -> bool:
        pair_id = require_frame_bytes(frame, "pair_id", expected_length=PAIR_ID_BYTES)
        await self._send_response(request_id, "session_close", ok=True)
        await self.server.close_session_route(self, pair_id)
        return True


def create_server_ssl_context(certificate: Path | str, private_key: Path | str) -> ssl.SSLContext:
    """Create the v1 TLS server context from an operator-provided certificate."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(certificate), keyfile=str(private_key))
    return context


def _stored_record_mapping(stored: StoredBoardRecord) -> dict[str, object]:
    return {
        "message_id": b64url_encode(stored.record.message_id),
        "display_token": b64url_encode(stored.record.display_token),
        "created_at": stored.created_at,
        "expires_at": stored.expires_at,
        "ciphertext": b64url_encode(stored.record.ciphertext),
    }


def _hello_from_frame(frame: Mapping[str, object], *, expected_role: SessionRole) -> HandshakeHello:
    role_text = require_frame_string(frame, "role", max_chars=16)
    try:
        role = SessionRole(role_text)
    except ValueError as exc:
        raise FrameError(FrameErrorCode.INVALID) from exc
    if role is not expected_role:
        raise FrameError(FrameErrorCode.INVALID)
    return HandshakeHello(
        role=role,
        nonce=require_frame_bytes(
            frame, "handshake_nonce", expected_length=HANDSHAKE_NONCE_BYTES
        ),
        public_key=require_frame_bytes(
            frame, "public_key", expected_length=X25519_PUBLIC_BYTES
        ),
    )


def _optional_request_id(frame: Mapping[str, object]) -> str | None:
    if "request_id" not in frame:
        return None
    try:
        return require_request_id(frame)
    except FrameError:
        return None


def _peer_key(writer: asyncio.StreamWriter) -> str:
    peer = writer.get_extra_info("peername")
    if isinstance(peer, tuple) and peer:
        return str(peer[0])
    return "unknown"


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
