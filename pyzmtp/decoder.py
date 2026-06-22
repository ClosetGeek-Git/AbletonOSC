"""
Streaming, sans-I/O ZMTP decoder. Fed arbitrary byte chunks via feed(); returns a list
of high-level events. Buffers any partial split (greeting/frame header/body/multipart)
across calls, so feeding 1 byte at a time yields the same event sequence as one big feed.

Events (tuples):
  ("greeting", Greeting)
  ("command", name: bytes, payload: bytes)     # READY / PING / PONG / ERROR / unknown
  ("message", frames: list[bytes])             # one complete multipart message
"""

import struct

from . import protocol
from .protocol import FLAG_MORE, FLAG_LONG, FLAG_COMMAND, GREETING_SIZE


class Decoder:
    def __init__(self):
        self._buf = bytearray()
        self._greeted = False
        self._parts = []          # accumulating frames of the in-progress multipart message

    def feed(self, data):
        if data:
            self._buf += data
        events = []
        while True:
            if not self._greeted:
                if len(self._buf) < GREETING_SIZE:
                    break
                greeting = protocol.parse_greeting(self._buf)
                del self._buf[:GREETING_SIZE]
                self._greeted = True
                events.append(("greeting", greeting))
                continue

            frame = self._next_frame()
            if frame is None:
                break
            flags, body = frame
            if flags & FLAG_COMMAND:
                # Commands are standalone (never part of a multipart message).
                name, payload = protocol.parse_command(body)
                events.append(("command", name, payload))
            else:
                self._parts.append(body)
                if not (flags & FLAG_MORE):
                    events.append(("message", self._parts))
                    self._parts = []
        return events

    def _next_frame(self):
        buf = self._buf
        if len(buf) < 1:
            return None
        flags = buf[0]
        if flags & FLAG_LONG:
            if len(buf) < 9:
                return None
            size = struct.unpack(">Q", buf[1:9])[0]
            hdr = 9
        else:
            if len(buf) < 2:
                return None
            size = buf[1]
            hdr = 2
        if len(buf) < hdr + size:
            return None
        body = bytes(buf[hdr:hdr + size])
        del self._buf[:hdr + size]
        return flags, body
