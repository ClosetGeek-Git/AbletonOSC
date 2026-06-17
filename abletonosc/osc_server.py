from typing import Tuple, Any, Callable
from .constants import OSC_LISTEN_PORT, OSC_RESPONSE_PORT
from ..pythonosc.osc_message import OscMessage, ParseError
from ..pythonosc.osc_bundle import OscBundle
from ..pythonosc.osc_message_builder import OscMessageBuilder, BuildError

import re
import errno
import socket
import logging
import traceback

#--------------------------------------------------------------------------------
# Optional request markers.
#
# A client may prepend a single reserved leading string argument to a request:
#
#   "@id:<token>"   one-shot request/response correlation. The server strips it,
#                   runs the handler, and re-prepends the identical string to the
#                   reply -- including a marker-only acknowledgement for commands
#                   that would otherwise send nothing, and a marker-carrying error
#                   reply when the handler raises or the address is unknown (so the
#                   client fails fast instead of timing out).
#
#   "@tag:<token>"  persistent listener tag. When prepended to a start_listen
#                   request, EVERY update for that listener (the immediate value
#                   push and all later async pushes) carries the identical string
#                   as its first argument, so a client can demultiplex several
#                   long-lived listeners. A @tag: request gets no separate ack --
#                   its immediate tagged value-push is the confirmation.
#
# Only params[0] is inspected, and only one marker is recognised per request.
# Clients that send neither are entirely unaffected.
#
# Defined at module level so they survive importlib.reload() on /live/api/reload.
# NOTE: changes to THIS module only take effect after a full Live restart -- the
# Manager creates the OSCServer once in __init__, and /live/api/reload re-imports
# modules and rebuilds the handlers but keeps the existing server instance.
#--------------------------------------------------------------------------------
CORRELATION_PREFIX = "@id:"
LISTEN_TAG_PREFIX = "@tag:"

#--------------------------------------------------------------------------------
# Reject absurdly long leading strings as markers, to defend the reserved
# namespace against accidental/abusive huge payloads. Such a string is passed
# through to the handler as ordinary data instead of being treated as a marker.
#--------------------------------------------------------------------------------
MAX_MARKER_LENGTH = 64

#--------------------------------------------------------------------------------
# Correlated errors are reported on this address carrying the request's marker, so
# the client's token-keyed waiter resolves and query() can raise. Uncorrelated
# errors are also broadcast here (without a marker) by the logging relay.
#--------------------------------------------------------------------------------
ERROR_ADDRESS = "/live/error"

#--------------------------------------------------------------------------------
# Upper bound on the broadcast client set, to cap memory growth from transient or
# spoofed senders (the server binds 0.0.0.0). Oldest entries are dropped first.
#--------------------------------------------------------------------------------
MAX_KNOWN_CLIENTS = 64

