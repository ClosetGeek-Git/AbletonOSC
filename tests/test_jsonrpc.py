#--------------------------------------------------------------------------------
# T0b — JSON-RPC dispatcher battery (headless: no Live, no socket).
#
# Drives the REAL descriptor-driven dispatcher (abletonosc/jsonrpc.py) against a
# generic fake LOM tree, PARAMETRIZED over the descriptor (abletonosc/lom_schema.py)
# so coverage is provably complete and self-maintaining: every property is
# get/set/subscribe-exercised and every method is call-exercised. Adding a
# descriptor entry automatically adds a case. Plus explicit notes / batch / error /
# computed / list / drop_client tests.
#
# T0 proves *routing/coverage* headlessly (no exception, correct reply shape);
# real-value assertions belong to the live tier (T2) against the canonical project.
#--------------------------------------------------------------------------------

import sys
import json
import types

import pytest


def _install_live_stubs():
    def mod(n):
        m = sys.modules.get(n)
        if m is None:
            m = types.ModuleType(n)
            sys.modules[n] = m
        return m

    class _Stub:
        def __init__(self, *a, **k): pass
        def __call__(self, *a, **k): return _Stub()
        def __getattr__(self, n): return _Stub()

    live = mod("Live")
    live.__getattr__ = lambda n: _Stub()

    class _BrowserItem:
        def __init__(self, name, loadable=True, children=()):
            self.name = name
            self.is_loadable = loadable
            self.children = list(children)
        @property
        def iter_children(self):
            return iter(self.children)

    class _Browser:
        def __init__(self):
            self.loaded = []           # names passed to load_item (test inspects this)
            self.instruments = _BrowserItem("Instruments", loadable=False,
                                            children=[_BrowserItem("Operator"), _BrowserItem("Wavetable")])
            self.audio_effects = _BrowserItem("Audio Effects", loadable=False,
                                              children=[_BrowserItem("Reverb"), _BrowserItem("EQ Eight")])
            self.drums = _BrowserItem("Drums", loadable=False, children=[])
            self.sounds = _BrowserItem("Sounds", loadable=False, children=[])
            self.midi_effects = _BrowserItem("MIDI Effects", loadable=False, children=[])
        def load_item(self, item):
            self.loaded.append(item.name)

    class _App:
        average_process_usage = 0.1
        def __init__(self):
            self.browser = _Browser()
        def get_major_version(self): return 12
        def get_minor_version(self): return 0

    # Persist one application instance so browser.load_item state is inspectable.
    _app_singleton = {}

    class _Application:
        @staticmethod
        def get_application():
            if "app" not in _app_singleton:
                _app_singleton["app"] = _App()
            return _app_singleton["app"]

    class _MidiNoteSpec:
        def __init__(self, **kw): self.__dict__.update(kw)

    class _Clip:
        MidiNoteSpecification = _MidiNoteSpec

    live.Application = _Application
    live.Clip = _Clip

    mod("ableton"); v2 = mod("ableton.v2"); cs = mod("ableton.v2.control_surface")
    comp = mod("ableton.v2.control_surface.component")

    class Component:
        def __init__(self, *a, **k): pass

    comp.Component = Component; cs.Component = Component; cs.ControlSurface = Component
    sys.modules["ableton"].v2 = v2; v2.control_surface = cs; cs.component = comp
    fw = mod("_Framework"); enc = mod("_Framework.EncoderElement")

    class EncoderElement: pass
    enc.EncoderElement = EncoderElement; fw.EncoderElement = enc


_install_live_stubs()

from ..abletonosc.jsonrpc import JsonRpcHandler, LomRpcError
from ..abletonosc.lom_schema import OBJECTS, COMPUTED, SEND, PARAM

RID = b"client-1"


#--------------------------------------------------------------------------------
# Generic fake LOM
#--------------------------------------------------------------------------------
class Fake:
    def __init__(self, methods=(), **attrs):
        d = self.__dict__
        d["_v"] = dict(attrs)
        d["_m"] = set(methods)
        d["_ls"] = {}
        d["calls"] = []

    def __getattr__(self, name):
        d = self.__dict__
        if name in d["_v"]:
            return d["_v"][name]
        if name in d["_m"]:
            def fn(*a, **k):
                d["calls"].append((name, a))
                return None
            return fn
        if name.startswith("add_") and name.endswith("_listener"):
            p = name[4:-9]
            return lambda cb: d["_ls"].setdefault(p, []).append(cb)
        if name.startswith("remove_") and name.endswith("_listener"):
            p = name[7:-9]
            def rm(cb):
                ls = d["_ls"].get(p, [])
                if cb in ls:
                    ls.remove(cb)
            return rm
        return 0  # default scalar for any property read

    def __setattr__(self, name, value):
        self.__dict__["_v"][name] = value

    def _emit(self, prop):
        for cb in list(self.__dict__["_ls"].get(prop, [])):
            cb()


