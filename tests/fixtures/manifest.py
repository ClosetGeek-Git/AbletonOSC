"""
Ground truth for the canonical AbletonOSC test project (`AbletonOSCTest`).

This is the single source of fixture truth. The live test battery (T2) asserts that
the live LOM state matches what is declared here, and the live harness uses it as the
"verify-loaded" guard (fail fast if the wrong set is open). Build the .als to match
this exactly — see MANIFEST.md for step-by-step instructions.

Two kinds of expected value:
  * FIXED    — values the build explicitly sets (names, tempos, the MIDI clip's notes).
               These are hard ground truth; tests assert them by ==.
  * CAPTURED — values that are stable for a given build but awkward to predict or that
               the Live UI does not let you set by number (device class_name/type,
               parameter names + min/max, the audio clip's sample_length, and every
               `color_index` — Live's UI shows color swatches, not the numeric palette
               index, so you pick any colors you like and the actual indices are read
               back from real Live). These are recorded ONCE by the capture step
               (MANIFEST.md §Capture) into manifest_captured.json, which the battery
               loads and merges over these placeholders. `None` here means "capture it".

Index conventions (Live session view, left→right): group tracks occupy an index and
their child tracks follow immediately. Return tracks and the master have their own
index spaces (song.return_tracks / song.master_track).
"""

PROJECT_NAME = "AbletonOSCTest"
PROJECT_RELPATH = "AbletonOSCTest Project/AbletonOSCTest.als"  # under tests/fixtures/

# --- Song (global) — FIXED, set explicitly during build ---------------------------
SONG = {
    "tempo": 120.0,
    "signature_numerator": 4,
    "signature_denominator": 4,
    "is_playing": False,
    "metronome": False,
    # Scale settings (Live 11+/12). Set the song scale to C Major during build.
    "root_note": 0,            # 0 = C
    "scale_name": "Major",
    # num_tracks counts group + children (7 below: 5 base + the Scratch group); num_scenes = 3.
    "num_tracks": 7,
    "num_scenes": 3,
    "num_return_tracks": 1,
}

# --- Tracks (song.tracks, left→right) ---------------------------------------------
# role: "fixture" = read-only ground truth (never mutated by tests);
#       "scratch"  = tests may set/create/delete here, restoring afterward.
TRACKS = [
    {
        "index": 0, "name": "MIDI-A", "role": "fixture",
        "is_midi": True, "has_midi_input": True, "is_foldable": False, "is_grouped": False,
        "color_index": None,   # CAPTURED (pick any color; index read back from Live)
        # Devices on this track (UI names to add; class_name/type CAPTURED).
        "devices": [
            {"index": 0, "ui_name": "Operator",  "class_name": None, "type": None},  # instrument
            {"index": 1, "ui_name": "Reverb",    "class_name": None, "type": None},  # audio effect
        ],
        # The fixture MIDI clip lives at (track 0, scene 0) — see CLIPS below.
    },
    {
        "index": 1, "name": "Audio-A", "role": "fixture",
        "is_midi": False, "has_audio_input": True, "is_foldable": False, "is_grouped": False,
        "color_index": None,   # CAPTURED
        "devices": [],
        # The fixture audio clip lives at (track 1, scene 0) — see CLIPS below.
    },
    {
        # NOT API-built — created by the manual Cmd-G on Child-A (see GROUP_STEP). The
        # is_foldable/fold_state assertions are POST_GROUP (committed set only).
        "index": 2, "name": "Group-G", "role": "fixture", "built": "manual_group",
        "is_midi": False, "is_foldable": True, "is_grouped": False, "fold_state": 0,
        "color_index": None, "devices": [],   # color_index CAPTURED
    },
    {
        # API-built as a plain track; "is_grouped" becomes True only after the manual
        # group step (POST_GROUP_ASSERTIONS).
        "index": 3, "name": "Child-A", "role": "fixture",
        "is_midi": True, "is_foldable": False, "is_grouped": True,   # grouped under Group-G (post-step)
        "color_index": None, "devices": [],   # color_index CAPTURED
    },
    {
        "index": 4, "name": "Scratch", "role": "scratch",
        "is_midi": True, "is_foldable": False, "is_grouped": False,
        "color_index": None, "devices": [],   # color_index CAPTURED
    },
    {
        # Scratch GROUP (added 2026-06-21): a foldable group track + its child, giving a
        # MUTABLE group surface for fold_state / grouping tests (Group-G/Child-A above are
        # read-only fixtures). Added manually in the UI (Cmd-G); not built by build_fixture.py.
        "index": 5, "name": "Group-Scratch", "role": "scratch_group",
        "is_midi": False, "is_foldable": True, "is_grouped": False, "fold_state": 0,
        "color_index": None, "devices": [],   # color_index CAPTURED
    },
    {
        "index": 6, "name": "Scratch-Child", "role": "fixture",
        "is_midi": True, "is_foldable": False, "is_grouped": True,
        "color_index": None, "devices": [],   # color_index CAPTURED
    },
]

# --- Return tracks (song.return_tracks) -------------------------------------------
RETURN_TRACKS = [
    {"index": 0, "name": "A-Reverb", "role": "fixture"},
]
# Each non-return track therefore has sends[0] (to Return A). Tests read track.send.0.

# --- Master track ------------------------------------------------------------------
MASTER = {"name": "Master", "role": "fixture"}

