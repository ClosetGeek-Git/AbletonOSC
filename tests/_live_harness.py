#--------------------------------------------------------------------------------
# T2 live-battery harness: shared fixtures, helpers, per-property policy, and the
# coverage ledger. Imported by tests/test_live_battery.py.
#
# Connects to an ALREADY-RUNNING Ableton Live (12.2+) with AbletonOSC enabled and the
# committed AbletonOSCTest project loaded (see tests/fixtures/MANIFEST.md). NOT headless.
# Gated by --run-live (conftest.py) so a plain pytest never touches Ableton.
#
# HARD RULE: fixture objects (role "fixture") are never permanently mutated. Every set /
# subscribe-mutation hits a SCRATCH object (track 4 / scene 2 / clip (4,0) / device (4,0) /
# audio clip (1,1)) and is restored; the clean_set guard asserts the set is left pristine.
#--------------------------------------------------------------------------------
import os
import sys
import json
import time

import pytest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))   # import manifest (data module)

import manifest                                                      # noqa: E402
# Package-relative imports (this file is AbletonOSC.tests._live_harness) so the abletonosc
# package keeps its own relative imports (e.g. zmtp_transport's `from ..pyzmtp`) intact.
from ..client.jsonrpc_client import JsonRpcClient, LomRpcError       # noqa: E402
from ..abletonosc.lom_schema import OBJECTS, DIRECT, MIXER, SEND, PARAM, COMPUTED, LIST  # noqa: E402

HOST = os.environ.get("ABLETONOSC_HOST", "127.0.0.1")
PORT = int(os.environ.get("ABLETONOSC_PORT", "11000"))
T = manifest.TARGETS
WAV_PATH = os.path.join(HERE, "fixtures", "audio", "demo_loop.wav")

# The Reverb "Dry/Wet" macro is the last parameter (index num_parameters-1); used for the
# PARAM round-trip on the scratch device. Reverb has 33 params -> index 32.
SCRATCH_PARAM_INDEX = 32


#================================================================================
# Merged manifest (FIXED + CAPTURED)
#================================================================================
def load_merged():
    m = {
        "song": dict(manifest.SONG),
        "tracks": {t["index"]: dict(t) for t in manifest.TRACKS},
        "scenes": {s["index"]: dict(s) for s in manifest.SCENES},
        "clips": {(c["track"], c["scene"]): dict(c) for c in manifest.CLIPS},
        "cues": list(manifest.CUE_POINTS),
        "application": dict(manifest.APPLICATION),
    }
    cap_path = os.path.join(HERE, "fixtures", manifest.CAPTURED_FILE)
    cap = {}
    if os.path.exists(cap_path):
        with open(cap_path) as f:
            cap = json.load(f)
    # merge captured color indices / device / audio values
    for i, tr in cap.get("tracks", {}).items():
        m["tracks"].setdefault(int(i), {}).update({k: v for k, v in tr.items() if v is not None})
    for i, sc in cap.get("scenes", {}).items():
        for k, v in sc.items():
            if v is not None:
                m["scenes"].setdefault(int(i), {})[k] = v
    m["devices"] = cap.get("devices", {})              # keyed "t.d"
    m["audio_clip"] = cap.get("audio_clip", {})        # keyed "t.s"
    return m


#================================================================================
# Coverage ledger
#================================================================================
RUN_LEDGER = {"props": {}, "methods": {}}


def record_prop(obj, prop, mode):
    RUN_LEDGER["props"].setdefault((obj, prop), set()).add(mode)


def record_method(obj, method, classification):
    RUN_LEDGER["methods"][(obj, method)] = classification


