"""
Single source of truth for the Ableton Live Object Model surface that AbletonOSC
exposes over JSON-RPC.

Pure data — no `Live` import, no object references. Three consumers:
  * the JSON-RPC dispatcher (`jsonrpc.py`) interprets it to serve get/set/call/subscribe;
  * the test battery iterates it for provably-complete coverage;
  * `export_schema()` emits `lom_schema.json` for the PHP client.

Derived from the deployed per-object handlers (the working enumeration of what's
exposed) plus verified Live-12 additions (marked `note="live12"`). The LOM-specific
access strategy for each property is captured by `kind`, so the dispatcher needs no
per-object special-casing:

  DIRECT  getattr/setattr(obj, name)
  MIXER   obj.mixer_device.<name>.value            (track volume / panning)
  SEND    obj.mixer_device.sends[i].value          (leaf "send.<i>")
  PARAM   obj.parameters[i].<sub>                   (leaf "parameter.<i>.<sub>"; device)
  COMPUTED dispatcher scalar getter keyed by `compute` (e.g. "len:tracks", "version")
  LIST    dispatcher list getter keyed by `compute` (e.g. "clips.name")

Objects resolve from the song root by a fixed accessor chain (see jsonrpc.py
_resolve_object); `arity` is the number of leading path indices.
"""

DIRECT = "direct"
MIXER = "mixer"
SEND = "send"
PARAM = "param"
COMPUTED = "computed"
LIST = "list"


class Prop:
    __slots__ = ("readable", "writable", "observable", "kind", "compute", "note")

    def __init__(self, readable=True, writable=False, observable=False,
                 kind=DIRECT, compute=None, note=None):
        self.readable = readable
        self.writable = writable
        self.observable = observable
        self.kind = kind
        self.compute = compute
        self.note = note

    def to_dict(self):
        return {"readable": self.readable, "writable": self.writable,
                "observable": self.observable, "kind": self.kind,
                "compute": self.compute, "note": self.note}


def _directs(names, writable, observable=True, note=None):
    return {n: Prop(readable=True, writable=writable, observable=observable, note=note) for n in names}


