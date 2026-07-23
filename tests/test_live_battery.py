#--------------------------------------------------------------------------------
# T2 LIVE battery: exhaustive descriptor-driven coverage against REAL Ableton Live 12.x
# + the committed AbletonOSCTest fixture. Opt-in: every test is @pytest.mark.live and is
# skipped unless `--run-live` is passed (conftest.py). Run:
#   .venv311/bin/python -m pytest tests/test_live_battery.py --run-live -v
#
# Structure: parametrized get/set/subscribe over the descriptor (provably complete) +
# explicit known-value oracle checks + conditional/audio/param round-trips + the method
# battery (FULLY-ASSERTED vs SMOKE) + notes + protocol + a coverage meta-test (runs last)
# + the clean_set backstop. See tests/_live_harness.py for the policy + fixtures.
#--------------------------------------------------------------------------------
import time

import pytest

from ._live_harness import (
    OBJECTS, DIRECT, MIXER, SEND, PARAM, COMPUTED, LIST,
    manifest, T, WAV_PATH, SCRATCH_PARAM_INDEX,
    JsonRpcClient, LomRpcError, HOST, PORT,
    RUN_LEDGER, SKIP_REASONS, DEDICATED_SET, NO_FORCE_SUB, record_prop, record_method, CLIP_AUDIO,
    SAFE_VALUES, read_path, write_path, sub_path, is_jsonable, approx_eq, poll, roundtrip, Sub,
    # fixtures (imported so pytest registers them for this module)
    live_client, merged, verify_loaded, scratch_clip, scratch_device, scratch_audio_clip, clean_set,
)

pytestmark = pytest.mark.live

# Audio-only clip props: read these on the AUDIO clip (1,0), not the MIDI clip.
CLIP_AUDIO_PROPS = {"gain", "gain_display_string", "sample_length", "file_path",
                    "warp_mode", "warping", "pitch_coarse", "pitch_fine"}

PROP_CASES = [(o, p) for o, s in OBJECTS.items() for p in s["props"]]
READABLE = [(o, p) for o, p in PROP_CASES if OBJECTS[o]["props"][p].readable]
# Generic blind round-trip: simple writables only; the DEDICATED_SET writables get their
# own tests (conditional/audio/transport/record) so NOTHING is skipped.
WRITABLE = [(o, p) for o, p in PROP_CASES
            if OBJECTS[o]["props"][p].writable and (o, p) not in DEDICATED_SET]
OBSERVABLE = [(o, p) for o, p in PROP_CASES if OBJECTS[o]["props"][p].observable]
METHOD_CASES = [(o, m) for o, s in OBJECTS.items() for m in s["methods"]]

_ids = lambda cases: ["%s.%s" % (o, p) for o, p in cases]


def _get_read_path(obj, prop):
    if obj == "clip" and prop in CLIP_AUDIO_PROPS:
        return "clip.%d.%d." % T["audio_clip"] + (prop if not prop.startswith("parameter.") else prop)
    return read_path(obj, prop)


#================================================================================
# 1. Every readable property reads without error and returns a JSON-able value
#================================================================================
@pytest.mark.parametrize("obj,prop", READABLE, ids=_ids(READABLE))
def test_prop_get(live_client, obj, prop):
    c = live_client
    if obj == "view" and prop == "selected_device":
        c.set("view.selected_track", T["midi_track"])   # ensure a device-bearing track is selected
    v = c.get(_get_read_path(obj, prop))
    assert is_jsonable(v), "%s.%s returned non-JSON %r" % (obj, prop, type(v))
    record_prop(obj, prop, "get")


#================================================================================
# 2. Known-value oracle checks (manifest FIXED + captured)
#================================================================================
def test_known_song(live_client, merged):
    c = live_client
    s = merged["song"]
    assert c.get("song.tempo") == s["tempo"]
    assert c.get("song.signature_numerator") == s["signature_numerator"]
    assert c.get("song.signature_denominator") == s["signature_denominator"]
    assert c.get("song.metronome") is False
    assert c.get("song.root_note") == s["root_note"]
    assert c.get("song.scale_name") == s["scale_name"]
    assert c.get("song.num_tracks") == manifest.SONG["num_tracks"]
    assert c.get("song.num_scenes") == 3
    assert c.get("song.num_return_tracks") == 1
    assert c.get("song.track_names") == [t["name"] for t in manifest.TRACKS]
    cues = {tuple(x) for x in c.get("song.cue_points")}
    assert ("Cue-1", 4.0) in cues and ("Cue-2", 8.0) in cues


