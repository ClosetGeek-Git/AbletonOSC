from .constants import OSC_ENDPOINT
from .zmtp_transport import ZmtpTransport

import json
import logging
import traceback

#--------------------------------------------------------------------------------
# The plugin's network server.
#
# It speaks ONE wire: a path-based JSON-RPC envelope (one JSON object per ZMTP frame),
# the PHP-first RPC the in-house client (closetgeek/stemdj -> Closetgeek\Stemdj\Lom)
# and the Python JsonRpcClient use. The OSC protocol layer -- vendored pythonosc, the
# @id:/@tag: string markers, the per-object OSC handlers -- has been RETIRED; JSON-RPC's
# native id / sub / error carry correlation, subscriptions, and failures directly.
#
# The transport is a pyzmtp ROUTER on a background asyncio thread (ZmtpTransport),
# bridged to Live's ~100ms tick by thread-safe queues. Each connected client is
# identified by its pyzmtp routing-id -- an opaque ``bytes`` token -- the per-client key
# used everywhere downstream: reply routing, per-listener registration, broadcasts, and
# dead-client teardown.
#
# (The OSCServer name / osc_server.py module / OSC_* constants are kept as the stable
# transport symbols; only the protocol changed. Changes to THIS module take effect only
# after a full Live restart -- the Manager builds the server once and /live api reload
# rebuilds handlers but keeps the running ROUTER + its transport thread.)
#--------------------------------------------------------------------------------

#--------------------------------------------------------------------------------
# Upper bound on the broadcast client set, to cap memory growth. FIFO eviction.
#--------------------------------------------------------------------------------
MAX_KNOWN_CLIENTS = 64