# Writables handled by a DEDICATED test (excluded from the generic blind round-trip param,
# but each STILL exercised + records `set` coverage). NONE are silently skipped.
DEDICATED_SET = {
    # high-value song writables — real tests with arm/transport/restore as needed
    ("song", "record_mode"), ("song", "session_record"), ("song", "arrangement_overdub"),
    ("song", "current_song_time"), ("song", "is_ableton_link_enabled"),
    # momentary one-way triggers — assert the API accepts the write + transport restored
    ("song", "nudge_up"), ("song", "nudge_down"), ("song", "back_to_arranger"),
    # group fold/unfold on the scratch GROUP track (Group-Scratch)
    ("track", "fold_state"),
    # conditional scene writables (need their *_enabled flag first)
    ("scene", "tempo"), ("scene", "time_signature_numerator"), ("scene", "time_signature_denominator"),
    # audio-only clip writables (run on the scratch AUDIO clip)
    ("clip", "gain"), ("clip", "warp_mode"), ("clip", "warping"),
    ("clip", "pitch_coarse"), ("clip", "pitch_fine"), ("clip", "ram_mode"),
    # looping-conditional
    ("clip", "position"),
}

# Observable writables that must NOT be force-mutated inside the subscribe test (dangerous,
# momentary, or conditional). The subscribe test asserts only the immediate push for these.
NO_FORCE_SUB = {
    ("song", "record_mode"), ("song", "session_record"), ("song", "arrangement_overdub"),
    ("song", "current_song_time"), ("song", "is_ableton_link_enabled"),
    ("song", "nudge_up"), ("song", "nudge_down"), ("song", "back_to_arranger"),
    ("scene", "tempo"), ("scene", "time_signature_numerator"), ("scene", "time_signature_denominator"),
    ("clip", "position"),
}

# Nothing is silently skipped any more; kept (empty) for the meta-test's accounting.
SKIP_REASONS = {}

# Clip props that must be exercised on an AUDIO clip (the scratch audio clip (1,1)):
# audio-only knobs are listenable + settable there, not on a MIDI clip.
CLIP_AUDIO = {"gain", "gain_display_string", "sample_length", "file_path",
              "warp_mode", "warping", "pitch_coarse", "pitch_fine", "ram_mode"}


#================================================================================
# Path helpers
#================================================================================
def read_leaf(prop):
    if prop.startswith("parameter."):
        return "parameter.0." + prop.split(".", 1)[1]   # read on device param 0 (Operator "Device On")
    if prop == "send":
        return "send.0"
    return prop


def write_leaf(prop):
    if prop == "send":
        return "send.0"
    if prop.startswith("parameter."):
        return "parameter.%d.%s" % (SCRATCH_PARAM_INDEX, prop.split(".", 1)[1])
    return prop


# Read targets: a representative FIXTURE object per arity-bearing object.
READ_BASE = {
    "song": "song",
    "track": "track.%d" % T["midi_track"],          # MIDI-A (has devices, midi clip)
    "scene": "scene.%d" % T["fixture_scene"],        # Scene-A
    "clip_slot": "clip_slot.%d.%d" % T["midi_clip"],
    "clip": "clip.%d.%d" % T["midi_clip"],           # MidiClipA
    "device": "device.%d.%d" % T["device"],          # Operator
    "view": "view",
    "application": "application",
    "midimap": "midimap",
}

# Write/subscribe targets: SCRATCH objects only.
WRITE_BASE = {
    "song": "song",
    "track": "track.%d" % T["scratch_track"],
    "scene": "scene.%d" % T["scratch_scene"],
    "clip_slot": "clip_slot.%d.%d" % T["scratch_slot"],
    "clip": "clip.%d.%d" % T["scratch_slot"],         # scratch MIDI clip
    "device": "device.%d.%d" % (T["scratch_track"], 0),  # scratch Reverb on track 4
    "view": "view",
}


def read_path(obj, prop):
    return READ_BASE[obj] + "." + read_leaf(prop)


def write_path(obj, prop):
    return WRITE_BASE[obj] + "." + write_leaf(prop)


def sub_path(obj, prop):
    """Subscription target: clip audio knobs route to the scratch AUDIO clip (1,1)."""
    if obj == "clip" and prop in CLIP_AUDIO:
        return "clip.%d.%d.%s" % (T["scratch_audio_slot"][0], T["scratch_audio_slot"][1], prop)
    return write_path(obj, prop)