def test_known_tracks(live_client, merged):
    c = live_client
    for i, tr in merged["tracks"].items():
        assert c.get("track.%d.name" % i) == tr["name"]
        if tr.get("color_index") is not None:
            assert c.get("track.%d.color_index" % i) == tr["color_index"]
    assert c.get("track.0.has_midi_input") is True
    assert c.get("track.1.has_audio_input") is True
    assert c.get("track.2.is_foldable") is True       # Group-G
    assert c.get("track.3.is_grouped") is True        # Child-A
    assert c.get("track.5.is_foldable") is True        # Group-Scratch
    assert c.get("track.6.is_grouped") is True         # Scratch-Child
    assert c.get("track.0.num_devices") == 2
    assert c.get("track.4.num_devices") == 0
    assert c.get("track.0.devices.name") == ["Operator", "Reverb"]


def test_known_clips(live_client, merged):
    c = live_client
    assert c.get("clip.0.0.name") == "MidiClipA"
    assert c.get("clip.0.0.is_midi_clip") is True
    assert c.get("clip.0.0.length") == 4.0
    assert c.get("clip.0.0.looping") is True
    assert c.get("clip.1.0.name") == "AudioClipA"
    assert c.get("clip.1.0.is_audio_clip") is True
    assert c.get("clip.1.0.warping") is True
    assert c.get("clip.1.0.file_path").endswith("demo_loop.wav")
    ac = merged["audio_clip"].get("1.0", {})
    if ac.get("sample_length") is not None:
        assert c.get("clip.1.0.sample_length") == ac["sample_length"]
    if ac.get("gain") is not None:
        assert approx_eq(c.get("clip.1.0.gain"), ac["gain"], 1e-5)


def test_known_devices(live_client, merged):
    c = live_client
    for key, exp in merged["devices"].items():
        t, d = (int(x) for x in key.split("."))
        base = "device.%d.%d" % (t, d)
        assert c.get(base + ".class_name") == exp["class_name"]
        assert c.get(base + ".type") == exp["type"]
        assert c.get(base + ".num_parameters") == exp["num_parameters"]
        assert c.get(base + ".parameters.name") == exp["parameter_names"]
        assert c.get(base + ".parameters.min") == exp["parameter_mins"]
        assert c.get(base + ".parameters.max") == exp["parameter_maxes"]
        assert c.get(base + ".parameter.0.name") == exp["parameter_names"][0]


def test_known_scene_b(live_client):
    c = live_client
    assert c.get("scene.0.name") == "Scene-A"
    assert c.get("scene.1.name") == "Scene-B"
    assert c.get("scene.1.tempo_enabled") is True
    assert c.get("scene.1.tempo") == 126.0
    assert c.get("scene.1.time_signature_enabled") is True
    assert c.get("scene.1.time_signature_numerator") == 3
    assert c.get("scene.1.time_signature_denominator") == 4
    assert c.get("scene.0.is_empty") is False
    assert c.get("scene.1.is_empty") is True


def test_known_clip_notes(live_client):
    c = live_client
    notes = sorted(c.call("clip.0.0.get_notes"), key=lambda n: n["start_time"])
    assert [n["pitch"] for n in notes] == [60, 64, 67]
    assert [n["velocity"] for n in notes] == [100, 90, 80]
    assert all(n["note_id"] is not None for n in notes)


def test_known_application(live_client):
    c = live_client
    v = c.get("application.version")
    assert isinstance(v, list) and v[0] == 12
    assert isinstance(c.get("application.average_process_usage"), (int, float))


#================================================================================
# 3. Writable round-trips on SCRATCH (parametrized)
#================================================================================
@pytest.mark.parametrize("obj,prop", WRITABLE, ids=_ids(WRITABLE))
def test_prop_set_roundtrip(live_client, scratch_clip, scratch_device, obj, prop):
    c = live_client
    path = write_path(obj, prop)
    value = SAFE_VALUES.get((obj, prop))     # None -> roundtrip() toggles bool / no-ops
    roundtrip(c, path, value)
    record_prop(obj, prop, "set")


#================================================================================
# 4. Subscriptions on SCRATCH (parametrized)
#================================================================================
def _forceable(obj, prop):
    p = OBJECTS[obj]["props"][prop]
    return p.writable and (obj, prop) not in NO_FORCE_SUB and obj != "midimap"


@pytest.mark.parametrize("obj,prop", OBSERVABLE, ids=_ids(OBSERVABLE))
def test_prop_subscribe(live_client, scratch_clip, scratch_device, scratch_audio_clip, obj, prop):
    c = live_client
    path = sub_path(obj, prop)
    with Sub(c, path) as sub:
        assert sub.wait_push(2.0) is not None, "%s: no immediate push" % path
        if _forceable(obj, prop):
            value = SAFE_VALUES.get((obj, prop))
            prior = c.get(path)
            new = (not prior) if (value is None and isinstance(prior, bool)) else value
            if new is not None and not approx_eq(new, prior):
                n0 = len(sub.events)
                c.set(path, new)
                poll(lambda: len(sub.events), lambda n: n > n0, timeout=2.0)
                assert len(sub.events) > n0, "%s: no push after change" % path
                c.set(path, prior)
    record_prop(obj, prop, "sub")