class OSCServer:
    def __init__(self, endpoint: str = OSC_ENDPOINT):
        self._endpoint = endpoint

        #--------------------------------------------------------------------------------
        # Bring up the transport (binds the ROUTER synchronously; raises TransportBindError
        # on failure). `bound_endpoint` is the actually-bound endpoint (resolves ":0").
        #--------------------------------------------------------------------------------
        self._transport = ZmtpTransport(endpoint)
        self.bound_endpoint = self._transport.bound_endpoint

        #--------------------------------------------------------------------------------
        # The JSON-RPC dispatcher (set by JsonRpcHandler.init_api). Every inbound frame is
        # routed to it. Reset on reload by clear_handlers().
        #--------------------------------------------------------------------------------
        self.json_dispatcher = None

        #--------------------------------------------------------------------------------
        # Single-client fallback target (last routing-id seen). Per-listener routing uses
        # the routing-id captured at registration, not this.
        #--------------------------------------------------------------------------------
        self._remote_addr = None

        #--------------------------------------------------------------------------------
        # Listener-owning handlers, so a vanished client's subscriptions can be torn down
        # centrally. Reset in clear_handlers() and rebuilt on reload.
        #--------------------------------------------------------------------------------
        self._components = []

        #--------------------------------------------------------------------------------
        # Clients seen, used for broadcasts (lifecycle events). Bounded.
        #--------------------------------------------------------------------------------
        self._known_clients = []

        self.logger = logging.getLogger("abletonosc")
        self.logger.info("Starting RPC server (pyzmtp ROUTER on %s)", str(self.bound_endpoint))

    def clear_handlers(self) -> None:
        """
        Drop the dispatcher hook and component registrations. Rebuilt handlers re-register
        themselves via register_component() in init_api() (and JsonRpcHandler re-hooks
        json_dispatcher).
        """
        self._components = []
        self.json_dispatcher = None

    def register_component(self, component) -> None:
        """
        Register a listener-owning handler so that its subscriptions for a given client
        can be torn down centrally when that client disconnects.
        """
        if component not in self._components:
            self._components.append(component)

    def send_json(self, remote_addr, obj) -> None:
        """
        Send a single JSON object (one ZMTP frame) to a client by routing-id. Used by the
        JSON-RPC dispatcher for replies and subscription pushes.
        """
        if remote_addr is None:
            return
        try:
            payload = json.dumps(obj).encode("utf-8")
        except (TypeError, ValueError):
            self.logger.error("AbletonOSC: JSON encode error: %s" % (traceback.format_exc()))
            return
        #--------------------------------------------------------------------------------
        # Fire-and-forget onto the transport thread. A send to a vanished/unknown routing-id
        # surfaces async as a HostUnreachable, drained into _drop_client in process().
        #--------------------------------------------------------------------------------
        self._transport.send(remote_addr, payload)

    def broadcast_json(self, obj) -> None:
        """
        Send an unsolicited JSON object to every known client (lifecycle events such as
        {"event":"error","message":...}). Falls back to the single last-seen client when
        no broadcast set has built up yet; best-effort if no client has ever been seen, so
        clients should detect readiness with an active probe ({"op":"ping"}).
        """
        if self._known_clients:
            targets = list(self._known_clients)
        elif self._remote_addr is not None:
            targets = [self._remote_addr]
        else:
            targets = []
        for addr in targets:
            self.send_json(addr, obj)

    def dispatch_frame(self, data, remote_addr) -> None:
        """
        Route an inbound frame to the JSON-RPC dispatcher. Every frame is a JSON envelope;
        if no dispatcher is registered the frame is a no-op error (logged).
        """
        if self.json_dispatcher is None:
            self.logger.error("AbletonOSC: frame received but no JSON-RPC dispatcher is registered")
            return
        try:
            self.json_dispatcher.handle(data, remote_addr)
        except Exception:
            self.logger.error("AbletonOSC: JSON-RPC dispatch error: %s" % (traceback.format_exc()))

    def _note_client(self, remote_addr):
        #--------------------------------------------------------------------------------
        # Record a client (by routing-id) for broadcasts. Bounded, FIFO eviction.
        #--------------------------------------------------------------------------------
        if remote_addr not in self._known_clients:
            self._known_clients.append(remote_addr)
            if len(self._known_clients) > MAX_KNOWN_CLIENTS:
                self._known_clients.pop(0)

    def _drop_client(self, addr):
        #--------------------------------------------------------------------------------
        # Tear down every listener registered by `addr`, across all components, and forget
        # the client. Called when a peer disconnects (pyzmtp event) or a send to it fails.
        #--------------------------------------------------------------------------------
        for component in list(self._components):
            try:
                component.drop_client(addr)
            except Exception as e:
                self.logger.info("AbletonOSC: Exception dropping client %s: %s" % (str(addr), e))
        if addr in self._known_clients:
            self._known_clients.remove(addr)

    def process(self) -> None:
        """
        Drain the transport bridge queues (populated on the background asyncio thread) and
        dispatch -- pure queue operations, no socket I/O, no blocking. Called once per tick.
        """
        try:
            #--------------------------------------------------------------------------------
            # Inbound frames: (routing_id, payload). Update the single-client fallback +
            # broadcast set, then dispatch to the JSON-RPC handler.
            #--------------------------------------------------------------------------------
            for routing_id, frame in self._transport.drain_inbound():
                self._remote_addr = routing_id
                self._note_client(routing_id)
                self.dispatch_frame(frame, routing_id)

            #--------------------------------------------------------------------------------
            # Peer lifecycle: connect -> remember for broadcasts; disconnect -> identity-correct
            # listener teardown.
            #--------------------------------------------------------------------------------
            for evt in self._transport.drain_events():
                if evt.kind == "connect":
                    self._note_client(evt.routing_id)
                elif evt.kind == "disconnect":
                    self._drop_client(evt.routing_id)

            #--------------------------------------------------------------------------------
            # Sends that failed with HostUnreachable (vanished/unknown routing-id): reap.
            #--------------------------------------------------------------------------------
            for routing_id in self._transport.drain_send_failures():
                self._drop_client(routing_id)

        except Exception as e:
            self.logger.error("AbletonOSC: Error processing transport: %s" % e)
            self.logger.warning("AbletonOSC: %s" % traceback.format_exc())

    def shutdown(self) -> None:
        """
        Tear down the transport (closes the ROUTER, stops the asyncio loop, joins the
        thread) deterministically -- no port/thread leak on Live shutdown.
        """
        self._transport.shutdown()
