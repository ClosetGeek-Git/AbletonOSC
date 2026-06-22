from ableton.v2.control_surface.component import Component
import logging
from .osc_server import OSCServer

class AbletonOSCHandler(Component):
    """
    Base for listener-owning RPC handlers (now just JsonRpcHandler). Provides the
    per-client listener registry and central teardown; the JSON-RPC dispatcher builds its
    own subscription closures and calls _add_listener / _remove_listener directly.
    """
    def __init__(self, manager):
        super().__init__()

        self.logger = logging.getLogger("abletonosc")
        self.manager = manager
        self.osc_server: OSCServer = self.manager.osc_server
        #--------------------------------------------------------------------------------
        # Per-listener registry. The JSON-RPC dispatcher keys each subscription by
        # (sub_id, client_routing_id); the value is a (callback, unsubscribe) pair, where
        # `unsubscribe` detaches the underlying Live listener. Keying on the registering
        # client lets multiple clients subscribe independently and lets a vanished client's
        # subscriptions be torn down by drop_client().
        #--------------------------------------------------------------------------------
        self.listeners = {}
        self.class_identifier = None
        self.init_api()
        #--------------------------------------------------------------------------------
        # Register with the server so a dead client's subscriptions can be reaped centrally.
        #--------------------------------------------------------------------------------
        self.osc_server.register_component(self)

    def init_api(self):
        pass

    def clear_api(self):
        self._clear_listeners()

    #--------------------------------------------------------------------------------
    # Listener registry
    #
    # All subscriptions store a (callback, unsubscribe) pair under a key whose last
    # element is the registering client's routing-id, so teardown is uniform across
    # _remove_listener, _clear_listeners and drop_client.
    #--------------------------------------------------------------------------------
    def _add_listener(self, key, callback, unsubscribe):
        if key in self.listeners:
            self._remove_listener(key)
        self.listeners[key] = (callback, unsubscribe)

    def _remove_listener(self, key) -> bool:
        entry = self.listeners.pop(key, None)
        if entry is None:
            return False
        _callback, unsubscribe = entry
        try:
            unsubscribe()
        except Exception as e:
            #--------------------------------------------------------------------------------
            # May be thrown when an observer is no longer connected -- e.g. unsubscribing a
            # clip property of a clip that has been deleted. Benign.
            #--------------------------------------------------------------------------------
            self.logger.info("Exception whilst removing listener (likely benign): %s" % e)
        return True

    def _clear_listeners(self):
        """Detach all listeners, to prevent them reporting after a reload."""
        for key in list(self.listeners.keys()):
            self._remove_listener(key)

    def drop_client(self, client_addr):
        """
        Remove every listener registered by `client_addr` (called when that client is
        detected unreachable). The client's routing-id is the last element of each key.
        """
        for key in list(self.listeners.keys()):
            if key[-1] == client_addr:
                self._remove_listener(key)
