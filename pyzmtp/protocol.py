"""
ZMTP 3.x wire codec (sans-I/O): greeting, NULL-mechanism READY command, and message/
command frames. Encoders are pure functions; the streaming decoder lives in decoder.py.

Interop notes (verified against RFC 23/37 and libzmq 4.3.5), the load-bearing ones:
  * Greeting bytes 1..8 are NOT validated on receive (libzmq puts a legacy uint64 length
    there); we send zeros, accept anything between the 0xFF / 0x7F sentinels.
  * We advertise major=3, minor=0 but ACCEPT minor 0 or 1 (libzmq sends minor=1). The
    NULL handshake and frame format are identical across 3.0/3.1 for ROUTER/DEALER.
  * Metadata length fields are asymmetric: name-size = 1 byte, value-size = 4 bytes BE.
  * Metadata property names are case-insensitive (we emit canonical, match lowercased).
  * A routing-id (Identity) sent by a peer must not begin with 0x00 (reserved for the
    ROUTER's auto-assigned ids: 0x00 + 4-byte big-endian counter).
  * Minimal length form: short (1-byte len) for <= 255 bytes, long (8-byte BE) otherwise.
"""

import struct

from .errors import ProtocolError

#--------------------------------------------------------------------------------
# Frame flag bits
#--------------------------------------------------------------------------------
FLAG_MORE = 0x01
FLAG_LONG = 0x02
FLAG_COMMAND = 0x04

#--------------------------------------------------------------------------------
# Greeting
#--------------------------------------------------------------------------------
GREETING_SIZE = 64
VERSION_MAJOR = 3
VERSION_MINOR = 0
MECHANISM_NULL = b"NULL"

#--------------------------------------------------------------------------------
# Socket-type metadata values (our scope)
#--------------------------------------------------------------------------------
SOCKET_TYPE_ROUTER = b"ROUTER"
SOCKET_TYPE_DEALER = b"DEALER"

#--------------------------------------------------------------------------------
# Command names
#--------------------------------------------------------------------------------
CMD_READY = b"READY"
CMD_PING = b"PING"
CMD_PONG = b"PONG"
CMD_ERROR = b"ERROR"


class Greeting:
    __slots__ = ("version_major", "version_minor", "mechanism", "as_server")

    def __init__(self, version_major, version_minor, mechanism, as_server):
        self.version_major = version_major
        self.version_minor = version_minor
        self.mechanism = mechanism          # bytes, trailing NULs stripped
        self.as_server = as_server          # int 0/1 (informational for NULL)

    def __repr__(self):
        return "Greeting(v%d.%d, mechanism=%r, as_server=%d)" % (
            self.version_major, self.version_minor, self.mechanism, self.as_server)


def encode_greeting(as_server=False, minor=VERSION_MINOR):
    """The 64-byte ZMTP greeting. Bytes 1..8 are spec 'padding' (we send zeros)."""
    g = bytearray(GREETING_SIZE)
    g[0] = 0xFF
    g[9] = 0x7F
    g[10] = VERSION_MAJOR
    g[11] = minor
    g[12:12 + len(MECHANISM_NULL)] = MECHANISM_NULL   # rest of mechanism field stays 0x00
    g[32] = 1 if as_server else 0
    # bytes 33..63 are filler, already 0x00
    return bytes(g)


def parse_greeting(buf):
    """Parse a 64-byte greeting. `buf` must hold >= GREETING_SIZE bytes."""
    if buf[0] != 0xFF or buf[9] != 0x7F:
        raise ProtocolError("bad ZMTP signature")
    major = buf[10]
    if major < 3:
        raise ProtocolError("unsupported ZMTP major version %d (need >= 3)" % major)
    minor = buf[11]
    mechanism = bytes(buf[12:32]).rstrip(b"\x00")
    as_server = buf[32]
    return Greeting(major, minor, mechanism, as_server)


#--------------------------------------------------------------------------------
# Frames
#--------------------------------------------------------------------------------
def encode_frame(body, more=False, command=False):
    flags = 0
    if more:
        flags |= FLAG_MORE
    if command:
        flags |= FLAG_COMMAND
    n = len(body)
    if n <= 255:
        return bytes((flags, n)) + body
    return bytes((flags | FLAG_LONG,)) + struct.pack(">Q", n) + body


def encode_message(frames):
    """A multipart message: MORE set on every frame but the last."""
    if not frames:
        return encode_frame(b"", more=False)
    out = bytearray()
    last = len(frames) - 1
    for i, f in enumerate(frames):
        out += encode_frame(f, more=(i < last))
    return bytes(out)


#--------------------------------------------------------------------------------
# Commands (a command is a frame with FLAG_COMMAND; body = <name-len><name><payload>)
#--------------------------------------------------------------------------------
def encode_command(name, payload=b""):
    body = bytes((len(name),)) + name + payload
    return encode_frame(body, more=False, command=True)


def parse_command(body):
    """Split a command frame body into (name, payload)."""
    if not body:
        raise ProtocolError("empty command frame")
    n = body[0]
    if 1 + n > len(body):
        raise ProtocolError("truncated command name")
    return bytes(body[1:1 + n]), bytes(body[1 + n:])


#--------------------------------------------------------------------------------
# Metadata (READY): name-size(1) name value-size(4 BE) value, repeated
#--------------------------------------------------------------------------------
def encode_metadata(properties):
    """`properties`: ordered iterable of (name: bytes, value: bytes)."""
    out = bytearray()
    for name, value in properties:
        if not (1 <= len(name) <= 255):
            raise ProtocolError("metadata name length out of range")
        out += bytes((len(name),)) + name + struct.pack(">I", len(value)) + value
    return bytes(out)


def parse_metadata(body):
    """Parse metadata into a dict with lowercased str keys -> bytes values."""
    props = {}
    i = 0
    n = len(body)
    while i < n:
        nlen = body[i]
        i += 1
        if i + nlen > n:
            raise ProtocolError("truncated metadata name")
        name = bytes(body[i:i + nlen])
        i += nlen
        if i + 4 > n:
            raise ProtocolError("truncated metadata value size")
        vlen = struct.unpack(">I", body[i:i + 4])[0]
        i += 4
        if i + vlen > n:
            raise ProtocolError("truncated metadata value")
        value = bytes(body[i:i + vlen])
        i += vlen
        props[name.decode("ascii", "replace").lower()] = value
    return props


def encode_ready(socket_type, routing_id=None):
    """READY command for the NULL mechanism, with Socket-Type and optional Identity."""
    props = [(b"Socket-Type", socket_type)]
    if routing_id:
        if routing_id[0:1] == b"\x00":
            raise ProtocolError("routing-id must not begin with 0x00 (reserved)")
        props.append((b"Identity", routing_id))
    return encode_command(CMD_READY, encode_metadata(props))


def encode_pong(context=b""):
    """PONG echoes up to 16 bytes of the PING context; carries no TTL."""
    return encode_command(CMD_PONG, context[:16])
