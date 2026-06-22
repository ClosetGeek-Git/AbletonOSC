"""
JsonRpcClient -- a Python client for AbletonOSC's path-based JSON-RPC LOM wire.

Runs OUTSIDE Live, so it uses real pyzmq (`pip install pyzmq`), wire-compatible with
the in-Live pyzmtp ROUTER. It is the Python mirror of the in-house PHP client
(`Closetgeek\\Stemdj\\Lom\\LomBase`): same envelope, same reply demux, same shared
sequence counter for ids/subs.

Reply demux (the DEALER strips its own routing-id; the JSON payload is the last frame):
    {"id":N,"result":...} | {"id":N,"error":{"code","message"}}  -> one-shot RPC waiter
    {"sub":N,"path":...,"value":...}                              -> subscription callback
    {"event":"startup"|...}                                       -> lifecycle callback

Async model mirrors LomBase's promise+callback, but in a synchronous test client the
"promise" is a blocking waiter (threading.Event) resolved on the background recv thread
-- the exact analogue of LomBase resolving a Deferred from its read-stream callback.
Threads are safe here (this is NOT in Live); a single lock serializes the pyzmq socket.

This is the harness for the T1 loopback tier and the T2 live battery.
"""

import json
import threading
import uuid

# Real pyzmq -- the client is out-of-Live, so libzmq is available. Wire-compatible
# with the in-Live pyzmtp ROUTER (proven by pyzmtp's acceptance gate).
import zmq

DEFAULT_PORT = 11000

#--------------------------------------------------------------------------------
# An Ableton Live tick is ~100ms; replies land within a couple of ticks. The default
# RPC timeout factors in transport + tick overhead. Callers tune per-call as needed.
#--------------------------------------------------------------------------------
DEFAULT_TIMEOUT = 2.0
_RECV_IDLE = 0.005


class LomRpcError(Exception):
    """A remote error reply, or a client-side timeout. `code` mirrors the wire code
    (the dispatcher's LomRpcError codes; 408 for a client timeout)."""
    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


class _Err:
    __slots__ = ("exc",)
    def __init__(self, exc):
        self.exc = exc


class _Waiter:
    """One-shot blocking waiter -- the synchronous analogue of LomBase's Deferred."""
    __slots__ = ("_ev", "_value")
    def __init__(self):
        self._ev = threading.Event()
        self._value = None
    def resolve(self, value):
        self._value = value
        self._ev.set()
    def reject(self, exc):
        self._value = _Err(exc)
        self._ev.set()
    def wait(self, timeout):
        if not self._ev.wait(timeout):
            return False, None
        return True, self._value