#================================================================================
# 5. Conditional / audio / param round-trips (dedicated)
#================================================================================
def test_scene_conditional_writables(live_client):
    c = live_client
    sc = "scene.%d" % T["scratch_scene"]
    # tempo (needs tempo_enabled)
    c.set(sc + ".tempo_enabled", True)
    roundtrip(c, sc + ".tempo", 100.0)
    c.set(sc + ".tempo_enabled", False)
    record_prop("scene", "tempo", "set")
    # time signature (needs time_signature_enabled)
    c.set(sc + ".time_signature_enabled", True)
    roundtrip(c, sc + ".time_signature_numerator", 5)
    roundtrip(c, sc + ".time_signature_denominator", 8)
    c.set(sc + ".time_signature_enabled", False)
    record_prop("scene", "time_signature_numerator", "set")
    record_prop("scene", "time_signature_denominator", "set")


def test_audio_clip_writables(live_client, scratch_audio_clip):
    c = live_client
    cp = scratch_audio_clip
    roundtrip(c, cp + ".gain", 0.5)
    roundtrip(c, cp + ".warping", None)          # bool toggle
    roundtrip(c, cp + ".warp_mode", 0)
    roundtrip(c, cp + ".pitch_coarse", 2)
    roundtrip(c, cp + ".pitch_fine", 10.0)
    roundtrip(c, cp + ".ram_mode", None)         # bool toggle
    for p in ("gain", "warping", "warp_mode", "pitch_coarse", "pitch_fine", "ram_mode"):
        record_prop("clip", p, "set")


def test_clip_position_writable(live_client, scratch_clip):
    c = live_client
    cp = scratch_clip
    c.set(cp + ".looping", True)
    roundtrip(c, cp + ".position", 1.0)
    record_prop("clip", "position", "set")


def test_device_parameter_roundtrip(live_client, scratch_device):
    c = live_client
    path = scratch_device + ".parameter.%d.value" % SCRATCH_PARAM_INDEX
    roundtrip(c, path, 0.5)
    record_prop("device", "parameter.value", "set")


#--------------------------------------------------------------------------------
# High-value writables that were previously skipped -- now real tests.
#--------------------------------------------------------------------------------
def _disarm_all(c):
    for i in range(c.get("song.num_tracks")):
        if c.get("track.%d.can_be_armed" % i):
            c.set("track.%d.arm" % i, False)


def _truthy(v):
    return bool(v)


def test_song_arrangement_overdub_writable(live_client):
    # Plain boolean round-trip -- no transport, so nothing is recorded.
    c = live_client
    roundtrip(c, "song.arrangement_overdub", None)        # bool toggle
    record_prop("song", "arrangement_overdub", "set")


def test_song_current_song_time_writable(live_client):
    c = live_client
    c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    t0 = c.get("song.current_song_time")
    c.set("song.current_song_time", 8.0)
    assert approx_eq(poll(lambda: c.get("song.current_song_time"),
                          lambda v: approx_eq(v, 8.0, 0.05)), 8.0, 0.05)
    c.set("song.current_song_time", t0)
    record_prop("song", "current_song_time", "set")


def test_song_link_enabled_writable(live_client):
    c = live_client
    prior = c.get("song.is_ableton_link_enabled")
    tempo0 = c.get("song.tempo")
    c.set("song.is_ableton_link_enabled", not prior)
    assert poll(lambda: c.get("song.is_ableton_link_enabled"), lambda v: v == (not prior)) == (not prior)
    c.set("song.is_ableton_link_enabled", prior)
    assert poll(lambda: c.get("song.is_ableton_link_enabled"), lambda v: v == prior) == prior
    c.set("song.tempo", tempo0)                           # Link may nudge tempo; restore
    record_prop("song", "is_ableton_link_enabled", "set")


def test_song_record_mode_writable(live_client):
    # record_mode arms+rolls ARRANGEMENT record. Disarm everything first so NOTHING is
    # captured, assert the flag round-trips, then stop + reset the playhead.
    c = live_client
    _disarm_all(c)
    c.call("song.stop_playing")
    t0 = c.get("song.current_song_time")
    c.set("song.record_mode", True)
    assert _truthy(poll(lambda: c.get("song.record_mode"), _truthy, timeout=2.0))
    c.set("song.record_mode", False)
    c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    c.set("song.current_song_time", t0)
    assert not _truthy(c.get("song.record_mode"))
    record_prop("song", "record_mode", "set")