#================================================================================
# Safe round-trip values for non-boolean writables (booleans auto-toggle).
# Anything writable + not-bool + not here + not in SKIP_REASONS -> a no-op set-to-current
# (proves the setter accepts a value) recorded as coverage.
#================================================================================
SAFE_VALUES = {
    ("song", "tempo"): 123.0,
    ("song", "signature_numerator"): 3,
    ("song", "signature_denominator"): 8,
    ("song", "groove_amount"): 0.5,
    ("song", "loop_start"): 2.0,
    ("song", "loop_length"): 8.0,
    ("song", "root_note"): 2,
    ("song", "scale_name"): "Minor",
    ("song", "clip_trigger_quantization"): 5,
    ("song", "midi_recording_quantization"): 5,
    ("track", "name"): "ScratchTmp",
    ("track", "color_index"): 7,
    ("track", "current_monitoring_state"): 1,
    ("track", "volume"): 0.5,
    ("track", "panning"): 0.25,
    ("track", "send"): 0.3,
    ("scene", "name"): "ScratchTmp",
    ("scene", "color_index"): 5,
    ("clip", "name"): "ScratchClip",
    ("clip", "color_index"): 7,
    ("clip", "loop_end"): 2.0,
    ("clip", "loop_start"): 0.0,
    ("clip", "start_marker"): 0.0,
    ("clip", "end_marker"): 2.0,
    ("clip", "launch_mode"): 0,
    ("clip", "launch_quantization"): 0,
    ("clip", "velocity_amount"): 0.5,
    ("device", "parameter.value"): 0.5,
    ("view", "selected_scene"): T["scratch_scene"],
    ("view", "selected_track"): T["scratch_track"],
    ("view", "selected_clip"): list(T["scratch_slot"]),
    ("view", "selected_device"): list(T["device"]),
}


#================================================================================
# Generic helpers
#================================================================================
JSONABLE = (bool, int, float, str, list, dict, type(None))


def is_jsonable(v):
    return isinstance(v, JSONABLE)


def approx_eq(a, b, tol=1e-4):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return a == b
    return a == b


def poll(fn, pred, timeout=2.0, interval=0.03):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if pred(last):
            return last
        time.sleep(interval)
    return last


def roundtrip(client, path, new_value, tol=1e-4):
    """set -> get -> assert -> restore. For booleans pass new_value=None to auto-toggle."""
    prior = client.get(path)
    if new_value is None:                       # bool toggle or no-op
        new_value = (not prior) if isinstance(prior, bool) else prior
    client.set(path, new_value)
    got = poll(lambda: client.get(path), lambda v: approx_eq(v, new_value, tol), timeout=2.0)
    ok = approx_eq(got, new_value, tol)
    # restore regardless
    client.set(path, prior)
    poll(lambda: client.get(path), lambda v: approx_eq(v, prior, tol), timeout=2.0)
    assert ok, "%s round-trip: set %r, got %r" % (path, new_value, got)


class Sub:
    """A subscription capture; use as a context manager so it always unsubscribes."""
    def __init__(self, client, path):
        self.client = client
        self.path = path
        self.events = []
        self.handle = None

    def __enter__(self):
        self.handle = self.client.subscribe(self.path, lambda v, p: self.events.append(v))
        return self

    def __exit__(self, *exc):
        try:
            self.client.unsubscribe(self.handle)
        except Exception:
            pass

    def wait_push(self, timeout=2.0):
        n0 = len(self.events)
        poll(lambda: len(self.events), lambda n: n > n0, timeout=timeout)
        return self.events[-1] if len(self.events) > n0 else None


#================================================================================
# Fixtures (session-scoped)
#================================================================================
@pytest.fixture(scope="session")
def live_client():
    c = JsonRpcClient(hostname=HOST, port=PORT, timeout=10.0)
    try:
        assert c.ping() == "ok", "AbletonOSC not answering ping"
        ver = c.get("application.version")
        assert tuple(ver[:2]) >= (12, 2), "needs Live >= 12.2, got %s" % ver
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def merged():
    return load_merged()


