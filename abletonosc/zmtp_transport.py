"""
ZmtpTransport: a synchronous, non-blocking, tick-facing adapter over a pyzmtp ROUTER.

pyzmtp is asyncio-native (it spawns no threads and holds no locks; the caller runs the
event loop). AbletonOSC runs single-threaded on Live's ~100ms tick and must never block.
So we run a pyzmtp ROUTER on a private asyncio event loop on a daemon thread, and bridge
it to the tick through thread-safe queue.Queue objects.

THREAD-OWNERSHIP RULE (load-bearing): ONLY the loop thread ever touches a pyzmtp object
(the Context / RouterSocket, recv_multipart / send_multipart, .events, close / term). The
tick thread touches ONLY the queue.Queue objects and run_coroutine_threadsafe /
call_soon_threadsafe. This keeps Live's tick / LOM / Link free of all socket work, and
honours pyzmtp's single-thread contract. libzmq's native I/O thread is replaced here by a
Python asyncio thread, but it only does socket + ZMTP framing (never the LOM), and native
Link/audio threads don't take the GIL -- so Link timing is unaffected.
"""

import asyncio
import logging
import queue
import threading

from ..pyzmtp import Context, ROUTER, HostUnreachable
from .constants import (
    OSC_ENDPOINT,
    START_TIMEOUT_S,
    CLOSE_TIMEOUT_S,
    JOIN_TIMEOUT_S,
    INBOUND_MAXSIZE,
)

logger = logging.getLogger("abletonosc")


class TransportBindError(Exception):
    """Raised when the pyzmtp ROUTER cannot bind its endpoint at startup."""