class JsonRpcClient:
    def __init__(self, hostname="127.0.0.1", port=DEFAULT_PORT, identity=None,
                 endpoint=None, timeout=DEFAULT_TIMEOUT):
        """
        Connect a DEALER to an AbletonOSC ROUTER over ZeroMQ.

        Args:
            hostname / port: the AbletonOSC ROUTER (default tcp://127.0.0.1:11000).
            identity: explicit ZeroMQ routing identity (str/bytes). Stable identity keeps
                      server-side listener routing across reconnects and distinguishes
                      multiple clients on one host. Defaults to a fresh UUID. Must not
                      begin with NUL (pyzmtp reserves 0x00 for auto-ids).
            endpoint: full ZeroMQ endpoint, overriding hostname/port.
            timeout: default per-RPC timeout (seconds).
        """
        self._endpoint = endpoint or ("tcp://%s:%s" % (hostname, port))
        if identity is None:
            identity = "lom-py-" + uuid.uuid4().hex
        if isinstance(identity, str):
            identity = identity.encode()
        self._identity = identity
        self._timeout = timeout

        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.DEALER)
        self._sock.setsockopt(zmq.IDENTITY, self._identity)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.connect(self._endpoint)

        # A ZeroMQ socket is not thread-safe: serialize every op (and fence across
        # the send/recv threads) under this lock.
        self._sock_lock = threading.Lock()

        # Shared sequence counter for ids AND subs, exactly like LomBase ($this->seq):
        # one namespace means an id and a sub never collide.
        self._seq = 0
        self._seq_lock = threading.Lock()

        self._pending = {}   # id  -> _Waiter
        self._subs = {}      # sub -> callback(value, path)
        self._events = {}    # event name -> callback(msg)

        self._stop = threading.Event()
        self._rx = threading.Thread(target=self._recv_loop, daemon=True)
        self._rx.start()

    # ---- ids ---------------------------------------------------------------------
    def _next_id(self):
        with self._seq_lock:
            self._seq += 1
            return self._seq

    # ---- receive path ------------------------------------------------------------
    def _recv_loop(self):
        while not self._stop.is_set():
            frames = None
            with self._sock_lock:
                try:
                    frames = self._sock.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:
                    pass
                except zmq.ZMQError:
                    break
            if frames is not None:
                # DEALER stripped the routing-id; the JSON payload is the last frame.
                # Dispatch OUTSIDE the lock so a callback may send() without deadlocking.
                self._dispatch(frames[-1])
            else:
                self._stop.wait(_RECV_IDLE)

    def _dispatch(self, payload):
        try:
            msg = json.loads(payload.decode("utf-8"))
        except Exception:
            return                                  # never throw on the read path
        if not isinstance(msg, dict):
            return
        if "sub" in msg:                            # unsolicited subscription push
            cb = self._subs.get(msg["sub"])
            if cb is not None:
                cb(msg.get("value"), msg.get("path"))
            return
        if "id" in msg:                             # one-shot RPC reply
            waiter = self._pending.get(msg["id"])
            if waiter is None:
                return                              # late / duplicate -> drop
            if "error" in msg:
                err = msg["error"] or {}
                waiter.reject(LomRpcError(err.get("message", "Lom remote error"),
                                          err.get("code", 0)))
            else:
                waiter.resolve(msg.get("result"))
            return
        if "event" in msg:                          # server lifecycle event
            cb = self._events.get(msg["event"])
            if cb is not None:
                cb(msg)

    # ---- send path ---------------------------------------------------------------
    def _raw_send(self, env):
        data = json.dumps(env).encode("utf-8")
        with self._sock_lock:
            try:
                self._sock.send_multipart([data], zmq.NOBLOCK)
            except zmq.Again:
                pass                                # outbound HWM -> drop, never block

    def rpc(self, env, timeout=None):
        """One-shot RPC. Returns the `result` value; raises LomRpcError on a remote
        error or timeout."""
        rid = self._next_id()
        env = dict(env)
        env["id"] = rid
        waiter = _Waiter()
        self._pending[rid] = waiter
        try:
            self._raw_send(env)
            ok, value = waiter.wait(self._timeout if timeout is None else timeout)
        finally:
            self._pending.pop(rid, None)
        if not ok:
            raise LomRpcError("Lom RPC timeout (id %d)" % rid, 408)
        if isinstance(value, _Err):
            raise value.exc
        return value

    def send(self, env):
        """Fire-and-forget command (no reply awaited)."""
        self._raw_send(env)

    # ---- convenience verbs -------------------------------------------------------
    def get(self, path, timeout=None):
        return self.rpc({"op": "get", "path": path}, timeout)

    def set(self, path, value, timeout=None):
        return self.rpc({"op": "set", "path": path, "value": value}, timeout)

    def call(self, path, args=None, timeout=None):
        env = {"op": "call", "path": path}
        if args:
            env["args"] = args
        return self.rpc(env, timeout)

    def batch(self, ops, timeout=None):
        return self.rpc({"batch": ops}, timeout)

    def ping(self, timeout=None):
        return self.rpc({"op": "ping"}, timeout)

    # ---- subscriptions -----------------------------------------------------------
    def subscribe(self, path, callback):
        """Register a persistent subscription; callback(value, path). Returns a handle."""
        sub = self._next_id()
        self._subs[sub] = callback
        self._raw_send({"op": "subscribe", "sub": sub, "path": path})
        return sub

    def unsubscribe(self, sub):
        if sub in self._subs:
            del self._subs[sub]
            self._raw_send({"op": "unsubscribe", "sub": sub})

    def on(self, event, callback):
        """Register a lifecycle-event callback ('startup', 'error', ...)."""
        self._events[event] = callback

    # ---- lifecycle ---------------------------------------------------------------
    def close(self):
        self._stop.set()
        self._rx.join(timeout=2)
        with self._sock_lock:
            try:
                self._sock.close(linger=0)
            except Exception:
                pass
        try:
            self._ctx.term()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