def _merge(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


# ---- Song --------------------------------------------------------------------------
_SONG_RW = [
    "arrangement_overdub", "back_to_arranger", "clip_trigger_quantization",
    "current_song_time", "groove_amount", "is_ableton_link_enabled", "loop",
    "loop_length", "loop_start", "metronome", "midi_recording_quantization",
    "nudge_down", "nudge_up", "punch_in", "punch_out", "record_mode", "root_note",
    "scale_name", "session_record", "signature_denominator", "signature_numerator",
    "tempo",
]
_SONG_RO = ["can_redo", "can_undo", "is_playing", "song_length", "session_record_status"]
_SONG_METHODS = {
    "capture_and_insert_scene": [], "capture_midi": [], "continue_playing": [],
    "create_audio_track": ["index"], "create_midi_track": ["index"],
    "create_return_track": [], "create_scene": ["index"], "delete_return_track": ["index"],
    "delete_scene": ["index"], "delete_track": ["index"], "duplicate_scene": ["index"],
    "duplicate_track": ["index"], "force_link_beat_time": [], "jump_by": ["beats"],
    "jump_to_prev_cue": [], "jump_to_next_cue": [], "redo": [], "re_enable_automation": [],
    "set_or_delete_cue": [], "start_playing": [], "stop_all_clips": [], "stop_playing": [],
    "tap_tempo": [], "trigger_session_record": [], "undo": [],
    # Composite: rename the cue point at a given Arrangement time (set_or_delete_cue creates
    # one at current_song_time; CuePoint.name is writable but CuePoint.time is read-only).
    "name_cue": ["time", "name"],
}

# ---- Track -------------------------------------------------------------------------
_TRACK_RW = ["arm", "color", "color_index", "current_monitoring_state", "fold_state",
             "mute", "solo", "name"]
_TRACK_RO = ["can_be_armed", "fired_slot_index", "has_audio_input", "has_audio_output",
             "has_midi_input", "has_midi_output", "is_foldable", "is_grouped", "is_visible",
             "output_meter_level", "output_meter_left", "output_meter_right",
             "playing_slot_index"]

# ---- Clip --------------------------------------------------------------------------
_CLIP_RW = ["color", "color_index", "end_marker", "gain", "launch_mode",
            "launch_quantization", "legato", "loop_end", "loop_start", "looping", "muted",
            "name", "pitch_coarse", "pitch_fine", "position", "ram_mode", "start_marker",
            "velocity_amount", "warp_mode", "warping"]
_CLIP_RO = ["end_time", "file_path", "gain_display_string", "has_groove", "is_midi_clip",
            "is_audio_clip", "is_overdubbing", "is_playing", "is_recording", "is_triggered",
            "length", "playing_position", "sample_length", "start_time", "will_record_on_start"]
_CLIP_METHODS = {"fire": [], "stop": [], "duplicate_loop": []}

# ---- Clip slot ---------------------------------------------------------------------
_CLIPSLOT_RW = ["has_stop_button"]
_CLIPSLOT_RO = ["has_clip", "controls_other_clips", "is_group_slot", "is_playing",
                "is_triggered", "playing_status", "will_record_on_start"]

# ---- Scene -------------------------------------------------------------------------
_SCENE_RW = ["color", "color_index", "name", "tempo", "tempo_enabled",
             "time_signature_numerator", "time_signature_denominator", "time_signature_enabled"]
_SCENE_RO = ["is_empty", "is_triggered"]

# ---- Device ------------------------------------------------------------------------
_DEVICE_RO = ["class_name", "name", "type"]


OBJECTS = {
    "song": {
        "arity": 0,
        "props": _merge(
            _directs(_SONG_RW, writable=True),
            _directs(_SONG_RO, writable=False),
            {
                "num_tracks": Prop(kind=COMPUTED, compute="len:tracks"),
                "num_scenes": Prop(kind=COMPUTED, compute="len:scenes"),
                "num_return_tracks": Prop(kind=COMPUTED, compute="len:return_tracks"),
                "track_names": Prop(kind=LIST, compute="track_names"),
                "cue_points": Prop(kind=LIST, compute="cue_points"),
                # Verified Live-12 additions:
                "scale_mode": Prop(readable=True, writable=True, observable=True, note="live12"),
                "scale_intervals": Prop(readable=True, writable=False, observable=True, note="live12"),
                "tempo_follower_enabled": Prop(readable=True, writable=True, observable=True, note="live12"),
            },
        ),
        "methods": _SONG_METHODS,
        # Beat is a synthetic edge-detect listener (current_song_time), not a real LOM prop.
        "special_listeners": ["beat"],
    },
    "track": {
        "arity": 1,
        "props": _merge(
            _directs(_TRACK_RW, writable=True),
            _directs(_TRACK_RO, writable=False),
            {
                "volume": Prop(readable=True, writable=True, observable=True, kind=MIXER),
                "panning": Prop(readable=True, writable=True, observable=True, kind=MIXER),
                "send": Prop(readable=True, writable=True, observable=True, kind=SEND),
                "num_devices": Prop(kind=COMPUTED, compute="len:devices"),
                "clips.name": Prop(kind=LIST, compute="clips.name"),
                "clips.length": Prop(kind=LIST, compute="clips.length"),
                "clips.color": Prop(kind=LIST, compute="clips.color"),
                "arrangement_clips.name": Prop(kind=LIST, compute="arrangement_clips.name"),
                "arrangement_clips.length": Prop(kind=LIST, compute="arrangement_clips.length"),
                "arrangement_clips.start_time": Prop(kind=LIST, compute="arrangement_clips.start_time"),
                "devices.name": Prop(kind=LIST, compute="devices.name"),
                "devices.type": Prop(kind=LIST, compute="devices.type"),
                "devices.class_name": Prop(kind=LIST, compute="devices.class_name"),
            },
        ),
        "methods": {"stop_all_clips": [], "delete_device": ["device_index"],
                    "delete_clip": ["clip_index"],
                    # Creation verbs (close AbletonOSC's historical Browser gap).
                    # load_device is a composite (select track -> walk browser -> load_item),
                    # works since Live 9; insert_device is the Live-12.3 native fast path.
                    "load_device": ["name"], "insert_device": ["name"]},
    },
    "scene": {
        "arity": 1,
        "props": _merge(_directs(_SCENE_RW, writable=True), _directs(_SCENE_RO, writable=False)),
        "methods": {"fire": [], "fire_as_selected": [], "fire_selected": []},
    },
    "clip_slot": {
        "arity": 2,
        "props": _merge(_directs(_CLIPSLOT_RW, writable=True), _directs(_CLIPSLOT_RO, writable=False)),
        "methods": {"fire": [], "stop": [], "create_clip": ["length"], "delete_clip": [],
                    "duplicate_clip_to": ["target_track", "target_slot"],
                    # create_audio_clip(path) references an audio file on disk; Live 12.2+.
                    "create_audio_clip": ["path"]},
    },
    "clip": {
        "arity": 2,
        "props": _merge(
            _directs(_CLIP_RW, writable=True),
            _directs(_CLIP_RO, writable=False),
            {"start_time": Prop(readable=True, writable=False, observable=True, note="live12")},
        ),
        "methods": _CLIP_METHODS,
        # Notes use the extended dict API (get_notes_extended / add_new_notes /
        # remove_notes_extended / remove_notes_by_id); see jsonrpc.py note ops.
        "notes": True,
    },
    "device": {
        "arity": 2,
        "props": _merge(
            _directs(_DEVICE_RO, writable=False),
            {
                "is_using_compare_preset_b": Prop(readable=True, writable=False, observable=True, note="live12"),
                "num_parameters": Prop(kind=COMPUTED, compute="len:parameters"),
                "parameters.name": Prop(kind=LIST, compute="parameters.name"),
                "parameters.value": Prop(kind=LIST, compute="parameters.value"),
                "parameters.min": Prop(kind=LIST, compute="parameters.min"),
                "parameters.max": Prop(kind=LIST, compute="parameters.max"),
                "parameters.is_quantized": Prop(kind=LIST, compute="parameters.is_quantized"),
                # Indexed single-parameter access: leaf "parameter.<i>.<sub>".
                "parameter.value": Prop(readable=True, writable=True, observable=True, kind=PARAM),
                "parameter.value_string": Prop(readable=True, writable=False, kind=PARAM),
                "parameter.name": Prop(readable=True, writable=False, kind=PARAM),
                "parameter.min": Prop(readable=True, writable=False, kind=PARAM),
                "parameter.max": Prop(readable=True, writable=False, kind=PARAM),
                "parameter.is_quantized": Prop(readable=True, writable=False, kind=PARAM),
            },
        ),
        "methods": {"set_parameters": ["values..."]},  # bulk set; handled explicitly
    },
    "view": {
        "arity": 0,
        "props": {
            "selected_scene": Prop(readable=True, writable=True, observable=True, kind=COMPUTED, compute="selected_scene"),
            "selected_track": Prop(readable=True, writable=True, observable=True, kind=COMPUTED, compute="selected_track"),
            "selected_clip": Prop(readable=True, writable=True, kind=COMPUTED, compute="selected_clip"),
            "selected_device": Prop(readable=True, writable=True, kind=COMPUTED, compute="selected_device"),
        },
        "methods": {},
    },
    "application": {
        "arity": 0,
        "props": {
            "version": Prop(readable=True, writable=False, kind=COMPUTED, compute="version"),
            "average_process_usage": Prop(readable=True, writable=False, kind=COMPUTED, compute="avg_process_usage"),
        },
        # Manager-routed control ops (reload/log level/status message) + readiness ping.
        "methods": {"reload": [], "show_message": ["message"], "get_log_level": [],
                    "set_log_level": ["level"], "ping": []},
    },
    "midimap": {
        "arity": 0,
        "props": {},
        "methods": {"map_cc": ["track", "device", "parameter", "channel", "cc"]},
    },
}


# Props that `_directs` blanket-marked observable=True but that Live 12.4 has NO listener
# for (verified empirically by the T2 live battery: subscribe registers no listener and
# pushes nothing). Corrected to observable=False so clients aren't offered a dead
# subscription. NOTE: audio-clip knobs (warping/warp_mode/pitch_coarse/pitch_fine/ram_mode/
# file_path) ARE listenable on audio clips and remain observable.
_NOT_OBSERVABLE = {
    ("song", "can_undo"), ("song", "can_redo"), ("song", "scale_intervals"),
    ("track", "fold_state"), ("track", "can_be_armed"), ("track", "is_foldable"),
    ("track", "is_grouped"), ("track", "is_visible"),
    ("scene", "is_empty"),
    ("clip_slot", "is_group_slot"), ("clip_slot", "is_playing"), ("clip_slot", "will_record_on_start"),
    ("clip", "gain"), ("clip", "gain_display_string"), ("clip", "has_groove"),
    ("clip", "is_midi_clip"), ("clip", "is_audio_clip"), ("clip", "is_playing"),
    ("clip", "is_triggered"), ("clip", "length"), ("clip", "sample_length"),
    ("clip", "will_record_on_start"),
    ("device", "class_name"), ("device", "type"),
}
for _obj, _prop in _NOT_OBSERVABLE:
    OBJECTS[_obj]["props"][_prop].observable = False


def export_schema():
    """Serialise to a plain dict (for lom_schema.json / PHP codegen)."""
    out = {}
    for obj, spec in OBJECTS.items():
        out[obj] = {
            "arity": spec["arity"],
            "props": {name: p.to_dict() for name, p in spec.get("props", {}).items()},
            "methods": spec.get("methods", {}),
            "special_listeners": spec.get("special_listeners", []),
            "notes": spec.get("notes", False),
        }
    return out
