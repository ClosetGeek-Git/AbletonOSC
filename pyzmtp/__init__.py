"""pyzmtp — pure-Python asyncio ZMTP (ROUTER/DEALER), libzmq-wire-compatible.

Scope: exactly ROUTER (bind) + DEALER (connect) with routing-ids, NULL security, over
tcp:// and ipc://, faithful to the libzmq wire so it interoperates with real libzmq peers.
Disconnect is direct (asyncio connection_lost) — no monitor, no heartbeat.
"""

__version__ = "0.1.0"

from .errors import ZMTPError, ProtocolError, Again, HostUnreachable
from .context import Context, ROUTER, DEALER
from .socket import RouterSocket, DealerSocket, PeerEvent

__all__ = [
    "Context", "ROUTER", "DEALER",
    "RouterSocket", "DealerSocket", "PeerEvent",
    "ZMTPError", "ProtocolError", "Again", "HostUnreachable",
    "__version__",
]
