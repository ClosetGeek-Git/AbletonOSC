"""
One `asyncio.Protocol` per peer connection. Drives the ZMTP greeting + NULL READY
handshake, feeds the streaming decoder, and bridges complete messages / lifecycle to the
owning socket. Disconnect is `connection_lost` — direct and identity-correct, no monitor.
"""

import asyncio

from .decoder import Decoder
from .protocol import (
    encode_greeting, encode_ready, encode_message, encode_pong, parse_metadata,
    MECHANISM_NULL, CMD_READY, CMD_PING, CMD_ERROR,
)
from .errors import ProtocolError

_GREETING = "GREETING"
_TRAFFIC = "TRAFFIC"
_CLOSED = "CLOSED"


class ZmtpConnection(asyncio.Protocol):
    def __init__(self, owner, as_server):
        self._owner = owner
        self._as_server = as_server
        self._decoder = Decoder()
        self._state = _GREETING
        self._transport = None
        self._ready_sent = False
        self._pending_out = []          # messages queued before TRAFFIC
        self.peer_routing_id = None     # assigned by a ROUTER owner
        self.peer_socket_type = None

    #--------------------------------------------------------------------------------
    # asyncio.Protocol callbacks
    #--------------------------------------------------------------------------------
    def connection_made(self, transport):
        self._transport = transport
        self._owner._on_connection_made(self)
        transport.write(encode_greeting(as_server=self._as_server))

    def data_received(self, data):
        try:
            for event in self._decoder.feed(data):
                self._handle(event)
        except ProtocolError:
            self._close_transport()

    def connection_lost(self, exc):
        self._state = _CLOSED
        self._owner._on_connection_lost(self, exc)

    #--------------------------------------------------------------------------------
    # ZMTP event handling
    #--------------------------------------------------------------------------------
    def _handle(self, event):
        kind = event[0]
        if kind == "greeting":
            if event[1].mechanism != MECHANISM_NULL:
                self._close_transport()
                return
            if not self._ready_sent:           # send READY only after the peer's greeting
                rid = None if self._as_server else self._owner._own_routing_id
                self._transport.write(encode_ready(self._owner._socket_type_name, rid))
                self._ready_sent = True
        elif kind == "command":
            _, name, payload = event
            if name == CMD_READY:
                self._on_peer_ready(payload)
            elif name == CMD_PING:
                # PING payload = 2-byte TTL + context; PONG echoes the context only.
                self._transport.write(encode_pong(payload[2:]))
            elif name == CMD_ERROR:
                self._close_transport()
            # PONG / unknown commands: ignore
        elif kind == "message":
            if self._state == _TRAFFIC:
                self._owner._on_message(self, event[1])

    def _on_peer_ready(self, payload):
        meta = parse_metadata(payload)
        self.peer_socket_type = meta.get("socket-type")
        self._state = _TRAFFIC
        self._owner._on_ready(self, self.peer_socket_type, meta.get("identity"))
        for frames in self._pending_out:
            self._transport.write(encode_message(frames))
        self._pending_out = []

    #--------------------------------------------------------------------------------
    # Outbound + teardown
    #--------------------------------------------------------------------------------
    def send_message(self, frames):
        if self._state == _TRAFFIC and self._transport is not None:
            self._transport.write(encode_message(frames))
        else:
            self._pending_out.append(frames)

    def _close_transport(self):
        if self._transport is not None:
            self._transport.close()

    # Public alias used by sockets during shutdown.
    close = _close_transport
