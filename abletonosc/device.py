from typing import Tuple, Any
from .handler import AbletonOSCHandler

class DeviceHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "device"

    def init_api(self):
        def create_device_callback(func, *args, include_ids: bool = False):
            def device_callback(params: Tuple[Any]):
                track_index, device_index = int(params[0]), int(params[1])
                device = self.song.tracks[track_index].devices[device_index]
                if (include_ids):
                    rv = func(device, *args, params[0:])
                else:
                    rv = func(device, *args, params[2:])

                if rv is not None:
                    return (track_index, device_index, *rv)

            return device_callback

        methods = [
        ]
        properties_r = [
            "class_name",
            "name",
            "type"
        ]
        properties_rw = [
        ]

        for method in methods:
            self.osc_server.add_handler("/live/device/%s" % method,
                                        create_device_callback(self._call_method, method))

        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/device/get/%s" % prop,
                                        create_device_callback(self._get_property, prop))
            self.osc_server.add_handler("/live/device/start_listen/%s" % prop,
                                        create_device_callback(self._start_listen, prop))
            self.osc_server.add_handler("/live/device/stop_listen/%s" % prop,
                                        create_device_callback(self._stop_listen, prop))
        for prop in properties_rw:
            self.osc_server.add_handler("/live/device/set/%s" % prop,
                                        create_device_callback(self._set_property, prop))

        #--------------------------------------------------------------------------------
        # Device: Get/set parameter lists
        #--------------------------------------------------------------------------------
        def device_get_num_parameters(device, params: Tuple[Any] = ()):
            return len(device.parameters),

        def device_get_parameters_name(device, params: Tuple[Any] = ()):
            return tuple(parameter.name for parameter in device.parameters)

        def device_get_parameters_value(device, params: Tuple[Any] = ()):
            return tuple(parameter.value for parameter in device.parameters)

        def device_get_parameters_min(device, params: Tuple[Any] = ()):
            return tuple(parameter.min for parameter in device.parameters)

        def device_get_parameters_max(device, params: Tuple[Any] = ()):
            return tuple(parameter.max for parameter in device.parameters)

        def device_get_parameters_is_quantized(device, params: Tuple[Any] = ()):
            return tuple(parameter.is_quantized for parameter in device.parameters)

        def device_set_parameters_value(device, params: Tuple[Any] = ()):
            for index, value in enumerate(params):
                device.parameters[index].value = value

        self.osc_server.add_handler("/live/device/get/num_parameters", create_device_callback(device_get_num_parameters))
        self.osc_server.add_handler("/live/device/get/parameters/name", create_device_callback(device_get_parameters_name))
        self.osc_server.add_handler("/live/device/get/parameters/value", create_device_callback(device_get_parameters_value))
        self.osc_server.add_handler("/live/device/get/parameters/min", create_device_callback(device_get_parameters_min))
        self.osc_server.add_handler("/live/device/get/parameters/max", create_device_callback(device_get_parameters_max))
        self.osc_server.add_handler("/live/device/get/parameters/is_quantized", create_device_callback(device_get_parameters_is_quantized))
        self.osc_server.add_handler("/live/device/set/parameters/value", create_device_callback(device_set_parameters_value))

        #--------------------------------------------------------------------------------
        # Device: Get/set individual parameters
        #--------------------------------------------------------------------------------
        def device_get_parameter_value(device, params: Tuple[Any] = ()):
            # Cast to ints so that we can tolerate floats from interfaces such as TouchOSC
            # that send floats by default.
            # https://github.com/ideoforms/AbletonOSC/issues/33
            param_index = int(params[0])
            return param_index, device.parameters[param_index].value
        
        # Uses str_for_value method to return the UI-friendly version of a parameter value (ex: "2500 Hz")
        def device_get_parameter_value_string(device, params: Tuple[Any] = ()):
            param_index = int(params[0])
            return param_index, device.parameters[param_index].str_for_value(device.parameters[param_index].value)
        
        def device_get_parameter_value_listener(device, params: Tuple[Any] = ()):
            #--------------------------------------------------------------------------------
            # Capture the requesting client and any tag now (see AbletonOSCHandler._start_listen).
            #--------------------------------------------------------------------------------
            remote_addr = self.osc_server.current_request_addr()
            tag = self.osc_server.current_request_tag()
            parameter = device.parameters[params[2]]

            def property_changed_callback():
                value = parameter.value
                self.logger.info("Property %s changed of %s %s: %s" % ('value', 'device parameter', str(params), value))
                value_string = parameter.str_for_value(parameter.value)
                self.logger.info("Property %s changed of %s %s: %s" % ('value_string', 'device parameter', str(params), value_string))
                value_payload = (*params, value)
                string_payload = (*params, value_string)
                if tag is not None:
                    value_payload = (tag, *value_payload)
                    string_payload = (tag, *string_payload)
                self.osc_server.send("/live/device/get/parameter/value", value_payload, remote_addr=remote_addr)
                self.osc_server.send("/live/device/get/parameter/value_string", string_payload, remote_addr=remote_addr)

            self.logger.info("Adding listener for %s %s, property: %s" % ('device parameter', str(params), 'value'))
            parameter.add_value_listener(property_changed_callback)

            def unsubscribe():
                parameter.remove_value_listener(property_changed_callback)

            self._add_listener(('device_parameter_value', tuple(params), remote_addr), property_changed_callback, unsubscribe)

            property_changed_callback()

        def device_get_parameter_remove_value_listener(device, params: Tuple[Any] = ()):
            key = ('device_parameter_value', tuple(params), self.osc_server.current_request_addr())
            self.logger.info("Removing listener for %s %s, property %s" % (self.class_identifier, str(params), 'value'))
            if not self._remove_listener(key):
                self.logger.warning("No listener function found for device parameter value (%s)" % str(params))

        def device_set_parameter_value(device, params: Tuple[Any] = ()):
            param_index, param_value = params[:2]
            param_index = int(param_index)
            device.parameters[param_index].value = param_value

        def device_get_parameter_name(device, params: Tuple[Any] = ()):
            param_index = int(params[0])
            return param_index, device.parameters[param_index].name

        self.osc_server.add_handler("/live/device/get/parameter/value", create_device_callback(device_get_parameter_value))
        self.osc_server.add_handler("/live/device/get/parameter/value_string", create_device_callback(device_get_parameter_value_string))
        self.osc_server.add_handler("/live/device/set/parameter/value", create_device_callback(device_set_parameter_value))
        self.osc_server.add_handler("/live/device/get/parameter/name", create_device_callback(device_get_parameter_name))
        self.osc_server.add_handler("/live/device/start_listen/parameter/value", create_device_callback(device_get_parameter_value_listener, include_ids = True))
        self.osc_server.add_handler("/live/device/stop_listen/parameter/value", create_device_callback(device_get_parameter_remove_value_listener, include_ids = True))