def test_song_session_record_writable(live_client):
    # session_record arms session capture; with no slot fired, nothing is recorded.
    c = live_client
    _disarm_all(c)
    c.set("song.session_record", True)
    assert _truthy(poll(lambda: c.get("song.session_record"), _truthy, timeout=2.0))
    c.set("song.session_record", False)
    c.call("song.stop_all_clips"); c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    assert not _truthy(c.get("song.session_record"))
    record_prop("song", "session_record", "set")


def test_song_momentary_triggers_writable(live_client):
    # nudge_up/nudge_down/back_to_arranger are one-way momentary -- the value auto-resets,
    # so we assert the API ACCEPTS the write (the binding works) and restore the transport.
    # Perceptual nudge amount is confirmed in the human-watched test set.
    c = live_client
    tempo0 = c.get("song.tempo")
    for prop in ("nudge_up", "nudge_down", "back_to_arranger"):
        assert c.set("song.%s" % prop, True) is True      # accepted, no error
        c.set("song.%s" % prop, False)
        record_prop("song", prop, "set")
    c.set("song.tempo", tempo0)
    c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)


def test_track_fold_state_writable(live_client):
    # fold/unfold round-trip on the scratch GROUP track (Group-Scratch).
    c = live_client
    g = "track.%d" % T["scratch_group"]
    assert c.get(g + ".is_foldable") is True
    prior = c.get(g + ".fold_state")
    c.set(g + ".fold_state", 1)
    assert poll(lambda: c.get(g + ".fold_state"), lambda v: v == 1) == 1
    c.set(g + ".fold_state", 0)
    assert poll(lambda: c.get(g + ".fold_state"), lambda v: v == 0) == 0
    c.set(g + ".fold_state", prior)
    record_prop("track", "fold_state", "set")


#================================================================================
# 6. NOTES extended round-trip on the scratch clip
#================================================================================
def test_notes_extended_roundtrip(live_client, scratch_clip):
    c = live_client
    cp = scratch_clip
    c.call(cp + ".remove_notes")                  # clear
    assert c.call(cp + ".get_notes") == []
    notes = [
        {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100, "mute": False,
         "probability": 0.8, "velocity_deviation": 5.0, "release_velocity": 70},
        {"pitch": 64, "start_time": 1.0, "duration": 0.5, "velocity": 90, "mute": True,
         "probability": 1.0, "velocity_deviation": 0.0, "release_velocity": 64},
    ]
    ids = c.call(cp + ".add_notes", [notes])
    assert len(ids) == 2 and all(i is not None for i in ids), "add_notes ids=%r" % ids
    got = sorted(c.call(cp + ".get_notes"), key=lambda n: n["start_time"])
    assert len(got) == 2
    n0 = got[0]
    assert n0["pitch"] == 60 and approx_eq(n0["probability"], 0.8) \
        and approx_eq(n0["velocity_deviation"], 5.0) and n0["release_velocity"] == 70
    assert got[1]["pitch"] == 64 and got[1]["mute"] is True
    # remove by id (the pitch-60 note), then by range
    c.call(cp + ".remove_notes_by_id", [[n0["note_id"]]])
    rem = c.call(cp + ".get_notes")
    assert len(rem) == 1 and rem[0]["pitch"] == 64
    c.call(cp + ".remove_notes")
    assert c.call(cp + ".get_notes") == []
    for m in ("get_notes", "add_notes", "remove_notes", "remove_notes_by_id"):
        record_method("clip", m, "FULLY-ASSERTED")


#================================================================================
# 7. Method battery (FULLY-ASSERTED + SMOKE), all on scratch
#================================================================================
def test_song_create_delete_methods(live_client):
    c = live_client
    n = c.get("song.num_tracks")
    c.call("song.create_midi_track", [-1])
    assert c.get("song.num_tracks") == n + 1 and c.get("track.%d.has_midi_input" % n) is True
    c.call("song.delete_track", [n]); assert c.get("song.num_tracks") == n
    c.call("song.create_audio_track", [-1])
    assert c.get("song.num_tracks") == n + 1 and c.get("track.%d.has_audio_input" % n) is True
    c.call("song.delete_track", [n]); assert c.get("song.num_tracks") == n
    r = c.get("song.num_return_tracks")
    c.call("song.create_return_track"); assert c.get("song.num_return_tracks") == r + 1
    c.call("song.delete_return_track", [r]); assert c.get("song.num_return_tracks") == r
    s = c.get("song.num_scenes")
    c.call("song.create_scene", [-1]); assert c.get("song.num_scenes") == s + 1
    c.call("song.delete_scene", [s]); assert c.get("song.num_scenes") == s
    # duplicate inserts the copy AFTER the original (not at the end) -> find the inserted
    # index by diffing names, so we delete the duplicate and never a fixture track.
    pre = c.get("song.track_names")
    c.call("song.duplicate_track", [T["scratch_track"]]); assert c.get("song.num_tracks") == n + 1
    post = c.get("song.track_names")
    dup_i = next((i for i in range(len(pre)) if post[i] != pre[i]), len(pre))
    c.call("song.delete_track", [dup_i])
    assert c.get("song.num_tracks") == n and c.get("song.track_names") == pre
    pre_s = [c.get("scene.%d.name" % i) for i in range(s)]
    c.call("song.duplicate_scene", [T["scratch_scene"]]); assert c.get("song.num_scenes") == s + 1
    post_s = [c.get("scene.%d.name" % i) for i in range(s + 1)]
    dup_si = next((i for i in range(s) if post_s[i] != pre_s[i]), s)
    c.call("song.delete_scene", [dup_si])
    assert c.get("song.num_scenes") == s and [c.get("scene.%d.name" % i) for i in range(s)] == pre_s
    for m in ("create_midi_track", "create_audio_track", "delete_track", "create_return_track",
              "delete_return_track", "create_scene", "delete_scene", "duplicate_track", "duplicate_scene"):
        record_method("song", m, "FULLY-ASSERTED")


