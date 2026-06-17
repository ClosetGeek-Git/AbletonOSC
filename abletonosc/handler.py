from ableton.v2.control_surface.component import Component
from typing import Optional, Tuple, Any
import logging
from .osc_server import OSCServer

class AbletonOSCHandler(Component):
    def __init__(self, manager):
        super().__init__()

        self.logger = logging.getLogger("abletonosc")
        self.manager = manager
        self.osc_server: OSCServer = self.manager.osc_server
        #--------------------------------------------------------------------------------
        # Per-listener registry, keyed by (prop, tuple(params), client_addr). Each value
        # is a (callback, unsubscribe) pair, where `unsubscribe` is a thunk that detaches
        # the underlying Live listener. Keying on the registering client's reply address
        # lets multiple clients listen to the same property independently, and lets a
        # vanished client's listeners be torn down by drop_client().
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
    # Generic callbacks
    #--------------------------------------------------------------------------------
    def _call_method(self, target, method, params: Optional[Tuple] = ()):
        self.logger.info("Calling method for %s: %s (params %s)" % (self.class_identifier, method, str(params)))
        getattr(target, method)(*params)

    def _set_property(self, target, prop, params: Tuple) -> None:
        self.logger.info("Setting property for %s: %s (new value %s)" % (self.class_identifier, prop, params[0]))
        setattr(target, prop, params[0])

    def _get_property(self, target, prop, params: Optional[Tuple] = ()) -> Tuple[Any]:
        try:
            value = getattr(target, prop)
        except RuntimeError:
            #--------------------------------------------------------------------------------
            # Gracefully handle errors, which may occur when querying parameters that don't apply
            # to a particular object (e.g. track.fold_state for a non-group track)
            #--------------------------------------------------------------------------------
            value = None
        self.logger.info("Getting property for %s: %s = %s" % (self.class_identifier, prop, value))
        return (value, *params)

    #--------------------------------------------------------------------------------
    # Listener registry
    #
    # All listener kinds (generic property, mixer parameter, device parameter, ...)
    # store a (callback, unsubscribe) pair under a (prop, params, client) key here, so
    # teardown is uniform across _stop_listen, _clear_listeners and drop_client.
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
            # This exception may be thrown when an observer is no longer connected --
            # e.g., when trying to stop listening for a clip property of a clip that has
            # been deleted. Ignore as it is benign.
            #--------------------------------------------------------------------------------
            self.logger.info("Exception whilst removing listener (likely benign): %s" % e)
        return True

    def _clear_listeners(self):
        """
        Clears all listener functions, to prevent listeners continuing to report after a reload.
        """
        for key in list(self.listeners.keys()):
            self._remove_listener(key)

    def drop_client(self, client_addr):
        """
        Remove every listener registered by `client_addr` (called when that client is
        detected unreachable). The client address is the last element of each key.
        """
        for key in list(self.listeners.keys()):
            if key[-1] == client_addr:
                self._remove_listener(key)

    def _start_listen(self, target, prop, params: Optional[Tuple] = (), getter = None) -> None:
        """
        Start listening for the property named `prop` on the Live object `target`.
        `params` is typically a tuple containing the track/clip index.

        Updates are routed to the client that registered the listener (captured at
        registration time), and -- if the request carried an @tag: marker -- prefixed
        with that tag so the client can demultiplex multiple listeners.

        getter can be used for a custom getter when we're accessing native objects
        e.g. in view.py we don't return the selected_scene, but the selected_scene index.

        Args:
            target:
            prop:
            params:
            getter:
        """
        #--------------------------------------------------------------------------------
        # Capture the requesting client and any tag NOW (by value). Later async pushes
        # fire on future ticks when the per-request context is no longer set.
        #--------------------------------------------------------------------------------
        remote_addr = self.osc_server.current_request_addr()
        tag = self.osc_server.current_request_tag()
        osc_address = "/live/%s/get/%s" % (self.class_identifier, prop)

        def property_changed_callback():
            if getter is None:
                value = getattr(target, prop)
            else:
                value = getter(params)
            if type(value) is not tuple:
                value = (value,)
            self.logger.info("Property %s changed of %s %s: %s" % (prop, self.class_identifier, str(params), value))
            payload = (*params, *value)
            if tag is not None:
                payload = (tag, *payload)
            self.osc_server.send(osc_address, payload, remote_addr=remote_addr)

        self.logger.info("Adding listener for %s %s, property: %s" % (self.class_identifier, str(params), prop))
        add_listener_function = getattr(target, "add_%s_listener" % prop)
        add_listener_function(property_changed_callback)

        def unsubscribe():
            getattr(target, "remove_%s_listener" % prop)(property_changed_callback)

        self._add_listener((prop, tuple(params), remote_addr), property_changed_callback, unsubscribe)
        #--------------------------------------------------------------------------------
        # Immediately send the current value
        #--------------------------------------------------------------------------------
        property_changed_callback()

    def _stop_listen(self, target, prop, params: Optional[Tuple[Any]] = ()) -> None:
        key = (prop, tuple(params), self.osc_server.current_request_addr())
        self.logger.info("Removing listener for %s %s, property %s" % (self.class_identifier, str(params), prop))
        if not self._remove_listener(key):
            self.logger.warning("No listener function found for property: %s (%s)" % (prop, str(params)))
