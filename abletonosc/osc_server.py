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
# Optional request-correlation marker.
#
# A client may prepend a single reserved string argument of the form
# "@id:<token>" as the first OSC param of a request. The server strips it before
# dispatching to any handler and re-prepends the identical string to the reply,
# so clients can correlate replies (and command acknowledgements) with concurrent
# in-flight requests. Clients that don't use it are entirely unaffected.
#
# Defined at module level so it survives importlib.reload() on /live/api/reload.
#--------------------------------------------------------------------------------
CORRELATION_PREFIX = "@id:"

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
            if remote_addr is None:
                remote_addr = self._remote_addr
            self._socket.sendto(msg.dgram, remote_addr)
        except BuildError:
            self.logger.error("AbletonOSC: OSC build error: %s" % (traceback.format_exc()))

    def _reply(self, address, rv, corr, remote_addr):
        """
        Send a reply for an incoming message.

        If the request carried a correlation marker (`corr`), it is re-prepended
        so the client can match this reply to the request that caused it. Replies
        are addressed to the host that sent the request (not the shared default
        remote address), so correlated request/response works per-client.
        """
        assert isinstance(rv, tuple)
        if corr is not None:
            rv = (corr, *rv)
        remote_hostname, _ = remote_addr
        response_addr = (remote_hostname, self._response_port)
        self.send(address=address, params=rv, remote_addr=response_addr)

    def process_message(self, message, remote_addr):
        #--------------------------------------------------------------------------------
        # Optional request correlation: strip a leading "@id:<token>" marker (if
        # present) before any handler runs, and re-prepend it to the reply via
        # _reply(). See CORRELATION_PREFIX above.
        #
        # Note: `params` is intentionally left as a list. Do not normalise it to a
        # tuple centrally without also fixing track.py's create_track_callback,
        # which does `[track_index] + params[1:]` (list + slice) and would raise
        # TypeError on a tuple.
        #--------------------------------------------------------------------------------
        params = list(message.params)
        corr = None
        if params and isinstance(params[0], str) and params[0].startswith(CORRELATION_PREFIX):
            corr = params[0]
            params = params[1:]

        if message.address in self._callbacks:
            callback = self._callbacks[message.address]
            rv = callback(params)

            if rv is not None:
                self._reply(message.address, rv, corr, remote_addr)
            elif corr is not None:
                #--------------------------------------------------------------------------------
                # Marker-gated acknowledgement: set/method handlers return None and
                # normally send no reply. When the request is correlated, send an
                # empty-payload ack so the client can confirm completion instead of
                # timing out. Non-correlated commands still produce no reply.
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
                        self._reply(callback_address, rv, corr, remote_addr)
        else:
            self.logger.error("AbletonOSC: Unknown OSC address: %s" % message.address)

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
                # Update the default reply address to the most recent client. Used when
                # sending (e.g) /live/song/beat messages and listen updates.
                #
                # This is slightly ugly and prevents registering listeners from different IPs.
                #--------------------------------------------------------------------------------
                self._remote_addr = (remote_addr[0], OSC_RESPONSE_PORT)
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

    def shutdown(self) -> None:
        """
        Shutdown the server network sockets.
        """
        self._socket.close()
