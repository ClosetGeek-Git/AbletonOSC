import argparse
import threading
from pythonosc.udp_client import SimpleUDPClient, OscBundle, OscMessageBuilder
from pythonosc.osc_bundle_builder import OscBundleBuilder
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from typing import Callable, Iterable

REMOTE_PORT = 11000
LOCAL_PORT = 11001

#--------------------------------------------------------------------------------
# Reserved prefixes for the optional request markers.
# Must match CORRELATION_PREFIX / LISTEN_TAG_PREFIX in abletonosc/osc_server.py.
#   @id:  one-shot request/response correlation (query / query_all)
#   @tag: persistent listener tag (start_listen / stop_listen)
#--------------------------------------------------------------------------------
CORRELATION_PREFIX = "@id:"
TAG_PREFIX = "@tag:"

#--------------------------------------------------------------------------------
# Address on which the server reports errors. A correlated request that fails is
# answered here carrying its @id: marker, so query() can raise instead of timing out.
#--------------------------------------------------------------------------------
ERROR_ADDRESS = "/live/error"

#--------------------------------------------------------------------------------
# An Ableton Live tick is 100ms. This constant is typically used for timeouts,
# and factors in some extra time for processing overhead.
#--------------------------------------------------------------------------------
TICK_DURATION = 0.150