class FakeParam(Fake):
    def __init__(self, value=0.0, name="P"):
        super().__init__(value=value, name=name, min=0.0, max=1.0, is_quantized=False)
    def str_for_value(self, v):
        return "%.2f" % v


class FakeClip(Fake):
    def __init__(self, **kw):
        super().__init__(methods=["fire", "stop", "duplicate_loop"], **kw)
        self.__dict__["_notes"] = []
    def get_notes_extended(self, fp, ps, ft, ts):
        return list(self.__dict__["_notes"])
    def add_new_notes(self, specs):
        added = []
        nid = len(self.__dict__["_notes"])
        for s in specs:
            nid += 1
            n = Fake(note_id=nid, pitch=getattr(s, "pitch", 60), start_time=getattr(s, "start_time", 0.0),
                     duration=getattr(s, "duration", 1.0), velocity=getattr(s, "velocity", 100),
                     mute=getattr(s, "mute", False), probability=getattr(s, "probability", 1.0),
                     velocity_deviation=getattr(s, "velocity_deviation", 0.0),
                     release_velocity=getattr(s, "release_velocity", 64))
            self.__dict__["_notes"].append(n)
            added.append(n)
        return added
    def remove_notes_extended(self, fp, ps, ft, ts):
        self.__dict__["_notes"] = []
    def remove_notes_by_id(self, ids):
        self.__dict__["_notes"] = [n for n in self.__dict__["_notes"] if n.note_id not in ids]


def _mixer():
    return Fake(volume=FakeParam(0.8, "Vol"), panning=FakeParam(0.0, "Pan"), sends=[FakeParam(0.1, "A")])


def _device():
    return Fake(class_name="Reverb", name="Reverb", type=1, is_using_compare_preset_b=False,
                parameters=[FakeParam(0.1, "Dry/Wet"), FakeParam(0.2, "Size")])


def _clip_slot(has):
    cs = Fake(methods=["fire", "stop", "create_clip", "delete_clip", "duplicate_clip_to",
                       "create_audio_clip"],
              has_clip=has, has_stop_button=False)
    cs.clip = FakeClip(name="MidiClipA", length=4.0, is_midi_clip=True, color=0) if has else None
    return cs


def _track(name):
    t = Fake(methods=["stop_all_clips", "delete_device", "delete_clip", "insert_device"],
             name=name, color=0, mute=False, solo=False, arm=False, can_be_armed=True)
    t.mixer_device = _mixer()
    t.devices = [_device()]
    t.clip_slots = [_clip_slot(True), _clip_slot(False)]
    t.arrangement_clips = []
    t.view = Fake(selected_device=t.devices[0])
    return t


def build_song():
    song = Fake(methods=list(OBJECTS["song"]["methods"].keys()),
                tempo=120.0, is_playing=False, current_song_time=0.0, metronome=False,
                signature_numerator=4, signature_denominator=4, root_note=0, scale_name="Major")
    song.tracks = [_track("MIDI-A"), _track("Audio-A")]
    song.scenes = [Fake(methods=["fire", "fire_as_selected"], name="Scene-A", color=0, is_empty=False),
                   Fake(methods=["fire", "fire_as_selected"], name="Scene-B", color=1, is_empty=True)]
    song.return_tracks = [_track("A-Reverb")]
    song.cue_points = [Fake(name="Cue-1", time=4.0)]
    view = Fake(methods=["select_device"])
    view.selected_scene = song.scenes[0]
    view.selected_track = song.tracks[0]
    song.view = view
    return song


class FakeOsc:
    def __init__(self):
        self.sent = []
        self.components = []
        self.json_dispatcher = None
    def register_component(self, c): self.components.append(c)
    def send_json(self, rid, obj): self.sent.append(obj)


class FakeManager:
    def __init__(self, osc):
        self.osc_server = osc
        self.log_level = "info"
        self.midi_mappings = {}
        self.log_file_handler = Fake(methods=["setLevel"])
        self.calls = []
    def reload_imports(self): self.calls.append("reload")
    def show_message(self, m): self.calls.append(("show", m))
    def request_rebuild_midi_map(self): self.calls.append("rebuild")


