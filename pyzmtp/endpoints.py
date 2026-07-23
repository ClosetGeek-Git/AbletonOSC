"""Endpoint parsing for tcp:// and ipc:// (the two transports we support)."""

from .errors import ZMTPError

# Conservative AF_UNIX sun_path limit (104 on macOS, 108 on Linux); leave headroom.
_IPC_PATH_MAX = 103


class Endpoint:
    __slots__ = ("scheme", "host", "port", "path")

    def __init__(self, scheme, host=None, port=None, path=None):
        self.scheme = scheme
        self.host = host
        self.port = port
        self.path = path

    @property
    def is_ipc(self):
        return self.scheme == "ipc"

    def __repr__(self):
        if self.is_ipc:
            return "Endpoint(ipc, path=%r)" % (self.path,)
        return "Endpoint(tcp, host=%r, port=%r)" % (self.host, self.port)


def parse_endpoint(endpoint):
    """Parse 'tcp://host:port' or 'ipc:///path'. Returns an Endpoint."""
    if endpoint.startswith("tcp://"):
        rest = endpoint[len("tcp://"):]
        host, sep, port = rest.rpartition(":")
        if not sep or not host or not port:
            raise ZMTPError("malformed tcp endpoint: %r" % (endpoint,))
        # Strip IPv6 brackets; map bind-all wildcard.
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host == "*":
            host = "0.0.0.0"
        try:
            port_num = int(port)
        except ValueError:
            raise ZMTPError("malformed tcp port in %r" % (endpoint,))
        return Endpoint("tcp", host=host, port=port_num)

    if endpoint.startswith("ipc://"):
        path = endpoint[len("ipc://"):]
        if not path:
            raise ZMTPError("malformed ipc endpoint: %r" % (endpoint,))
        if len(path.encode("utf-8")) > _IPC_PATH_MAX:
            raise ZMTPError("ipc path too long (>%d bytes): %r" % (_IPC_PATH_MAX, path))
        return Endpoint("ipc", path=path)

    raise ZMTPError("unsupported endpoint scheme: %r (want tcp:// or ipc://)" % (endpoint,))


def format_tcp(host, port):
    return "tcp://%s:%d" % (host, port)
