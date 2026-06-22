import json
import logging
import collections

import Live

from .handler import AbletonOSCHandler
from . import lom_schema
from .lom_schema import OBJECTS, DIRECT, MIXER, SEND, PARAM, COMPUTED, LIST


class LomRpcError(Exception):
    """A JSON-RPC dispatch error carrying an integer code echoed back to the client."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# (prop, getter, setter|None, listen_obj|None, listen_prop|None)
Accessor = collections.namedtuple("Accessor", ["prop", "getter", "setter", "listen_obj", "listen_prop"])

_NOTE_METHODS = ("get_notes", "add_notes", "remove_notes", "remove_notes_by_id")
_NOTE_SPEC_KEYS = ("pitch", "start_time", "duration", "velocity", "mute",
                   "probability", "velocity_deviation", "release_velocity")


#--------------------------------------------------------------------------------
# JSON-RPC LOM dispatcher, driven entirely by lom_schema (the single source of
# truth). One JSON object per ZMTP frame; replies/pushes route to the client's
# routing-id; subscriptions live in the shared per-client listener registry
# (OSCServer._drop_client reaps them on disconnect).
#
#   {"id":N,"op":"get|set|call","path":"track.0.volume","value":..,"args":[..]}
#   {"id":N,"op":"get","path":["a","b"]}                 multi-read
#   {"id":N,"batch":[{op..},..]}                         one round-trip
#   {"op":"subscribe","sub":N,"path":"track.0.volume"}   persistent (no id)
#   {"op":"unsubscribe","sub":N}
#   {"op":"ping"}                                        readiness probe
# reply: {"id":N,"result":..} | {"id":N,"error":{"code":C,"message":M}}
# push : {"sub":N,"path":..,"value":..}
#--------------------------------------------------------------------------------
class JsonRpcHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "jsonrpc"

    def init_api(self):
        self.osc_server.json_dispatcher = self

    # ---- entry point (called from OSCServer for inbound frames) -------------------
    def handle(self, data, routing_id):
        try:
            env = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data)
        except Exception:
            self.logger.error("AbletonOSC JSON-RPC: malformed frame")
            return
        if not isinstance(env, dict):
            return
        rid = env.get("id")

        if "batch" in env:
            results = []
            for op in env["batch"]:
                try:
                    results.append(self._dispatch_op(op, routing_id))
                except Exception as e:
                    self.logger.info("AbletonOSC JSON-RPC batch op failed: %s" % e)
                    results.append(None)
            if rid is not None:
                self.osc_server.send_json(routing_id, {"id": rid, "result": results})
            return

        try:
            value = self._dispatch_op(env, routing_id)
            if rid is not None:
                self.osc_server.send_json(routing_id, {"id": rid, "result": value})
        except LomRpcError as e:
            if rid is not None:
                self.osc_server.send_json(routing_id, {"id": rid, "error": {"code": e.code, "message": str(e)}})
            else:
                self.logger.error("AbletonOSC JSON-RPC: %s" % e)
        except Exception as e:
            if rid is not None:
                self.osc_server.send_json(routing_id, {"id": rid, "error": {"code": 500, "message": str(e)}})
            else:
                self.logger.error("AbletonOSC JSON-RPC: %s" % e)

    def _dispatch_op(self, op, routing_id):
        kind = op.get("op")
        if kind == "get":
            return self._do_get(op["path"])
        if kind == "set":
            self._do_set(op["path"], op.get("value"))
            return True
        if kind == "call":
            return self._do_call(op["path"], op.get("args", []) or [])
        if kind == "subscribe":
            self._do_subscribe(op["sub"], op["path"], routing_id)
            return True
        if kind == "unsubscribe":
            self._remove_listener((op["sub"], routing_id))
            return True
        if kind == "ping":
            return "ok"
        raise LomRpcError(400, "unknown op: %s" % kind)

    # ---- path / object resolution ------------------------------------------------
    def _split(self, path):
        parts = path.split(".")
        root = parts[0]
        spec = OBJECTS.get(root)
        if spec is None:
            raise LomRpcError(404, "unknown object: %s" % root)
        arity = spec["arity"]
        try:
            idxs = [int(x) for x in parts[1:1 + arity]]
        except ValueError:
            raise LomRpcError(400, "bad index in path: %s" % path)
        leaf = parts[1 + arity:]
        return root, idxs, leaf

    def _resolve_object(self, root, idxs):
        try:
            if root == "song":
                return self.song
            if root == "view":
                return self.song.view
            if root == "application":
                return Live.Application.get_application()
            if root == "track":
                return self.song.tracks[idxs[0]]
            if root == "scene":
                return self.song.scenes[idxs[0]]
            if root == "clip_slot":
                return self.song.tracks[idxs[0]].clip_slots[idxs[1]]
            if root == "clip":
                return self.song.tracks[idxs[0]].clip_slots[idxs[1]].clip
            if root == "device":
                return self.song.tracks[idxs[0]].devices[idxs[1]]
            if root == "midimap":
                return None
        except IndexError:
            raise LomRpcError(404, "index out of range: %s.%s" % (root, ".".join(map(str, idxs))))
        raise LomRpcError(404, "unknown object: %s" % root)

    def _lookup(self, root, leaf):
        """Return (Prop|None, index|None, param_sub|None) for a leaf path."""
        props = OBJECTS[root]["props"]
        if not leaf:
            return None, None, None
        if leaf[0] == "send":
            return props.get("send"), int(leaf[1]), None
        if leaf[0] == "parameter":
            sub = leaf[2] if len(leaf) > 2 else "value"
            return props.get("parameter." + sub), int(leaf[1]), sub
        return props.get(".".join(leaf)), None, None

    def _accessor(self, root, obj, leaf):
        prop, idx, sub = self._lookup(root, leaf)
        if prop is None:
            raise LomRpcError(404, "unknown property: %s.%s" % (root, ".".join(leaf)))
        kind = prop.kind
        if kind == DIRECT:
            name = leaf[0]
            return Accessor(prop, lambda: getattr(obj, name),
                            lambda v: setattr(obj, name, v), obj, name)
        if kind == MIXER:
            param = getattr(obj.mixer_device, leaf[0])
            return Accessor(prop, lambda: param.value,
                            lambda v: setattr(param, "value", v), param, "value")
        if kind == SEND:
            param = obj.mixer_device.sends[idx]
            return Accessor(prop, lambda: param.value,
                            lambda v: setattr(param, "value", v), param, "value")
        if kind == PARAM:
            param = obj.parameters[idx]
            if sub == "value":
                return Accessor(prop, lambda: param.value,
                                lambda v: setattr(param, "value", v), param, "value")
            if sub == "value_string":
                return Accessor(prop, lambda: param.str_for_value(param.value), None, None, None)
            return Accessor(prop, lambda: getattr(param, sub), None, None, None)
        if kind == COMPUTED:
            return self._computed(prop, prop.compute, obj)
        if kind == LIST:
            return Accessor(prop, lambda: self._list(prop.compute, obj), None, None, None)
        raise LomRpcError(500, "unhandled kind: %s" % kind)

    # ---- computed scalars (and the view selected-* listenables) -------------------
    def _computed(self, prop, compute, obj):
        song = self.song
        if compute.startswith("len:"):
            # Count resolves against the property's OWNING object: len:tracks on song,
            # len:devices on a track, len:parameters on a device.
            attr = compute[4:]
            return Accessor(prop, lambda: len(getattr(obj, attr)), None, None, None)
        if compute == "version":
            app = Live.Application.get_application()
            return Accessor(prop, lambda: [app.get_major_version(), app.get_minor_version()], None, None, None)
        if compute == "avg_process_usage":
            app = Live.Application.get_application()
            return Accessor(prop, lambda: app.average_process_usage, None, None, None)
        if compute == "selected_scene":
            return Accessor(prop,
                            lambda: list(song.scenes).index(song.view.selected_scene),
                            lambda v: setattr(song.view, "selected_scene", song.scenes[int(v)]),
                            song.view, "selected_scene")
        if compute == "selected_track":
            return Accessor(prop,
                            lambda: list(song.tracks).index(song.view.selected_track),
                            lambda v: setattr(song.view, "selected_track", song.tracks[int(v)]),
                            song.view, "selected_track")
        if compute == "selected_clip":
            return Accessor(prop,
                            lambda: [list(song.tracks).index(song.view.selected_track),
                                     list(song.scenes).index(song.view.selected_scene)],
                            self._set_selected_clip, None, None)
        if compute == "selected_device":
            return Accessor(prop, self._get_selected_device, self._set_selected_device, None, None)
        raise LomRpcError(500, "unknown computed: %s" % compute)

    def _set_selected_clip(self, v):
        self.song.view.selected_track = self.song.tracks[int(v[0])]
        self.song.view.selected_scene = self.song.scenes[int(v[1])]

    def _get_selected_device(self):
        track = self.song.view.selected_track
        dev = track.view.selected_device
        return [list(self.song.tracks).index(track), list(track.devices).index(dev)]

    def _set_selected_device(self, v):
        device = self.song.tracks[int(v[0])].devices[int(v[1])]
        self.song.view.select_device(device)

    # ---- list/aggregate getters --------------------------------------------------
    def _list(self, compute, obj):
        if compute == "track_names":
            return [t.name for t in obj.tracks]
        if compute == "cue_points":
            return [[cp.name, cp.time] for cp in obj.cue_points]
        if compute.startswith("clips."):
            attr = compute.split(".", 1)[1]
            return [getattr(cs.clip, attr) if cs.clip else None for cs in obj.clip_slots]
        if compute.startswith("arrangement_clips."):
            attr = compute.split(".", 1)[1]
            return [getattr(c, attr) for c in obj.arrangement_clips]
        if compute.startswith("devices."):
            attr = compute.split(".", 1)[1]
            return [getattr(d, attr) for d in obj.devices]
        if compute.startswith("parameters."):
            attr = compute.split(".", 1)[1]
            return [getattr(p, attr) for p in obj.parameters]
        raise LomRpcError(500, "unknown list: %s" % compute)

    # ---- get / set ---------------------------------------------------------------
    def _do_get(self, path):
        if isinstance(path, list):
            return [self._get_one(p) for p in path]
        return self._get_one(path)

    def _get_one(self, path):
        root, idxs, leaf = self._split(path)
        obj = self._resolve_object(root, idxs)
        acc = self._accessor(root, obj, leaf)
        if not acc.prop.readable:
            raise LomRpcError(400, "not readable: %s" % path)
        try:
            value = acc.getter()
        except IndexError:
            raise LomRpcError(404, "index out of range: %s" % path)
        except RuntimeError:
            value = None  # inaccessible property -> null (matches _get_property)
        return self._jsonable(value)

    def _do_set(self, path, value):
        root, idxs, leaf = self._split(path)
        obj = self._resolve_object(root, idxs)
        acc = self._accessor(root, obj, leaf)
        if not acc.prop.writable or acc.setter is None:
            raise LomRpcError(400, "not settable: %s" % path)
        acc.setter(value)

    # ---- call --------------------------------------------------------------------
    def _do_call(self, path, args):
        parts = path.split(".")
        root = parts[0]
        spec = OBJECTS.get(root)
        if spec is None:
            raise LomRpcError(404, "unknown object: %s" % root)
        arity = spec["arity"]
        try:
            idxs = [int(x) for x in parts[1:1 + arity]]
        except ValueError:
            raise LomRpcError(400, "bad index in path: %s" % path)
        method = ".".join(parts[1 + arity:])
        args = list(args)

        if root == "clip" and method in _NOTE_METHODS:
            return self._notes(idxs, method, args)
        if root == "application":
            return self._app_call(method, args)
        if root == "midimap" and method == "map_cc":
            return self._map_cc(args)

        if method not in spec.get("methods", {}):
            raise LomRpcError(404, "unknown method: %s.%s" % (root, method))

        if root == "scene" and method == "fire_selected":
            ss = self.song.view.selected_scene
            if ss:
                ss.fire_as_selected()
            return True
        if root == "song" and method == "name_cue":
            return self._name_cue(args)
        obj = self._resolve_object(root, idxs)
        if root == "clip_slot" and method == "duplicate_clip_to":
            target = self.song.tracks[int(args[0])].clip_slots[int(args[1])]
            obj.duplicate_clip_to(target)
            return True
        if root == "track" and method == "delete_clip":
            obj.clip_slots[int(args[0])].delete_clip()
            return True
        if root == "device" and method == "set_parameters":
            for i, v in enumerate(args):
                obj.parameters[i].value = v
            return True
        if root == "track" and method == "load_device":
            return self._load_device(obj, args)
        if root == "track" and method == "insert_device":
            obj.insert_device(str(args[0]))            # Live 12.3+ native device insert
            return True
        if root == "clip_slot" and method == "create_audio_clip":
            obj.create_audio_clip(str(args[0]))        # Live 12.2+; returns a Clip (discarded)
            return True
        try:
            rv = getattr(obj, method)(*args)
        except IndexError:
            raise LomRpcError(404, "index out of range: %s" % path)
        if rv is None:
            return True
        # Many Live creation/duplication methods (create_*_track, create_scene,
        # duplicate_track, ...) return the new LOM object, which is not serializable.
        # A command doesn't need it -- ack True rather than failing with 415.
        try:
            return self._jsonable(rv)
        except LomRpcError:
            return True

    def _app_call(self, method, args):
        m = self.manager
        if method == "ping":
            return "ok"
        if method == "reload":
            m.reload_imports()
            return True
        if method == "show_message":
            m.show_message(args[0])
            return True
        if method == "get_log_level":
            return m.log_level
        if method == "set_log_level":
            level = args[0]
            if level not in ("debug", "info", "warning", "error", "critical"):
                raise LomRpcError(400, "invalid log level: %s" % level)
            m.log_level = level
            m.log_file_handler.setLevel(level.upper())
            return True
        raise LomRpcError(404, "unknown application method: %s" % method)

    def _map_cc(self, args):
        track, device, parameter, channel, cc = [int(x) for x in args[:5]]
        param = self.song.tracks[track].devices[device].parameters[parameter]
        self.manager.midi_mappings[(channel, cc)] = param
        self.manager.request_rebuild_midi_map()
        return True

    # ---- browser device load (closes AbletonOSC's historical Browser gap) ---------
    def _load_device(self, track, args):
        """
        Load a native device by display name onto `track`. `browser.load_item` has no
        destination argument -- it loads onto the currently selected track -- so we set
        the selection first, then walk the instrument/effect/drum/sound roots for the
        first loadable item whose name matches. (Caveat: if a Drum Rack pad is the
        selection, load_item hot-swaps the pad; we always target a whole track here.)
        """
        name = str(args[0])
        self.song.view.selected_track = track
        browser = Live.Application.get_application().browser
        roots = [browser.instruments, browser.audio_effects]
        for extra in ("drums", "sounds", "midi_effects"):
            root = getattr(browser, extra, None)
            if root is not None:
                roots.append(root)
        target = name.lower()
        for root in roots:
            item = self._find_loadable(root, target)
            if item is not None:
                browser.load_item(item)
                return True
        raise LomRpcError(404, "device not found in browser: %s" % name)

    def _find_loadable(self, node, target_lower):
        children = list(getattr(node, "children", None) or [])
        if not children:
            try:
                children = list(node.iter_children)
            except Exception:
                children = []
        # Loadable leaves at this level first, then recurse into folders.
        for child in children:
            if getattr(child, "is_loadable", False) and child.name.lower() == target_lower:
                return child
        for child in children:
            found = self._find_loadable(child, target_lower)
            if found is not None:
                return found
        return None

    def _name_cue(self, args):
        time = float(args[0])
        name = str(args[1])
        for cp in self.song.cue_points:
            if abs(cp.time - time) < 1e-6:
                cp.name = name
                return True
        raise LomRpcError(404, "no cue point at time %s" % time)

    # ---- MIDI notes (extended dict) ----------------------------------------------
    def _notes(self, idxs, method, args):
        clip = self._resolve_object("clip", idxs)
        if method == "get_notes":
            fp, ps, ft, ts = (args[:4] if len(args) >= 4 else (0, 127, -8192, 16384))
            out = []
            for n in clip.get_notes_extended(fp, ps, ft, ts):
                out.append({
                    "note_id": getattr(n, "note_id", None),
                    "pitch": n.pitch, "start_time": n.start_time, "duration": n.duration,
                    "velocity": n.velocity, "mute": n.mute,
                    "probability": getattr(n, "probability", None),
                    "velocity_deviation": getattr(n, "velocity_deviation", None),
                    "release_velocity": getattr(n, "release_velocity", None),
                })
            return out
        if method == "add_notes":
            # Live 12.4's add_new_notes return value does NOT populate note_id, so we can't
            # read ids off it. Snapshot the clip's note_ids before/after and return the NEW
            # ids, matched back to each input note by (pitch, start_time) so the result is in
            # input order (distinct pitch+time is the normal case; duplicates fall back to
            # FIFO within that key).
            span = (0, 128, -1048576, 2097152)        # whole clip, all pitches
            before = set()
            for n in clip.get_notes_extended(*span):
                nid = getattr(n, "note_id", None)
                if nid is not None:
                    before.add(nid)
            in_notes = list(args[0] if args else [])
            specs = [Live.Clip.MidiNoteSpecification(**{k: d[k] for k in _NOTE_SPEC_KEYS if k in d})
                     for d in in_notes]
            clip.add_new_notes(tuple(specs))
            by_key = {}
            for n in clip.get_notes_extended(*span):
                nid = getattr(n, "note_id", None)
                if nid is None or nid in before:
                    continue
                by_key.setdefault((n.pitch, round(float(n.start_time), 6)), []).append(nid)
            out = []
            for d in in_notes:
                key = (d.get("pitch"), round(float(d.get("start_time", 0.0)), 6))
                ids = by_key.get(key)
                out.append(ids.pop(0) if ids else None)
            return out
        if method == "remove_notes":
            fp, ps, ft, ts = (args[:4] if len(args) >= 4 else (0, 127, -8192, 16384))
            clip.remove_notes_extended(fp, ps, ft, ts)
            return True
        if method == "remove_notes_by_id":
            clip.remove_notes_by_id(list(args[0]) if args else [])
            return True
        raise LomRpcError(400, "unknown clip notes method: %s" % method)

    # ---- subscribe ---------------------------------------------------------------
    def _do_subscribe(self, sub_id, path, routing_id):
        if path == "song.beat":
            self._subscribe_beat(sub_id, routing_id)
            return
        root, idxs, leaf = self._split(path)
        obj = self._resolve_object(root, idxs)
        acc = self._accessor(root, obj, leaf)
        if not acc.prop.observable or acc.listen_obj is None:
            raise LomRpcError(400, "not subscribable: %s" % path)
        getter = acc.getter

        def cb():
            try:
                value = self._jsonable(getter())
            except Exception:
                return
            self.osc_server.send_json(routing_id, {"sub": sub_id, "path": path, "value": value})

        try:
            getattr(acc.listen_obj, "add_%s_listener" % acc.listen_prop)(cb)
        except AttributeError:
            raise LomRpcError(400, "not subscribable: %s" % path)

        def unsubscribe():
            try:
                getattr(acc.listen_obj, "remove_%s_listener" % acc.listen_prop)(cb)
            except Exception as e:
                self.logger.info("JSON-RPC unsubscribe (benign): %s" % e)

        self._add_listener((sub_id, routing_id), cb, unsubscribe)
        cb()  # immediate value push

    def _subscribe_beat(self, sub_id, routing_id):
        state = {"last": -1.0}

        def cb():
            t = self.song.current_song_time
            if t < state["last"] or int(t) > int(state["last"]):
                self.osc_server.send_json(routing_id, {"sub": sub_id, "path": "song.beat", "value": int(t)})
            state["last"] = t

        self.song.add_current_song_time_listener(cb)

        def unsubscribe():
            try:
                self.song.remove_current_song_time_listener(cb)
            except Exception as e:
                self.logger.info("JSON-RPC beat unsubscribe (benign): %s" % e)

        self._add_listener((sub_id, routing_id), cb, unsubscribe)

    # ---- JSON-serialization guard ------------------------------------------------
    def _jsonable(self, value):
        if isinstance(value, (bool, int, float, str)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k): self._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._jsonable(v) for v in value]
        # Live LOM list properties (scale_intervals, etc.) return Vector/tuple-like
        # sequences -- coerce any non-string iterable to a list.
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
            try:
                return [self._jsonable(v) for v in value]
            except TypeError:
                pass
        raise LomRpcError(415, "value of type %s is not JSON-serializable" % type(value).__name__)