@pytest.fixture
def rig():
    osc = FakeOsc()
    h = JsonRpcHandler(FakeManager(osc))
    h.song = build_song()
    return osc, h


def _send(osc, h, env):
    osc.sent.clear()
    h.handle(json.dumps(env).encode("utf-8"), RID)
    return osc.sent


def _reply(osc, rid):
    for o in osc.sent:
        if o.get("id") == rid:
            return o
    return None


#--------------------------------------------------------------------------------
# Path construction from the descriptor
#--------------------------------------------------------------------------------
def _leaf_for(prop_key):
    # PARAM "parameter.<sub>" -> "parameter.0.<sub>"; SEND "send" -> "send.0".
    if prop_key.startswith("parameter."):
        return "parameter.0." + prop_key.split(".", 1)[1]
    if prop_key == "send":
        return "send.0"
    return prop_key


def _path(obj, leaf):
    arity = OBJECTS[obj]["arity"]
    return ".".join([obj] + ["0"] * arity + [leaf])


def _set_value(p):
    if p.kind == COMPUTED:
        if p.compute in ("selected_scene", "selected_track"):
            return 0
        return [0, 0]
    return 0.5


_PROP_CASES = [(obj, name) for obj, spec in OBJECTS.items() for name in spec["props"]]
_METHOD_CASES = [(obj, name) for obj, spec in OBJECTS.items() for name in spec["methods"]]

_METHOD_ARGS = {
    "create_clip": [4.0], "duplicate_clip_to": [1, 0], "delete_device": [0], "delete_clip": [0],
    "create_audio_track": [-1], "create_midi_track": [-1], "create_return_track": [],
    "create_scene": [-1], "delete_scene": [0], "delete_track": [0], "delete_return_track": [0],
    "duplicate_scene": [0], "duplicate_track": [0], "jump_by": [1.0], "set_or_delete_cue": [],
    "map_cc": [0, 0, 0, 0, 0], "show_message": ["hi"], "set_log_level": ["info"],
    "set_parameters": [0.1, 0.2],
    # creation verbs
    "load_device": ["Operator"], "insert_device": ["Operator"],
    "create_audio_clip": ["/tmp/demo_loop.wav"], "name_cue": [4.0, "Cue-1"],
}


#--------------------------------------------------------------------------------
# Parametrized coverage: every property
#--------------------------------------------------------------------------------
@pytest.mark.parametrize("obj,prop", _PROP_CASES, ids=["%s.%s" % (o, p) for o, p in _PROP_CASES])
def test_property_get_set_subscribe(rig, obj, prop):
    osc, h = rig
    p = OBJECTS[obj]["props"][prop]
    path = _path(obj, _leaf_for(prop))

    if p.readable:
        r = _reply(osc, 1) if _send(osc, h, {"id": 1, "op": "get", "path": path}) else None
        r = _reply(osc, 1)
        assert r is not None and "error" not in r, "get %s -> %s" % (path, r)

    if p.writable:
        _send(osc, h, {"id": 2, "op": "set", "path": path, "value": _set_value(p)})
        r = _reply(osc, 2)
        assert r is not None and "error" not in r, "set %s -> %s" % (path, r)

    if p.observable:
        sent = _send(osc, h, {"op": "subscribe", "sub": 900, "path": path})
        # immediate push proves the listener registered and resolved a value
        assert any(o.get("sub") == 900 for o in sent), "subscribe %s produced no push" % path


@pytest.mark.parametrize("obj,method", _METHOD_CASES, ids=["%s.%s" % (o, m) for o, m in _METHOD_CASES])
def test_method_call(rig, obj, method):
    osc, h = rig
    arity = OBJECTS[obj]["arity"]
    path = ".".join([obj] + ["0"] * arity + [method])
    args = _METHOD_ARGS.get(method, [])
    _send(osc, h, {"id": 3, "op": "call", "path": path, "args": args})
    r = _reply(osc, 3)
    assert r is not None and "error" not in r, "call %s -> %s" % (path, r)