def test_song_cue_methods(live_client):
    c = live_client
    t0 = c.get("song.current_song_time")
    c.set("song.current_song_time", 16.0)
    before = {tuple(x) for x in c.get("song.cue_points")}
    c.call("song.set_or_delete_cue")
    times = {x[1] for x in c.get("song.cue_points")}
    assert 16.0 in times, "cue not created at 16.0"
    c.call("song.name_cue", [16.0, "T2-Cue"])
    assert ["T2-Cue", 16.0] in c.get("song.cue_points")
    c.call("song.set_or_delete_cue")              # toggle off
    assert {tuple(x) for x in c.get("song.cue_points")} == before
    with pytest.raises(LomRpcError) as e:
        c.call("song.name_cue", [99.0, "X"])
    assert e.value.code == 404
    c.set("song.current_song_time", t0)
    record_method("song", "set_or_delete_cue", "FULLY-ASSERTED")
    record_method("song", "name_cue", "FULLY-ASSERTED")


def test_song_undo_redo(live_client):
    c = live_client
    tk = "track.%d.name" % T["scratch_track"]
    orig = c.get(tk)
    c.set(tk, "T2-Undo")
    poll(lambda: c.get(tk), lambda v: v == "T2-Undo")
    c.call("song.undo")
    assert poll(lambda: c.get(tk), lambda v: v == orig) == orig
    c.call("song.redo")
    assert poll(lambda: c.get(tk), lambda v: v == "T2-Undo") == "T2-Undo"
    c.set(tk, orig)
    record_method("song", "undo", "FULLY-ASSERTED")
    record_method("song", "redo", "FULLY-ASSERTED")


def test_song_transport_and_jumps(live_client):
    c = live_client
    t0 = c.get("song.current_song_time")
    c.set("song.current_song_time", 0.0)
    c.call("song.jump_by", [4.0])
    assert approx_eq(c.get("song.current_song_time"), 4.0, 0.5)
    c.set("song.current_song_time", 0.0)
    c.call("song.jump_to_next_cue")
    assert approx_eq(c.get("song.current_song_time"), 4.0, 0.5)
    c.set("song.current_song_time", 8.0)
    c.call("song.jump_to_prev_cue")
    assert approx_eq(c.get("song.current_song_time"), 4.0, 0.5)
    c.set("song.current_song_time", t0)
    # transport on/off
    c.call("song.start_playing")
    assert poll(lambda: c.get("song.is_playing"), lambda v: v is True) is True
    c.call("song.stop_playing")
    assert poll(lambda: c.get("song.is_playing"), lambda v: v is False) is False
    c.call("song.continue_playing")
    assert poll(lambda: c.get("song.is_playing"), lambda v: v is True) is True
    c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    c.set("song.current_song_time", t0)
    for m in ("jump_by", "jump_to_next_cue", "jump_to_prev_cue",
              "start_playing", "stop_playing", "continue_playing"):
        record_method("song", m, "FULLY-ASSERTED")


def test_song_beat_subscription(live_client):
    c = live_client
    t0 = c.get("song.current_song_time")
    c.set("song.current_song_time", 0.0)
    with Sub(c, "song.beat") as sub:
        c.call("song.start_playing")
        got = poll(lambda: len(sub.events), lambda n: n >= 1, timeout=3.0)
        c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    c.set("song.current_song_time", t0)
    assert sub.events, "no beat events received while playing"