class OSCServer:
    def __init__(self,
                 local_addr: Tuple[str, int] = ('0.0.0.0', OSC_LISTEN_PORT),
                 remote_addr: Tuple[str, int] = ('127.0.0.1', OSC_RESPONSE_PORT)):
        """
        Class that handles OSC server responsibilities, including support for sending
        reply messages.

        Implemented because pythonosc's OSC server causes a beachball when handling
        incoming messages. To investigate, as it would be ultimately better not to have
        to roll our own.

        Args:
            local_addr: Local address and port to listen on.
                        By default, binds to the wildcard address 0.0.0.0, which means listening on
                        every available local IPv4 interface (including 127.0.0.1).
            remote_addr: Remote address to send replies to, by default. Can be overridden in send().
        """

        self._local_addr = local_addr
        self._remote_addr = remote_addr
        self._response_port = remote_addr[1]

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(0)
        self._socket.bind(self._local_addr)
        self._callbacks = {}

        #--------------------------------------------------------------------------------
        # Multi-client async-routing state.
        #
        # Per-request context, valid only during the synchronous dispatch of a single
        # message (safe because everything runs on one ~100ms tick, no threads). Listener
        # registration reads this to capture "who asked" and any @tag: marker, so later
        # async updates route back to the right client.
        #--------------------------------------------------------------------------------
        self._req_remote_addr = None
        self._req_tag = None
        #--------------------------------------------------------------------------------
        # Listener-owning handlers, so a vanished client's subscriptions can be torn down
        # centrally. Reset in clear_handlers() and rebuilt on reload.
        #--------------------------------------------------------------------------------
        self._components = []
        #--------------------------------------------------------------------------------
        # Clients seen, used for true broadcasts (/live/startup, /live/error). Bounded.
        #--------------------------------------------------------------------------------
        self._known_clients = []
        #--------------------------------------------------------------------------------
        # Reply addresses that errored on send during a fan-out; drained (and their
        # listeners torn down) after each process() pass to avoid mutate-during-iterate.
        #--------------------------------------------------------------------------------
        self._dead_pending = set()

        self.logger = logging.getLogger("abletonosc")
        self.logger.info("Starting OSC server (local %s, response port %d)",
                         str(self._local_addr), self._response_port)

    def add_handler(self, address: str, handler: Callable) -> None:
        """
        Add an OSC handler.

        Args:
            address: The OSC address string
            handler: A handler function, with signature:
                     params: Tuple[Any, ...]
        """
        self._callbacks[address] = handler

    def clear_handlers(self) -> None:
        """
        Remove all existing OSC handlers.
        """
        self._callbacks = {}
        #--------------------------------------------------------------------------------
        # Rebuilt handlers re-register themselves via register_component() in init_api().
        #--------------------------------------------------------------------------------
        self._components = []

    def register_component(self, component) -> None:
        """
        Register a listener-owning handler so that its subscriptions for a given client
        can be torn down centrally when that client is detected unreachable.
        """
        if component not in self._components:
            self._components.append(component)

    #--------------------------------------------------------------------------------
    # Per-request context, read by listener registration during dispatch.
    #--------------------------------------------------------------------------------
    def current_request_addr(self):
        """The reply address of the request currently being dispatched, or None."""
        return self._req_remote_addr

    def current_request_tag(self):
        """The @tag:<token> marker of the request currently being dispatched, or None."""
        return self._req_tag

    def send(self,
             address: str,
             params: Tuple = (),
             remote_addr: Tuple[str, int] = None) -> None:
        """
        Send an OSC message.

        Args:
            address: The OSC address (e.g. /frequency)
            params: A tuple of zero or more OSC params
            remote_addr: The remote address to send to, as a 2-tuple (hostname, port).
                         If None, uses the default remote address.
        """
        msg_builder = OscMessageBuilder(address)
        for param in params:
            msg_builder.add_arg(param)

        try:
            msg = msg_builder.build()
        except BuildError:
            self.logger.error("AbletonOSC: OSC build error: %s" % (traceback.format_exc()))
            return

        if remote_addr is None:
            remote_addr = self._remote_addr
        try:
            self._socket.sendto(msg.dgram, remote_addr)
        except OSError:
            #--------------------------------------------------------------------------------
            # The peer is unreachable. (Best-effort: UDP rarely reports this, but when it
            # does -- e.g. an ICMP port-unreachable on a prior datagram -- mark the address
            # so its listeners are reaped after the current process() pass.)
            #--------------------------------------------------------------------------------
            self._dead_pending.add(remote_addr)

    def broadcast(self,
                  address: str,
                  params: Tuple = ()) -> None:
        """
        Send an unsolicited message to every known client (listeners' startup/error/etc).

        Falls back to the default remote address when no client has been seen yet -- e.g.
        /live/startup fires during init before any client has connected.
        """
        targets = list(self._known_clients) if self._known_clients else [self._remote_addr]
        for addr in targets:
            self.send(address, params, remote_addr=addr)

    def _reply(self, address, rv, marker, remote_addr):
        """
        Send a reply for an incoming message.

        If the request carried a marker (@id: or @tag:), it is re-prepended so the client
        can match this reply to the request that caused it. Replies are addressed to the
        host that sent the request (not the shared default remote address), so correlated
        request/response works per-client.
        """
        assert isinstance(rv, tuple)
        if marker is not None:
            rv = (marker, *rv)
        remote_hostname, _ = remote_addr
        response_addr = (remote_hostname, self._response_port)
        self.send(address=address, params=rv, remote_addr=response_addr)

    def _reply_error(self, request_address, exc, marker, remote_addr):
        """
        Send a correlated error reply on ERROR_ADDRESS, carrying the request marker so the
        client's token-keyed waiter resolves and query() can raise instead of timing out.

        Also logs the error, which (under Live) is relayed as an uncorrelated /live/error
        broadcast, exactly as an uncaught handler error would be.
        """
        message = "Error handling %s: %s" % (request_address, exc)
        self.logger.error("AbletonOSC: %s" % message)
        self._reply(ERROR_ADDRESS, (message,), marker, remote_addr)

    def process_message(self, message, remote_addr):
        #--------------------------------------------------------------------------------
        # Strip a single leading marker (@id: correlation OR @tag: listener tag) before
        # any handler runs, and re-prepend it to the reply via _reply(). The per-request
        # context (_req_remote_addr / _req_tag) is published for listener registration and
        # cleared in the finally below.
        #
        # Note: `params` is intentionally left as a list. Do not normalise it to a tuple
        # centrally without also fixing track.py's create_track_callback, which does
        # `[track_index] + params[1:]` (list + slice) and would raise TypeError on a tuple.
        #--------------------------------------------------------------------------------
        params = list(message.params)
        corr = None
        tag = None
        if params and isinstance(params[0], str) and len(params[0]) <= MAX_MARKER_LENGTH:
            if params[0].startswith(CORRELATION_PREFIX):
                corr = params[0]
                params = params[1:]
            elif params[0].startswith(LISTEN_TAG_PREFIX):
                tag = params[0]
                params = params[1:]
        marker = corr if corr is not None else tag

        self._req_remote_addr = (remote_addr[0], self._response_port)
        self._req_tag = tag
        try:
            if message.address in self._callbacks:
                callback = self._callbacks[message.address]
                try:
                    rv = callback(params)
                except Exception as e:
                    #--------------------------------------------------------------------------------
                    # A correlated request that raises gets a marker-carrying error reply so the
                    # client fails fast. An uncorrelated one re-raises into process(), preserving
                    # the existing /live/error logging broadcast.
                    #--------------------------------------------------------------------------------
                    if corr is not None:
                        self._reply_error(message.address, e, corr, remote_addr)
                        return
                    raise

                if rv is not None:
                    self._reply(message.address, rv, marker, remote_addr)
                elif corr is not None:
                    #--------------------------------------------------------------------------------
                    # Marker-gated acknowledgement: set/method handlers return None and normally
                    # send no reply. When the request is correlated (@id:), send an empty-payload
                    # ack so the client can confirm completion instead of timing out. A @tag:
                    # start_listen is confirmed by its immediate tagged push, so gets no ack here.
                    #--------------------------------------------------------------------------------
                    self._reply(message.address, (), corr, remote_addr)
            elif "*" in message.address:
                regex = message.address.replace("*", "[^/]+")
                for callback_address, callback in self._callbacks.items():
                    if re.match(regex, callback_address):
                        try:
                            rv = callback(params)
                        except ValueError:
                            #--------------------------------------------------------------------------------
                            # Don't throw errors for queries that require more arguments
                            # (e.g. /live/track/get/send with no args)
                            #--------------------------------------------------------------------------------
                            continue
                        except AttributeError:
                            #--------------------------------------------------------------------------------
                            # Don't throw errors when trying to create listeners for properties that can't
                            # be listened for (e.g. can_be_armed, is_foldable)
                            #--------------------------------------------------------------------------------
                            continue
                        if rv is not None:
                            self._reply(callback_address, rv, marker, remote_addr)
            else:
                if corr is not None:
                    self._reply_error(message.address, "Unknown OSC address: %s" % message.address,
                                      corr, remote_addr)
                self.logger.error("AbletonOSC: Unknown OSC address: %s" % message.address)
        finally:
            self._req_remote_addr = None
            self._req_tag = None

    def process_bundle(self, bundle, remote_addr):
        for i in bundle:
            if OscBundle.dgram_is_bundle(i.dgram):
                self.process_bundle(i, remote_addr)
            else:
                self.process_message(i, remote_addr)

    def parse_bundle(self, data, remote_addr):
        if OscBundle.dgram_is_bundle(data):
            try:
                bundle = OscBundle(data)
                self.process_bundle(bundle, remote_addr)
            except ParseError:
                self.logger.error("AbletonOSC: Error parsing OSC bundle: %s" % (traceback.format_exc()))
        else:
            try:
                message = OscMessage(data)
                self.process_message(message, remote_addr)
            except ParseError:
                self.logger.error("AbletonOSC: Error parsing OSC message: %s" % (traceback.format_exc()))

    def _note_client(self, remote_addr):
        #--------------------------------------------------------------------------------
        # Record a client (by reply address) for broadcasts. Bounded, FIFO eviction.
        #--------------------------------------------------------------------------------
        addr = (remote_addr[0], self._response_port)
        if addr not in self._known_clients:
            self._known_clients.append(addr)
            if len(self._known_clients) > MAX_KNOWN_CLIENTS:
                self._known_clients.pop(0)

    def _drop_client(self, addr):
        #--------------------------------------------------------------------------------
        # Tear down every listener registered by `addr`, across all components, and forget
        # the client. Called for addresses that errored on send.
        #--------------------------------------------------------------------------------
        for component in list(self._components):
            try:
                component.drop_client(addr)
            except Exception as e:
                self.logger.info("AbletonOSC: Exception dropping client %s: %s" % (str(addr), e))
        if addr in self._known_clients:
            self._known_clients.remove(addr)

    def _drain_dead_clients(self):
        if not self._dead_pending:
            return
        dead = list(self._dead_pending)
        self._dead_pending.clear()
        for addr in dead:
            self._drop_client(addr)

    def process(self) -> None:
        """
        Synchronously process all data queued on the OSC socket.
        """
        try:
            repeats = 0
            while True:
                #--------------------------------------------------------------------------------
                # Loop until no more data is available.
                #--------------------------------------------------------------------------------
                data, remote_addr = self._socket.recvfrom(65536)
                #--------------------------------------------------------------------------------
                # Update the default reply address to the most recent client, and remember the
                # client for broadcasts. The default is still used for legacy single-client
                # fallbacks; per-listener routing uses the captured per-request address.
                #--------------------------------------------------------------------------------
                self._remote_addr = (remote_addr[0], OSC_RESPONSE_PORT)
                self._note_client(remote_addr)
                self.parse_bundle(data, remote_addr)

        except socket.error as e:
            if e.errno == errno.ECONNRESET:
                #--------------------------------------------------------------------------------
                # This benign error seems to occur on startup on Windows
                #--------------------------------------------------------------------------------
                self.logger.warning("AbletonOSC: Non-fatal socket error: %s" % (traceback.format_exc()))
            elif e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
                #--------------------------------------------------------------------------------
                # Another benign networking error, throw when no data is received
                # on a call to recvfrom() on a non-blocking socket
                #--------------------------------------------------------------------------------
                pass
            else:
                #--------------------------------------------------------------------------------
                # Something more serious has happened
                #--------------------------------------------------------------------------------
                self.logger.error("AbletonOSC: Socket error: %s" % (traceback.format_exc()))

        except Exception as e:
            self.logger.error("AbletonOSC: Error handling OSC message: %s" % e)
            self.logger.warning("AbletonOSC: %s" % traceback.format_exc())

        finally:
            #--------------------------------------------------------------------------------
            # Reap listeners belonging to clients that errored on send during this pass.
            #--------------------------------------------------------------------------------
            self._drain_dead_clients()

    def shutdown(self) -> None:
        """
        Shutdown the server network sockets.
        """
        self._socket.close()
