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
# Reserved prefix for the optional request-correlation marker.
# Must match CORRELATION_PREFIX in abletonosc/osc_server.py.
#--------------------------------------------------------------------------------
CORRELATION_PREFIX = "@id:"

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

    def handle_osc(self, address, *params):
        # print("Received OSC: %s %s" % (address, params))
        #--------------------------------------------------------------------------------
        # If this is a correlated reply, route it to the waiting query() by token
        # (not by address), stripping the marker first. Messages without a marker
        # (listeners, beat events, errors, legacy replies) fall through to the
        # usual address-keyed dispatch below.
        #--------------------------------------------------------------------------------
        if params and isinstance(params[0], str) and params[0].startswith(CORRELATION_PREFIX):
            with self._corr_lock:
                waiter = self._pending.get(params[0])
            if waiter is not None:
                waiter(address, params[1:])
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
        _event = threading.Event()

        def received_response(address, params):
            nonlocal rv
            nonlocal _event
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
        return rv

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