def test_song_smoke_methods(live_client):
    c = live_client
    # ack-only / nondeterministic effects; force-restore transport+tempo after
    tempo0 = c.get("song.tempo")
    for m in ("tap_tempo", "capture_midi", "re_enable_automation", "stop_all_clips"):
        assert c.call("song.%s" % m) is True
        record_method("song", m, "SMOKE-ONLY")
    c.set("song.tempo", tempo0)
    # force_link_beat_time only if Link is off (avoid perturbing a Link session)
    if c.get("song.is_ableton_link_enabled") is False:
        assert c.call("song.force_link_beat_time") is True
    record_method("song", "force_link_beat_time", "SMOKE-ONLY")
    # capture_and_insert_scene: inserts a scene AFTER the selected one, a tick later (async).
    # Select the last scene so the insert lands at the end, then poll-and-delete the extra(s).
    s = c.get("song.num_scenes")
    c.set("view.selected_scene", s - 1)
    c.call("song.capture_and_insert_scene")
    poll(lambda: c.get("song.num_scenes"), lambda v: v != s, timeout=1.5)   # await the async insert
    while c.get("song.num_scenes") > s:
        c.call("song.delete_scene", [c.get("song.num_scenes") - 1])
    assert c.get("song.num_scenes") == s
    record_method("song", "capture_and_insert_scene", "SMOKE-ONLY")
    # trigger_session_record: arm + immediately disarm/stop
    c.call("song.trigger_session_record")
    c.set("song.session_record", False)
    c.call("song.stop_all_clips"); c.call("song.stop_playing")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    record_method("song", "trigger_session_record", "SMOKE-ONLY")


def test_track_device_methods(live_client):
    # Use a TEMP track (not the scratch track, which carries the persistent scratch device).
    c = live_client
    n = c.get("song.num_tracks")
    c.call("song.create_midi_track", [-1])
    ti = n                                          # appended at the end (index -1 -> last)
    base = "track.%d" % ti
    try:
        assert c.get(base + ".num_devices") == 0
        c.call(base + ".load_device", ["Reverb"])
        assert poll(lambda: c.get(base + ".num_devices"), lambda v: v == 1) == 1
        assert c.get("device.%d.0.class_name" % ti) == "Reverb"
        assert c.get("view.selected_track") == ti
        c.call(base + ".delete_device", [0]); assert c.get(base + ".num_devices") == 0
        c.call(base + ".insert_device", ["Reverb"])
        assert poll(lambda: c.get(base + ".num_devices"), lambda v: v == 1) == 1
        c.call(base + ".delete_device", [0]); assert c.get(base + ".num_devices") == 0
        with pytest.raises(LomRpcError) as e:
            c.call(base + ".load_device", ["NoSuchDeviceXYZ"])
        assert e.value.code == 404
        assert c.call(base + ".stop_all_clips") is True
    finally:
        c.call("song.delete_track", [ti])
        assert c.get("song.num_tracks") == n
    for m in ("load_device", "insert_device", "delete_device", "stop_all_clips"):
        record_method("track", m, "FULLY-ASSERTED" if m != "stop_all_clips" else "SMOKE-ONLY")


def test_track_delete_clip_method(live_client):
    c = live_client
    tk = T["scratch_track"]
    c.call("clip_slot.%d.2.create_clip" % tk, [2.0])     # slot 2 (scratch_clip owns slot 0)
    assert poll(lambda: c.get("clip_slot.%d.2.has_clip" % tk), lambda v: v is True) is True
    c.call("track.%d.delete_clip" % tk, [2])
    assert poll(lambda: c.get("clip_slot.%d.2.has_clip" % tk), lambda v: v is False) is False
    record_method("track", "delete_clip", "FULLY-ASSERTED")


def test_clip_slot_methods(live_client):
    # Uses scratch-track slots 1 & 2 and audio slot (1,2); never touches (4,0) (scratch_clip).
    c = live_client
    tk = T["scratch_track"]
    src = "clip_slot.%d.1" % tk
    dst = "clip_slot.%d.2" % tk
    for cs in (src, dst):
        if c.get(cs + ".has_clip"):
            c.call(cs + ".delete_clip")
    c.call(src + ".create_clip", [4.0])
    assert poll(lambda: c.get(src + ".has_clip"), lambda v: v is True) is True
    c.call(src + ".duplicate_clip_to", [tk, 2])
    assert poll(lambda: c.get(dst + ".has_clip"), lambda v: v is True) is True
    c.call(dst + ".delete_clip")
    # fire/stop the source clip (assert no crash + not left triggered)
    c.call(src + ".fire"); time.sleep(0.2); c.call(src + ".stop"); c.call("song.stop_all_clips")
    poll(lambda: c.get(src + ".is_triggered"), lambda v: v is False)
    c.call(src + ".delete_clip")
    # create_audio_clip on Audio-A slot 2 (empty)
    acs = "clip_slot.1.2"
    if c.get(acs + ".has_clip"):
        c.call(acs + ".delete_clip")
    c.call(acs + ".create_audio_clip", [WAV_PATH])
    assert poll(lambda: c.get(acs + ".has_clip"), lambda v: v is True) is True
    assert c.get("clip.1.2.is_audio_clip") is True
    c.call(acs + ".delete_clip")
    record_method("clip_slot", "create_clip", "FULLY-ASSERTED")
    record_method("clip_slot", "delete_clip", "FULLY-ASSERTED")
    record_method("clip_slot", "duplicate_clip_to", "FULLY-ASSERTED")
    record_method("clip_slot", "create_audio_clip", "FULLY-ASSERTED")
    record_method("clip_slot", "fire", "SMOKE-ONLY")
    record_method("clip_slot", "stop", "SMOKE-ONLY")