class AbletonOSCClient:
    def __init__(self, hostname="127.0.0.1", port=REMOTE_PORT, client_port=LOCAL_PORT):
        """
        Create a client to connect to an Ableton OSC instance.
        Args:
            hostname: The remote host to connect to.
            port: The remote port to connect to. Defaults to 11000, the default AbletonOSC port.
            client_port: The local port to bind to. Defaults to 11001, the default AbletonOSC reply port.
        """
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self.handle_osc)
        self.server = ThreadingOSCUDPServer(("0.0.0.0", client_port), dispatcher)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()
        self.address_handlers = {}
        self.client = SimpleUDPClient(hostname, port)
        self.verbose = False

        #--------------------------------------------------------------------------------
        # Request-correlation state. Each correlated query() allocates a unique
        # "@id:<n>" token and registers a one-shot waiter keyed by that token, so
        # multiple queries can be in flight at once (even to the same address).
        #--------------------------------------------------------------------------------
        self._pending = {}
        self._corr_counter = 0
        self._corr_lock = threading.Lock()

        #--------------------------------------------------------------------------------
        # Persistent listener-tag state. start_listen() allocates a unique "@tag:<n>"
        # token and registers a persistent callback keyed by that token, so a client can
        # demultiplex multiple long-lived listeners (including to the same address).
        #--------------------------------------------------------------------------------
        self._tags = {}
        self._tag_counter = 0

    def handle_osc(self, address, *params):
        # print("Received OSC: %s %s" % (address, params))
        #--------------------------------------------------------------------------------
        # Three-way demux by leading marker, stripping it first:
        #   @id:   -> the one-shot query()/query_all() waiter for that token
        #   @tag:  -> the persistent start_listen() callback for that token
        #   none   -> the usual address-keyed dispatch (legacy listeners, beat, errors)
        #--------------------------------------------------------------------------------
        if params and isinstance(params[0], str):
            marker = params[0]
            if marker.startswith(CORRELATION_PREFIX):
                with self._corr_lock:
                    waiter = self._pending.get(marker)
                if waiter is not None:
                    waiter(address, params[1:])
                return
            elif marker.startswith(TAG_PREFIX):
                with self._corr_lock:
                    entry = self._tags.get(marker)
                if entry is not None and entry["callback"] is not None:
                    entry["callback"](address, params[1:])
                return
        if address in self.address_handlers:
            self.address_handlers[address](address, params)
        if self.verbose:
            print(address, params)

    def stop(self):
        self.server.shutdown()
        self.server_thread.join()
        self.server = None

    def send_bundle(self,
                    messages: list[tuple[str, tuple]]):

        import time
        now = int(time.time())
        bundle_builder = OscBundleBuilder(now)
        for address, params in messages:
            builder = OscMessageBuilder(address=address)
            for param in params:
                builder.add_arg(param)
            msg = builder.build()
            bundle_builder.add_content(msg)
        bundle = bundle_builder.build()
        self.client.send(bundle)

    def send_message(self,
                     address: str,
                     params: Iterable = ()):
        """
        Send a message to the given OSC address on the server.

        Args:
            address (str): The OSC address to send to (e.g. /live/song/set/tempo)
            params (Iterable): Optional list of arguments to pass to the OSC message.
        """
        self.client.send_message(address, params)

    def set_handler(self,
                    address: str,
                    fn: Callable = None):
        """
        Set the handler for the specified OSC message.

        Args:
            address (str): The OSC address to listen for (e.g. /live/song/get/tempo)
            fn (Callable): The function to trigger when a message received.
                           Must accept a two arguments:
                            - str: the OSC address
                            - tuple: the OSC parameters
        """
        self.address_handlers[address] = fn

    def remove_handler(self,
                       address: str):
        """
        Remove the handler for the specified OSC message.

        Args:
            address (str): The OSC address whose handler to remove.
        """
        del self.address_handlers[address]

    def await_message(self,
                      address: str,
                      timeout: float = TICK_DURATION):
        """
        Awaits a reply from the given `address`, and optionally asserts that the function `fn`
        returns True when called with the returned OSC parameters.

        Args:
            address: OSC query (and reply) address
            fn: Optional assertion function
            timeout: Maximum number of seconds to wait for a successful reply

        Returns:
            True if the reply is received within the timeout period and the assertion succeeds,
            False otherwise

        """
        rv = None
        _event = threading.Event()

        def received_response(address, params):
            print("Received response: %s %s" % (address, str(params)))
            nonlocal rv
            nonlocal _event
            rv = params
            _event.set()

        self.set_handler(address, received_response)
        _event.wait(timeout)
        self.remove_handler(address)
        if not _event.is_set():
            raise RuntimeError("No response received to query: %s" % address)
        return rv

    def query(self,
              address: str,
              params: tuple = (),
              timeout: float = TICK_DURATION):
        #--------------------------------------------------------------------------------
        # Correlated query: prepend a unique "@id:<n>" marker and wait for the reply
        # carrying that same marker. This allows multiple queries to be in flight
        # simultaneously, including to the same address, without colliding. The
        # marker is stripped by handle_osc(), so the returned value is unchanged.
        #--------------------------------------------------------------------------------
        with self._corr_lock:
            self._corr_counter += 1
            token = "%s%d" % (CORRELATION_PREFIX, self._corr_counter)

        rv = None
        err = None
        _event = threading.Event()

        def received_response(reply_address, params):
            nonlocal rv
            nonlocal err
            nonlocal _event
            #--------------------------------------------------------------------------------
            # A correlated error is delivered on ERROR_ADDRESS carrying our token, so the
            # query fails fast (raised below) instead of timing out.
            #--------------------------------------------------------------------------------
            if reply_address == ERROR_ADDRESS:
                err = params[0] if params else "error"
            else:
                rv = params
            _event.set()

        with self._corr_lock:
            self._pending[token] = received_response
        try:
            self.send_message(address, (token, *tuple(params)))
            _event.wait(timeout)
        finally:
            with self._corr_lock:
                self._pending.pop(token, None)
        if not _event.is_set():
            raise RuntimeError("No response received to query: %s" % address)
        if err is not None:
            raise RuntimeError("Error querying %s: %s" % (address, err))
        return rv

    def query_all(self,
                  address: str,
                  params: tuple = (),
                  timeout: float = TICK_DURATION):
        """
        Correlated query that collects EVERY reply carrying our marker, for the full
        timeout, rather than returning the first. Intended for wildcard queries (e.g.
        "/live/clip/get/*"), where the server fans out one reply per matching handler.

        Returns:
            A list of (address, params) tuples, one per reply received within the timeout
            (markers already stripped). May be empty if nothing matched.
        """
        import time

        with self._corr_lock:
            self._corr_counter += 1
            token = "%s%d" % (CORRELATION_PREFIX, self._corr_counter)

        results = []

        def received_response(reply_address, params):
            with self._corr_lock:
                results.append((reply_address, params))

        with self._corr_lock:
            self._pending[token] = received_response
        try:
            self.send_message(address, (token, *tuple(params)))
            #--------------------------------------------------------------------------------
            # A wildcard reply count is unknown, so wait the full timeout and collect.
            #--------------------------------------------------------------------------------
            time.sleep(timeout)
        finally:
            with self._corr_lock:
                self._pending.pop(token, None)
                collected = list(results)
        return collected

    def start_listen(self,
                     address: str,
                     params: tuple = (),
                     callback: Callable = None):
        """
        Start a tagged listener. Allocates a unique "@tag:<n>" marker, sends the
        start_listen request with it, and registers `callback` to receive every update
        carrying that marker. Multiple listeners (even to the same address) can be active
        concurrently and are demultiplexed by tag.

        Args:
            address: A start_listen address, e.g. "/live/track/start_listen/volume"
            params: The listener's context params, e.g. (track_index,)
            callback: Called as callback(reply_address, params) for each update, with the
                      tag already stripped.

        Returns:
            An opaque handle (the tag string) to pass to stop_listen().
        """
        with self._corr_lock:
            self._tag_counter += 1
            tag = "%s%d" % (TAG_PREFIX, self._tag_counter)
        stop_address = address.replace("/start_listen/", "/stop_listen/")
        with self._corr_lock:
            self._tags[tag] = {
                "callback": callback,
                "stop_address": stop_address,
                "params": tuple(params),
            }
        self.send_message(address, (tag, *tuple(params)))
        return tag

    def stop_listen(self, handle):
        """
        Stop a tagged listener previously started with start_listen().

        Args:
            handle: The tag returned by start_listen().
        """
        with self._corr_lock:
            entry = self._tags.pop(handle, None)
        if entry is not None:
            self.send_message(entry["stop_address"], (handle, *entry["params"]))

def main(args):
    client = AbletonOSCClient(args.hostname, args.port)
    client.send_message("/live/song/set/tempo", [125.0])
    tempo = client.query("/live/song/get/tempo")
    print("Got song tempo: %.1f" % tempo[0])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Client for AbletonOSC")
    parser.add_argument("--hostname", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=str, default=11000)
    args = parser.parse_args()
    main(args)
