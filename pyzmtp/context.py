"""Context — a small factory + lifecycle owner for ROUTER/DEALER sockets."""

import asyncio

from .socket import RouterSocket, DealerSocket

# Public socket-type constants (string sentinels — pyzmtp is ROUTER/DEALER only).
ROUTER = "ROUTER"
DEALER = "DEALER"


class Context:
    def __init__(self):
        self._sockets = []

    def socket(self, socket_type):
        if socket_type == ROUTER:
            sock = RouterSocket(self)
        elif socket_type == DEALER:
            sock = DealerSocket(self)
        else:
            raise ValueError("unsupported socket type %r (use ROUTER or DEALER)" % (socket_type,))
        self._sockets.append(sock)
        return sock

    async def term(self):
        await asyncio.gather(*(s.close() for s in self._sockets), return_exceptions=True)
        self._sockets = []