def test_clip_methods(live_client, scratch_clip):
    c = live_client
    cp = scratch_clip
    # duplicate_loop doubles the loop length
    c.set(cp + ".looping", True); c.set(cp + ".loop_start", 0.0); c.set(cp + ".loop_end", 2.0)
    c.call(cp + ".duplicate_loop")
    assert approx_eq(c.get(cp + ".loop_end"), 4.0, 0.1), "loop_end=%s" % c.get(cp + ".loop_end")
    c.set(cp + ".loop_end", 4.0)
    c.call(cp + ".fire"); time.sleep(0.2); c.call(cp + ".stop"); c.call("song.stop_all_clips")
    record_method("clip", "duplicate_loop", "FULLY-ASSERTED")
    record_method("clip", "fire", "SMOKE-ONLY")
    record_method("clip", "stop", "SMOKE-ONLY")


def test_scene_methods(live_client):
    c = live_client
    sc = "scene.%d" % T["scratch_scene"]
    c.call(sc + ".fire"); c.call("song.stop_all_clips")
    c.set("view.selected_scene", T["scratch_scene"])
    c.call(sc + ".fire_as_selected"); c.call("song.stop_all_clips")
    c.call("scene.0.fire_selected"); c.call("song.stop_all_clips")
    poll(lambda: c.get("song.is_playing"), lambda v: v is False)
    record_method("scene", "fire", "SMOKE-ONLY")
    record_method("scene", "fire_as_selected", "SMOKE-ONLY")
    record_method("scene", "fire_selected", "SMOKE-ONLY")


def test_device_set_parameters(live_client, scratch_device):
    c = live_client
    dev = scratch_device
    p1 = c.get(dev + ".parameter.1.value")
    c.call(dev + ".set_parameters", [c.get(dev + ".parameter.0.value"), 0.5])
    assert approx_eq(c.get(dev + ".parameter.1.value"), 0.5, 0.05)
    c.call(dev + ".set_parameters", [c.get(dev + ".parameter.0.value"), p1])
    record_method("device", "set_parameters", "FULLY-ASSERTED")


def test_midimap_map_cc(live_client, scratch_device):
    c = live_client
    # map CC 119 ch 0 to scratch device param 1 (no wire read-back; residue cleared when
    # the scratch device is deleted at session teardown).
    assert c.call("midimap.map_cc", [T["scratch_track"], 0, 1, 0, 119]) is True
    record_method("midimap", "map_cc", "SMOKE-ONLY")


def test_application_methods(live_client):
    c = live_client
    assert c.call("application.ping") == "ok"
    lvl0 = c.call("application.get_log_level")
    c.call("application.set_log_level", ["debug"]); assert c.call("application.get_log_level") == "debug"
    c.call("application.set_log_level", [lvl0])
    with pytest.raises(LomRpcError) as e:
        c.call("application.set_log_level", ["bogus"])
    assert e.value.code == 400
    assert c.call("application.show_message", ["AbletonOSC T2 battery"]) is True
    record_method("application", "ping", "FULLY-ASSERTED")
    record_method("application", "get_log_level", "FULLY-ASSERTED")
    record_method("application", "set_log_level", "FULLY-ASSERTED")
    record_method("application", "show_message", "SMOKE-ONLY")


#================================================================================
# 8. Protocol
#================================================================================
def test_batch(live_client):
    c = live_client
    tk = "track.%d.name" % T["scratch_track"]
    orig = c.get(tk)
    out = c.batch([
        {"op": "get", "path": "song.tempo"},
        {"op": "get", "path": "track.0.name"},
        {"op": "set", "path": tk, "value": "T2batch"},
        {"op": "get", "path": tk},
        {"op": "get", "path": "track.99.volume"},     # failing op -> None
    ])
    assert out == [120.0, "MIDI-A", True, "T2batch", None]
    c.set(tk, orig)


