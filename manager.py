from ableton.v2.control_surface import ControlSurface
import Live

from . import abletonosc

import importlib
import traceback
import logging
import os

logger = logging.getLogger("abletonosc")

class Manager(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)

        self.log_level = "info"

        self.handlers = []
        self.midi_mappings = {}

        try:
            self.osc_server = abletonosc.OSCServer()
            self.schedule_message(0, self.tick)

            self.start_logging()
            self.init_api()

            self.show_message("AbletonOSC: Listening for JSON-RPC on port %d" % abletonosc.OSC_LISTEN_PORT)
            logger.info("Started AbletonOSC on address %s" % str(self.osc_server.bound_endpoint))
        except (OSError, abletonosc.TransportBindError) as msg:
            self.show_message("AbletonOSC: Couldn't bind to port %d (%s)" % (abletonosc.OSC_LISTEN_PORT, msg))
            logger.info("Couldn't bind to port %d (%s)" % (abletonosc.OSC_LISTEN_PORT, msg))


    def start_logging(self):
        """
        Start logging to a local logfile (logs/abletonosc.log), and relay error messages
        to connected clients as JSON lifecycle events ({"event":"error","message":...}).
        """
        module_path = os.path.dirname(os.path.realpath(__file__))
        log_dir = os.path.join(module_path, "logs")
        if not os.path.exists(log_dir):
            os.mkdir(log_dir, 0o755)
        log_path = os.path.join(log_dir, "abletonosc.log")
        self.log_file_handler = logging.FileHandler(log_path)
        self.log_file_handler.setLevel(self.log_level.upper())
        formatter = logging.Formatter('(%(asctime)s) [%(levelname)s] %(message)s')
        self.log_file_handler.setFormatter(formatter)
        logger.addHandler(self.log_file_handler)

        class LiveErrorLogHandler(logging.StreamHandler):
            def emit(handler, record):
                message = record.getMessage()
                message = message[message.index(":") + 2:]
                try:
                    self.osc_server.broadcast_json({"event": "error", "message": message})
                except OSError:
                    # If the connection is dead, silently ignore -- nothing more we can do.
                    pass
        self.live_error_handler = LiveErrorLogHandler()
        self.live_error_handler.setLevel(logging.ERROR)
        logger.addHandler(self.live_error_handler)

    def stop_logging(self):
        logger.removeHandler(self.log_file_handler)
        logger.removeHandler(self.live_error_handler)

    def init_api(self):
        #--------------------------------------------------------------------------------
        # The single JSON-RPC handler serves the whole LOM surface from the descriptor
        # (abletonosc/lom_schema.py). Control ops that used to be bespoke OSC addresses
        # (/live/test, /live/api/reload, log level, show_message) are now JSON-RPC methods:
        # {"op":"ping"}, application.reload / get_log_level / set_log_level / show_message.
        #--------------------------------------------------------------------------------
        with self.component_guard():
            self.handlers = [
                abletonosc.JsonRpcHandler(self),
            ]

    def clear_api(self):
        self.osc_server.clear_handlers()
        for handler in self.handlers:
            handler.clear_api()

    def tick(self):
        """
        Called once per 100ms "tick".
        Live's embedded Python implementation does not appear to support threading,
        and beachballs when a thread is started. Instead, this approach allows long-running
        processes such as the RPC server to perform operations.
        """
        logger.debug("Tick...")
        self.osc_server.process()
        self.schedule_message(1, self.tick)

    def reload_imports(self):
        try:
            importlib.reload(abletonosc.handler)
            importlib.reload(abletonosc.lom_schema)
            importlib.reload(abletonosc.jsonrpc)
            importlib.reload(abletonosc.osc_server)
            importlib.reload(abletonosc)
        except Exception:
            exc = traceback.format_exc()
            logging.warning(exc)

        self.clear_api()
        self.init_api()
        logger.info("Reloaded code")

    def disconnect(self):
        self.show_message("Disconnecting...")
        logger.info("Disconnecting...")
        self.stop_logging()
        self.osc_server.shutdown()
        super().disconnect()

    def build_midi_map(self, midi_map_handle):
        """
        Called by Live to build the MIDI map.
        """
        logger.debug("Building MIDI map...")

        for channel, cc in self.midi_mappings.keys():
            parameter = self.midi_mappings[(channel, cc)]
            Live.MidiMap.map_midi_cc(midi_map_handle, parameter, channel, cc, Live.MidiMap.MapMode.absolute, 1)
            logger.debug("Mapped CC %d on channel %d to parameter %s" % (cc, channel, parameter.name))