#--------------------------------------------------------------------------------
# Explicit deep tests
#--------------------------------------------------------------------------------
def test_get_mixer_and_param_and_send(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "get", "path": "track.0.volume"})
    assert _reply(osc, 1)["result"] == 0.8
    _send(osc, h, {"id": 2, "op": "get", "path": "track.0.send.0"})
    assert _reply(osc, 2)["result"] == 0.1
    _send(osc, h, {"id": 3, "op": "get", "path": "device.0.0.parameter.1.value"})
    assert _reply(osc, 3)["result"] == 0.2
    _send(osc, h, {"id": 4, "op": "get", "path": "device.0.0.parameter.0.value_string"})
    assert _reply(osc, 4)["result"] == "0.10"

def test_set_mixer_roundtrips(rig):
    osc, h = rig
    _send(osc, h, {"op": "set", "path": "track.0.volume", "value": 0.25})
    assert h.song.tracks[0].mixer_device.volume.value == 0.25

def test_computed_version_and_counts(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "get", "path": "application.version"})
    assert _reply(osc, 1)["result"] == [12, 0]
    _send(osc, h, {"id": 2, "op": "get", "path": "song.num_tracks"})
    assert _reply(osc, 2)["result"] == 2
    _send(osc, h, {"id": 3, "op": "get", "path": "view.selected_scene"})
    assert _reply(osc, 3)["result"] == 0

def test_list_getter(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "get", "path": "track.0.clips.name"})
    assert _reply(osc, 1)["result"] == ["MidiClipA", None]  # slot0 has clip, slot1 empty

def test_multi_get(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "get", "path": ["song.tempo", "track.0.name"]})
    assert _reply(osc, 1)["result"] == [120.0, "MIDI-A"]

def test_notes_extended_roundtrip(rig):
    osc, h = rig
    notes = [{"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100, "mute": False,
              "probability": 0.8, "velocity_deviation": 5.0, "release_velocity": 70}]
    _send(osc, h, {"id": 1, "op": "call", "path": "clip.0.0.add_notes", "args": [notes]})
    ids = _reply(osc, 1)["result"]
    assert ids == [1]
    _send(osc, h, {"id": 2, "op": "call", "path": "clip.0.0.get_notes"})
    got = _reply(osc, 2)["result"]
    assert len(got) == 1
    n = got[0]
    assert n["pitch"] == 60 and n["probability"] == 0.8 and n["note_id"] == 1 \
        and n["velocity_deviation"] == 5.0 and n["release_velocity"] == 70
    _send(osc, h, {"id": 3, "op": "call", "path": "clip.0.0.remove_notes_by_id", "args": [[1]]})
    _send(osc, h, {"id": 4, "op": "call", "path": "clip.0.0.get_notes"})
    assert _reply(osc, 4)["result"] == []

def test_add_notes_returns_real_ids_in_input_order(rig):
    # add_notes must return real ids (read back via get_notes_extended diff), in input order,
    # even though Live 12.4 leaves note_id=None on the add return value.
    osc, h = rig
    notes = [{"pitch": 67, "start_time": 2.0, "duration": 1.0, "velocity": 80},
             {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100}]
    _send(osc, h, {"id": 1, "op": "call", "path": "clip.0.0.add_notes", "args": [notes]})
    ids = _reply(osc, 1)["result"]
    assert ids == [1, 2] and None not in ids        # ids matched back in INPUT order (67@2, 60@0)

def test_batch(rig):
    osc, h = rig
    _send(osc, h, {"id": 9, "batch": [
        {"op": "get", "path": "track.0.name"},
        {"op": "get", "path": "track.1.name"},
        {"op": "set", "path": "song.tempo", "value": 140.0},
    ]})
    assert _reply(osc, 9)["result"] == ["MIDI-A", "Audio-A", True]
    assert h.song.tempo == 140.0

def test_subscribe_push_on_change(rig):
    osc, h = rig
    _send(osc, h, {"op": "subscribe", "sub": 5, "path": "track.0.volume"})
    osc.sent.clear()
    h.song.tracks[0].mixer_device.volume.value = 0.33
    h.song.tracks[0].mixer_device.volume._emit("value")
    assert osc.sent and osc.sent[-1] == {"sub": 5, "path": "track.0.volume", "value": 0.33}

def test_unsubscribe_and_drop_client(rig):
    osc, h = rig
    _send(osc, h, {"op": "subscribe", "sub": 5, "path": "track.0.mute"})
    _send(osc, h, {"op": "unsubscribe", "sub": 5})
    osc.sent.clear()
    h.song.tracks[0]._emit("mute")
    assert osc.sent == []
    # drop_client reaps anything left
    _send(osc, h, {"op": "subscribe", "sub": 6, "path": "track.0.mute"})
    h.drop_client(RID)
    assert len(h.listeners) == 0