@pytest.mark.parametrize("op,code", [
    ({"op": "get", "path": "nope.0.x"}, 404),
    ({"op": "get", "path": "track.0.not_a_prop"}, 404),
    ({"op": "call", "path": "song.not_a_method"}, 404),
    ({"op": "get", "path": "track.99.name"}, 404),
    ({"op": "get", "path": "scene.99.name"}, 404),
    ({"op": "get", "path": "device.0.99.name"}, 404),
    ({"op": "set", "path": "song.is_playing", "value": True}, 400),
    ({"op": "set", "path": "song.num_tracks", "value": 9}, 400),
    ({"op": "frobnicate"}, 400),
    ({"op": "get", "path": "track.x.name"}, 400),
], ids=["unknown-obj", "unknown-prop", "unknown-method", "track-oor", "scene-oor",
        "device-oor", "ro-set", "computed-set", "bad-op", "bad-index"])
def test_error_codes(live_client, op, code):
    with pytest.raises(LomRpcError) as e:
        live_client.rpc(dict(op, id=1))
    assert e.value.code == code


def test_multi_client_routing(live_client):
    a = JsonRpcClient(hostname=HOST, port=PORT, identity="t2-A", timeout=10.0)
    b = JsonRpcClient(hostname=HOST, port=PORT, identity="t2-B", timeout=10.0)
    try:
        assert a.get("track.0.name") == "MIDI-A"
        assert b.get("track.1.name") == "Audio-A"
        tk = "track.%d.name" % T["scratch_track"]
        orig = a.get(tk)
        ev_a, ev_b = [], []
        ha = a.subscribe(tk, lambda v, p: ev_a.append(v))
        hb = b.subscribe("track.%d.mute" % T["scratch_track"], lambda v, p: ev_b.append(v))
        # consume BOTH immediate pushes before clearing, so they don't pollute the assertion
        poll(lambda: (len(ev_a), len(ev_b)), lambda n: n[0] >= 1 and n[1] >= 1, timeout=2.0)
        ev_a.clear(); ev_b.clear()
        a.set(tk, "T2-A-only")
        poll(lambda: len(ev_a), lambda n: n > 0, timeout=2.0)
        time.sleep(0.2)                                   # give any stray B push a chance to (not) arrive
        assert ev_a and not ev_b, "name change routed to wrong client (a=%r b=%r)" % (ev_a, ev_b)
        a.unsubscribe(ha); b.unsubscribe(hb); a.set(tk, orig)
    finally:
        a.close(); b.close()


def test_disconnect_no_crash(live_client):
    # behavioral reap smoke: subscribe then close; server must keep serving others.
    d = JsonRpcClient(hostname=HOST, port=PORT, identity="t2-bye", timeout=10.0)
    d.subscribe("track.%d.name" % T["scratch_track"], lambda v, p: None)
    time.sleep(0.2)
    d.close()
    time.sleep(0.3)
    assert live_client.ping() == "ok"      # server healthy after a subscriber vanished
    live_client.set("track.%d.name" % T["scratch_track"], "Scratch")  # mutate the reaped prop, no crash


#================================================================================
# 9. application.reload runs LAST among method tests (wipes subscriptions; ROUTER survives)
#================================================================================
def test_zz_application_reload(live_client):
    c = live_client
    assert c.call("application.reload") is True
    pong = poll(lambda: _safe_ping(c), lambda v: v == "ok", timeout=5.0)
    assert pong == "ok", "server did not recover after reload"
    assert c.get("song.num_tracks") == manifest.SONG["num_tracks"]   # descriptor/dispatcher came back intact
    record_method("application", "reload", "SMOKE-ONLY")


def _safe_ping(c):
    try:
        return c.ping()
    except LomRpcError:
        return None


#================================================================================
# 10. Coverage meta-test (defined LAST -> runs last; reconciles ledger vs descriptor)
#================================================================================
def test_zzz_coverage_complete():
    expected_props = {(o, p) for o, s in OBJECTS.items() for p in s["props"]}
    expected_methods = {(o, m) for o, s in OBJECTS.items() for m in s["methods"]}
    if OBJECTS["clip"].get("notes"):
        expected_methods |= {("clip", m) for m in ("get_notes", "add_notes", "remove_notes", "remove_notes_by_id")}

    missing_methods = sorted(m for m in expected_methods
                             if m not in RUN_LEDGER["methods"] and m not in SKIP_REASONS)
    assert not missing_methods, "methods never executed: %s" % missing_methods

    gaps = []
    for (o, p) in sorted(expected_props):
        if (o, p) in SKIP_REASONS:
            continue
        prop = OBJECTS[o]["props"][p]
        modes = RUN_LEDGER["props"].get((o, p), set())
        if prop.readable and "get" not in modes:
            gaps.append("%s.%s:get" % (o, p))
        if prop.writable and "set" not in modes:
            gaps.append("%s.%s:set" % (o, p))
        if prop.observable and "sub" not in modes:
            gaps.append("%s.%s:sub" % (o, p))
    assert not gaps, "uncovered prop modes: %s" % gaps

    assert len(expected_props) >= 140 and len(expected_methods) >= 50