@pytest.fixture(scope="session", autouse=True)
def verify_loaded(live_client):
    """Fail the whole tier fast if the wrong set is open."""
    c = live_client
    names = c.get("song.track_names")
    expected = [t["name"] for t in manifest.TRACKS]
    if (c.get("song.num_tracks") != manifest.SONG["num_tracks"] or names != expected
            or c.get("song.num_scenes") != manifest.SONG["num_scenes"]
            or c.get("clip_slot.0.0.has_clip") is not True
            or c.get("clip_slot.1.0.has_clip") is not True):
        pytest.exit("verify-loaded FAILED: wrong/altered set open (tracks=%s)" % names)
    yield


@pytest.fixture(scope="session")
def scratch_clip(live_client):
    """A scratch MIDI clip at (4,0) for clip writables/observables/notes."""
    c = live_client
    t, s = T["scratch_slot"]
    if c.get("clip_slot.%d.%d.has_clip" % (t, s)):
        c.call("clip_slot.%d.%d.delete_clip" % (t, s))
    c.call("clip_slot.%d.%d.create_clip" % (t, s), [4.0])
    poll(lambda: c.get("clip_slot.%d.%d.has_clip" % (t, s)), lambda v: v is True)
    yield "clip.%d.%d" % (t, s)
    if c.get("clip_slot.%d.%d.has_clip" % (t, s)):
        c.call("clip_slot.%d.%d.delete_clip" % (t, s))


@pytest.fixture(scope="session")
def scratch_device(live_client):
    """A scratch Reverb device on track 4 -> device.4.0 for PARAM / set_parameters / map_cc."""
    c = live_client
    tk = T["scratch_track"]
    while c.get("track.%d.num_devices" % tk) > 0:
        c.call("track.%d.delete_device" % tk, [0])
    c.call("track.%d.load_device" % tk, ["Reverb"])
    poll(lambda: c.get("track.%d.num_devices" % tk), lambda v: v == 1, timeout=4.0)
    yield "device.%d.0" % tk
    while c.get("track.%d.num_devices" % tk) > 0:
        c.call("track.%d.delete_device" % tk, [0])


@pytest.fixture(scope="session")
def scratch_audio_clip(live_client):
    """A scratch AUDIO clip in the empty Audio-A slot (1,1) for audio-only clip writables."""
    c = live_client
    t, s = T["scratch_audio_slot"]
    if c.get("clip_slot.%d.%d.has_clip" % (t, s)):
        c.call("clip_slot.%d.%d.delete_clip" % (t, s))
    c.call("clip_slot.%d.%d.create_audio_clip" % (t, s), [WAV_PATH])
    poll(lambda: c.get("clip_slot.%d.%d.has_clip" % (t, s)), lambda v: v is True, timeout=4.0)
    yield "clip.%d.%d" % (t, s)
    if c.get("clip_slot.%d.%d.has_clip" % (t, s)):
        c.call("clip_slot.%d.%d.delete_clip" % (t, s))


@pytest.fixture(scope="session", autouse=True)
def clean_set(live_client):
    """Backstop: after the whole battery, assert the set was left pristine."""
    yield
    c = live_client
    try:
        c.call("song.stop_playing")
    except Exception:
        pass
    problems = []
    if c.get("song.num_tracks") != manifest.SONG["num_tracks"]:
        problems.append("num_tracks=%s" % c.get("song.num_tracks"))
    if c.get("song.num_scenes") != 3:
        problems.append("num_scenes=%s" % c.get("song.num_scenes"))
    if c.get("track.4.num_devices") != 0:
        problems.append("scratch devices=%s" % c.get("track.4.num_devices"))
    for slot in (T["scratch_slot"], T["scratch_dup_slot"], (4, 2),
                 T["scratch_audio_slot"], (1, 2)):
        if c.get("clip_slot.%d.%d.has_clip" % slot):
            problems.append("clip left in %s" % (slot,))
    if c.get("song.is_playing") is not False:
        problems.append("still playing")
    assert not problems, "set NOT left clean: " + "; ".join(problems)
