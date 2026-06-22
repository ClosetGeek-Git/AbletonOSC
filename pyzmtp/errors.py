"""pyzmtp exceptions."""


class ZMTPError(Exception):
    """Base for all pyzmtp errors."""


class ProtocolError(ZMTPError):
    """Malformed or unsupported ZMTP wire data (bad signature, version < 3, etc.)."""


class Again(ZMTPError):
    """A non-blocking operation would block (send queue full / no peer yet)."""


class HostUnreachable(ZMTPError):
    """
    ROUTER send to an unknown / disconnected routing-id. Mirrors libzmq's
    EHOSTUNREACH under ZMQ_ROUTER_MANDATORY.
    """

    def __init__(self, routing_id):
        self.routing_id = routing_id
        super().__init__("no route to routing-id %r" % (routing_id,))