def test_ping(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "ping"})
    assert _reply(osc, 1)["result"] == "ok"

def test_errors(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "get", "path": "track.99.volume"})
    assert _reply(osc, 1)["error"]["code"] == 404
    _send(osc, h, {"id": 2, "op": "get", "path": "track.0.bogus_prop"})
    assert _reply(osc, 2)["error"]["code"] == 404
    _send(osc, h, {"id": 3, "op": "frobnicate"})
    assert _reply(osc, 3)["error"]["code"] == 400
    _send(osc, h, {"id": 4, "op": "set", "path": "song.is_playing", "value": True})  # read-only
    assert _reply(osc, 4)["error"]["code"] == 400

def test_application_methods_route_to_manager(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "call", "path": "application.reload"})
    assert _reply(osc, 1)["result"] is True
    assert "reload" in h.manager.calls
    _send(osc, h, {"id": 2, "op": "call", "path": "application.set_log_level", "args": ["debug"]})
    assert h.manager.log_level == "debug"

def test_midimap_routes(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "call", "path": "midimap.map_cc", "args": [0, 0, 1, 2, 64]})
    assert _reply(osc, 1)["result"] is True
    assert (2, 64) in h.manager.midi_mappings

def test_load_device_walks_browser_and_targets_selected_track(rig):
    import Live
    osc, h = rig
    browser = Live.Application.get_application().browser
    browser.loaded.clear()
    _send(osc, h, {"id": 1, "op": "call", "path": "track.1.load_device", "args": ["Reverb"]})
    assert _reply(osc, 1)["result"] is True
    # selected_track set to track 1, and the matching browser item was loaded
    assert h.song.view.selected_track is h.song.tracks[1]
    assert browser.loaded == ["Reverb"]

def test_load_device_unknown_is_404(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "call", "path": "track.0.load_device", "args": ["Nonexistent"]})
    assert _reply(osc, 1)["error"]["code"] == 404

def test_create_audio_clip_calls_lom(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "call", "path": "clip_slot.0.1.create_audio_clip",
                   "args": ["/tmp/demo_loop.wav"]})
    assert _reply(osc, 1)["result"] is True
    cs = h.song.tracks[0].clip_slots[1]
    assert ("create_audio_clip", ("/tmp/demo_loop.wav",)) in cs.__dict__["calls"]

def test_name_cue_matches_by_time(rig):
    osc, h = rig
    _send(osc, h, {"id": 1, "op": "call", "path": "song.name_cue", "args": [4.0, "Intro"]})
    assert _reply(osc, 1)["result"] is True
    assert h.song.cue_points[0].name == "Intro"
    # no cue at that time -> 404
    _send(osc, h, {"id": 2, "op": "call", "path": "song.name_cue", "args": [99.0, "X"]})
    assert _reply(osc, 2)["error"]["code"] == 404


def test_method_returning_lom_object_acks_true(rig):
    # Live's create_*_track / create_scene / duplicate_* return the new LOM object, which
    # is not JSON-serializable. A command call must ack True, not fail with 415.
    osc, h = rig
    h.song.__dict__["_v"]["create_midi_track"] = lambda *a: object()   # non-serializable return
    _send(osc, h, {"id": 1, "op": "call", "path": "song.create_midi_track", "args": [-1]})
    assert _reply(osc, 1)["result"] is True


def test_dispatcher_hooked(rig):
    osc, h = rig
    assert osc.json_dispatcher is h and h in osc.components


#--------------------------------------------------------------------------------
# Completeness meta-test: the parametrized battery is GENERATED from the descriptor,
# so every property and every method is necessarily a case. This guards against a
# regression where the generation is accidentally narrowed (filter/slice) and silently
# stops covering part of the surface.
#--------------------------------------------------------------------------------
def test_battery_covers_every_descriptor_entry():
    expected_props = sum(len(spec["props"]) for spec in OBJECTS.values())
    expected_methods = sum(len(spec["methods"]) for spec in OBJECTS.values())
    assert len(_PROP_CASES) == expected_props, "property battery is not exhaustive"
    assert len(_METHOD_CASES) == expected_methods, "method battery is not exhaustive"
    # Every case is unique (no accidental dupes masking a gap).
    assert len(set(_PROP_CASES)) == expected_props
    assert len(set(_METHOD_CASES)) == expected_methods
    # Sanity floor so a descriptor that silently collapses to near-empty fails loudly.
    assert expected_props >= 140 and expected_methods >= 40
