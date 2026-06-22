"""
ROUTER / DEALER sockets over asyncio (tcp:// and ipc://). Routing-ids; direct
connect/disconnect events (ROUTER); DEALER auto-reconnect. The transport-agnostic
caller treats a peer as the opaque routing-id, exactly as AbletonOSC's transport does.
"""

import asyncio
import os
import struct
from collections import namedtuple

from . import protocol
from .connection import ZmtpConnection
from .endpoints import parse_endpoint, format_tcp
from .errors import HostUnreachable, ZMTPError

PeerEvent = namedtuple("PeerEvent", ["kind", "routing_id"])   # kind: "connect" | "disconnect"


class _BaseSocket:
    _socket_type_name = None

    def __init__(self, context):
        self._context = context
        self._loop = None
        self._recv_q = asyncio.Queue()
        self._closing = False
        self._own_routing_id = None          # DEALER's announced Identity (None for ROUTER)
        self.last_endpoint = None
        self._server = None                  # set by bind()
        self._ipc_path = None                # set when we bound an ipc:// (we own/unlink it)
        self._conns = set()                  # all live connections (handshaken or not)

    async def recv_multipart(self):
        return await self._recv_q.get()

    # --- connection callbacks (from ZmtpConnection) ---
    def _on_connection_made(self, conn):
        self._conns.add(conn)

    def _on_ready(self, conn, peer_socket_type, peer_identity):
        pass

    def _on_message(self, conn, frames):
        raise NotImplementedError

    def _on_connection_lost(self, conn, exc):
        self._conns.discard(conn)

    async def close(self, linger=0):
        self._closing = True
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        for conn in list(self._conns):
            conn.close()
        self._conns.clear()
        if self._ipc_path is not None:
            try:
                os.unlink(self._ipc_path)
            except FileNotFoundError:
                pass
            self._ipc_path = None


class RouterSocket(_BaseSocket):
    _socket_type_name = protocol.SOCKET_TYPE_ROUTER

    def __init__(self, context):
        super().__init__(context)
        self._peers = {}                                  # routing_id -> connection
        self.events = asyncio.Queue()                     # PeerEvent connect/disconnect
        self._next_auto = struct.unpack(">I", os.urandom(4))[0]   # libzmq-style random seed

    async def bind(self, endpoint):
        self._loop = asyncio.get_running_loop()
        ep = parse_endpoint(endpoint)
        factory = lambda: ZmtpConnection(self, as_server=True)
        if ep.is_ipc:
            self._server = await self._loop.create_unix_server(factory, path=ep.path)
            self._ipc_path = ep.path
            self.last_endpoint = endpoint
        else:
            self._server = await self._loop.create_server(factory, ep.host, ep.port)
            host, port = self._server.sockets[0].getsockname()[:2]
            self.last_endpoint = format_tcp(host, port)   # resolves an ephemeral :0
        return self.last_endpoint

    def _auto_id(self):
        rid = b"\x00" + struct.pack(">I", self._next_auto)   # 0x00 + 4-byte BE counter
        self._next_auto = (self._next_auto + 1) & 0xFFFFFFFF
        return rid

    def _on_ready(self, conn, peer_socket_type, peer_identity):
        if peer_identity and peer_identity[0:1] != b"\x00":
            rid = peer_identity
        else:
            rid = self._auto_id()
        old = self._peers.get(rid)
        if old is not None and old is not conn:           # same-id reconnect: replace
            old.close()
            self.events.put_nowait(PeerEvent("disconnect", rid))
        conn.peer_routing_id = rid
        self._peers[rid] = conn
        self.events.put_nowait(PeerEvent("connect", rid))

    def _on_message(self, conn, frames):
        self._recv_q.put_nowait([conn.peer_routing_id] + list(frames))

    def _on_connection_lost(self, conn, exc):
        super()._on_connection_lost(conn, exc)
        rid = conn.peer_routing_id
        if rid is not None and self._peers.get(rid) is conn:
            del self._peers[rid]
            self.events.put_nowait(PeerEvent("disconnect", rid))

    async def send_multipart(self, frames):
        frames = list(frames)
        if not frames:
            raise ZMTPError("ROUTER send_multipart needs [routing_id, *parts]")
        rid, parts = frames[0], frames[1:]
        conn = self._peers.get(rid)
        if conn is None:
            raise HostUnreachable(rid)        # ROUTER_MANDATORY semantics, always on
        conn.send_message(parts)

    def peers(self):
        return list(self._peers.keys())


class DealerSocket(_BaseSocket):
    _socket_type_name = protocol.SOCKET_TYPE_DEALER

    def __init__(self, context):
        super().__init__(context)
        self._conn = None
        self._endpoint = None
        self._ready = False
        self._pending_out = []                # queued while no connection exists
        self.reconnect = True
        self.reconnect_ivl = 0.1
        self.reconnect_ivl_max = 5.0
        self._reconnect_n = 0

    @property
    def routing_id(self):
        return self._own_routing_id

    @routing_id.setter
    def routing_id(self, value):
        self._own_routing_id = value

    async def connect(self, endpoint):
        self._loop = asyncio.get_running_loop()
        self._endpoint = parse_endpoint(endpoint)
        await self._open()

    async def _open(self):
        ep = self._endpoint
        factory = lambda: ZmtpConnection(self, as_server=False)
        if ep.is_ipc:
            await self._loop.create_unix_connection(factory, path=ep.path)
        else:
            await self._loop.create_connection(factory, ep.host, ep.port)

    def _on_connection_made(self, conn):
        super()._on_connection_made(conn)
        self._conn = conn
        for frames in self._pending_out:      # hand queued sends to the (pre-TRAFFIC) conn
            conn.send_message(frames)
        self._pending_out = []

    def _on_ready(self, conn, peer_socket_type, peer_identity):
        self._ready = True
        self._reconnect_n = 0

    def _on_message(self, conn, frames):
        self._recv_q.put_nowait(list(frames))

    def _on_connection_lost(self, conn, exc):
        super()._on_connection_lost(conn, exc)
        if self._conn is conn:
            self._conn = None
            self._ready = False
        if not self._closing and self.reconnect:
            self._loop.create_task(self._reconnect())

    async def _reconnect(self):
        delay = min(self.reconnect_ivl * (2 ** self._reconnect_n), self.reconnect_ivl_max)
        self._reconnect_n += 1
        await asyncio.sleep(delay)
        if self._closing or self._conn is not None:
            return
        try:
            await self._open()
        except Exception:
            if not self._closing and self.reconnect:
                self._loop.create_task(self._reconnect())

    async def send_multipart(self, frames):
        frames = list(frames)
        if self._conn is not None:
            self._conn.send_message(frames)   # conn buffers if still pre-TRAFFIC
        else:
            self._pending_out.append(frames)