class ZmtpTransport:
    def __init__(self, endpoint=OSC_ENDPOINT):
        self._endpoint = endpoint
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="abletonosc-zmtp", daemon=True)
        #--------------------------------------------------------------------------------
        # Inbound is bounded (drop-OLDEST on overflow -- control-rate traffic, prefer fresh
        # requests if the tick ever stalls). Events is UNBOUNDED: a dropped disconnect would
        # leak a client's listeners, and at <=10 LAN clients it can't grow pathologically.
        #--------------------------------------------------------------------------------
        self._inbound = queue.Queue(maxsize=INBOUND_MAXSIZE)   # (routing_id, dgram)
        self._events = queue.Queue()                            # PeerEvent(kind, routing_id)
        self._send_failures = queue.Queue()                     # routing_id of HostUnreachable sends

        self._ctx = None
        self._router = None
        self._recv_task = None
        self._events_task = None
        self._stopping = False
        self._closed = False
        self.bound_endpoint = None

        self._thread.start()
        #--------------------------------------------------------------------------------
        # Bind synchronously and surface failure. This blocks the CALLING thread once, at
        # construction (OSCServer.__init__, which runs before the first tick is scheduled --
        # NOT on the 100ms hot path), bounded by START_TIMEOUT_S.
        #--------------------------------------------------------------------------------
        try:
            fut = asyncio.run_coroutine_threadsafe(self._async_start(), self._loop)
            self.bound_endpoint = fut.result(timeout=START_TIMEOUT_S)
        except Exception as e:
            self._stop_loop_thread()
            raise TransportBindError("pyzmtp ROUTER could not bind %s: %r" % (endpoint, e))

    #--------------------------------------------------------------------------------
    # Loop-thread coroutines (producers)
    #--------------------------------------------------------------------------------
    async def _async_start(self):
        self._ctx = Context()
        self._router = self._ctx.socket(ROUTER)
        ep = await self._router.bind(self._endpoint)
        self._recv_task = self._loop.create_task(self._recv_drainer())
        self._events_task = self._loop.create_task(self._events_drainer())
        return ep

    async def _recv_drainer(self):
        try:
            while not self._stopping:
                frames = await self._router.recv_multipart()   # [routing_id, *parts]
                #--------------------------------------------------------------------------------
                # One OSC dgram == one ZMTP frame; frames[-1] is defensive against a stray
                # empty delimiter frame.
                #--------------------------------------------------------------------------------
                self._offer_inbound((frames[0], frames[-1]))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("AbletonOSC: zmtp recv drainer stopped: %r" % (e,))

    async def _events_drainer(self):
        try:
            while not self._stopping:
                evt = await self._router.events.get()
                self._events.put_nowait(evt)                    # unbounded by design
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("AbletonOSC: zmtp events drainer stopped: %r" % (e,))

    def _offer_inbound(self, item):
        try:
            self._inbound.put_nowait(item)
        except queue.Full:
            try:
                self._inbound.get_nowait()                      # drop oldest, make room
            except queue.Empty:
                pass
            try:
                self._inbound.put_nowait(item)
            except queue.Full:
                pass
            logger.warning("AbletonOSC: inbound queue full, dropped oldest message")

    #--------------------------------------------------------------------------------
    # Tick-thread synchronous API (consumers) -- NEVER blocks
    #--------------------------------------------------------------------------------
    def drain_inbound(self):
        return self._drain(self._inbound)

    def drain_events(self):
        return self._drain(self._events)

    def drain_send_failures(self):
        return self._drain(self._send_failures)

    @staticmethod
    def _drain(q):
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                return out

    def send(self, routing_id, dgram):
        #--------------------------------------------------------------------------------
        # Fire-and-forget: schedule the send on the loop and return immediately. We never
        # .result() on the tick (that would block the LOM thread). HostUnreachable (send to
        # a vanished/never-seen id) is surfaced asynchronously via the done-callback ->
        # _send_failures -> drained into _drop_client by the server, preserving the old
        # "errored peer -> reap its listeners" behavior.
        #--------------------------------------------------------------------------------
        if self._closed or self._router is None:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._router.send_multipart([routing_id, dgram]), self._loop)
        fut.add_done_callback(lambda f, rid=routing_id: self._on_send_done(f, rid))

    def _on_send_done(self, fut, routing_id):
        # Runs on the loop thread (the thread that completed the future).
        try:
            exc = fut.exception()
        except Exception:
            return
        if exc is None:
            return
        if isinstance(exc, HostUnreachable):
            self._send_failures.put_nowait(routing_id)
        else:
            logger.info("AbletonOSC: zmtp send failed for %r: %r" % (routing_id, exc))

    def peers(self):
        # Diagnostics/tests only -- reads pyzmtp state on the loop thread, short-bounded.
        if self._closed or self._router is None:
            return []
        try:
            fut = asyncio.run_coroutine_threadsafe(self._async_peers(), self._loop)
            return fut.result(timeout=0.5)
        except Exception:
            return []

    async def _async_peers(self):
        return self._router.peers()

    #--------------------------------------------------------------------------------
    # Deterministic teardown (the LINGER=0 analog -- no port/thread leak on Live reload)
    #--------------------------------------------------------------------------------
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._stopping = True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._async_close(), self._loop)
            fut.result(timeout=CLOSE_TIMEOUT_S)
        except Exception as e:
            logger.info("AbletonOSC: zmtp close error (continuing teardown): %r" % (e,))
        self._stop_loop_thread()

    async def _async_close(self):
        #--------------------------------------------------------------------------------
        # Cancel the drainers (so their awaited recv/events.get raise CancelledError and
        # unwind), then close the socket BEFORE stopping the loop -- close() awaits the
        # server's wait_closed(), which needs the loop still running.
        #--------------------------------------------------------------------------------
        for task in (self._recv_task, self._events_task):
            if task is not None:
                task.cancel()
        if self._router is not None:
            try:
                await self._router.close()
            except Exception:
                pass
        if self._ctx is not None:
            try:
                await self._ctx.term()
            except Exception:
                pass

    def _stop_loop_thread(self):
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=JOIN_TIMEOUT_S)
        try:
            self._loop.close()
        except Exception:
            pass