# --- Clips (track_index, scene_index) ---------------------------------------------
CLIPS = [
    {
        "track": 0, "scene": 0, "name": "MidiClipA", "role": "fixture",
        "is_midi_clip": True, "length": 4.0, "looping": True,
        # FIXED notes (build by drawing exactly these). Asserted via the extended API.
        # Each: pitch, start_time(beats), duration(beats), velocity, mute.
        "notes": [
            {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100, "mute": False},
            {"pitch": 64, "start_time": 1.0, "duration": 1.0, "velocity": 90,  "mute": False},
            {"pitch": 67, "start_time": 2.0, "duration": 1.0, "velocity": 80,  "mute": False},
        ],
    },
    {
        "track": 1, "scene": 0, "name": "AudioClipA", "role": "fixture",
        "is_audio_clip": True, "warping": True,
        "sample_file": "demo_loop.wav",        # committed under tests/fixtures/audio/
        "sample_length": None,                  # CAPTURED (frames)
        "gain": None,                           # CAPTURED (normalized float; build leaves at 0 dB)
        "file_path_endswith": "demo_loop.wav",  # file_path is absolute; assert suffix only
    },
]

# --- Scenes (song.scenes) ----------------------------------------------------------
SCENES = [
    {"index": 0, "name": "Scene-A", "role": "fixture", "color_index": None,  # CAPTURED
     "tempo_enabled": False, "is_empty": False},
    {"index": 1, "name": "Scene-B", "role": "fixture", "color_index": None,  # CAPTURED
     "tempo_enabled": True, "tempo": 126.0,
     "time_signature_enabled": True, "time_signature_numerator": 3, "time_signature_denominator": 4,
     "is_empty": True},
    {"index": 2, "name": "Scratch", "role": "scratch"},
]

# --- Cue points (song.cue_points), in time order ----------------------------------
CUE_POINTS = [
    {"index": 0, "name": "Cue-1", "time": 4.0},
    {"index": 1, "name": "Cue-2", "time": 8.0},
]

# --- Application -------------------------------------------------------------------
APPLICATION = {
    "major_version": 12,   # Live 12.x
}

# --- Index map: which fixture object each test family targets ----------------------
# The descriptor-driven battery reads this to know which concrete indices to hit for
# read-only assertions vs which scratch objects to mutate.
TARGETS = {
    "midi_track": 0,
    "audio_track": 1,
    "group_track": 2,
    "grouped_track": 3,
    "scratch_track": 4,
    "scratch_group": 5,            # foldable group track for fold_state/grouping tests
    "scratch_group_child": 6,
    "midi_clip": (0, 0),
    "audio_clip": (1, 0),
    "scratch_slot": (4, 0),        # scratch MIDI clip lives here (create/delete/notes)
    "scratch_dup_slot": (4, 1),    # duplicate_clip_to target on the scratch track
    "scratch_audio_slot": (1, 1),  # empty slot on Audio-A → scratch AUDIO clip (audio-only writables)
    "device": (0, 0),              # Operator on MIDI-A
    "device_effect": (0, 1),       # Reverb on MIDI-A
    "fixture_scene": 0,
    "scratch_scene": 2,
    "return_track": 0,
}

# Filled by the capture step (MANIFEST.md §Capture) from real Live; merged over the
# CAPTURED placeholders above. Kept in manifest_captured.json next to this file.
CAPTURED_FILE = "manifest_captured.json"

# --- API build vs committed layout ------------------------------------------------
# The project is built **via the API** (build_fixture.py), which exercises the
# create/load/notes/cue plumbing. Two things the Live Object Model genuinely cannot do
# stay manual (verified): track GROUPING (no API at all) and SAVING the set (no
# Song.save — Cmd-S only). See MANIFEST.md.
#
# Grouping shifts track indices, so the builder works in a PRE-GROUP layout and the
# human's Cmd-G produces the POST-GROUP layout that TRACKS / TARGETS (above) assert:
#   pre-group  (builder makes, in this order):  0 MIDI-A, 1 Audio-A, 2 Child-A, 3 Scratch
#   post-group (after manual Cmd-G on Child-A):  0 MIDI-A, 1 Audio-A, 2 Group-G, 3 Child-A, 4 Scratch
# MIDI-A/Audio-A (and so every clip/device/scene/cue/return) keep indices 0/1 across the
# shift, so the builder references them directly.

MIN_LIVE_VERSION = "12.2"          # create_audio_clip lands in Live 12.2

# Tracks the API builder creates, in order (Group-G is NOT here — Cmd-G makes it):
BUILD_TRACK_ORDER = [
    {"name": "MIDI-A", "kind": "midi"},
    {"name": "Audio-A", "kind": "audio"},
    {"name": "Child-A", "kind": "midi"},     # becomes Group-G's child after the manual group step
    {"name": "Scratch", "kind": "midi"},
]

# The one manual structural step, done next session after running the builder:
GROUP_STEP = {"select_track": "Child-A", "action": "Cmd-G", "name_group": "Group-G"}

# Assertions that hold ONLY after the manual group step — validated against the committed
# .als, not produced by the builder. (object_kind, track_name, prop, expected)
POST_GROUP_ASSERTIONS = [
    ("track", "Group-G", "is_foldable", True),
    ("track", "Child-A", "is_grouped", True),
]
