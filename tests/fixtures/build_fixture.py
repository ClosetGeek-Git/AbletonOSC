#!/usr/bin/env python3
"""
Build the canonical AbletonOSCTest project **via the API** (exercises create/load/notes/
cue plumbing), to the spec in manifest.py. Run against a running Ableton Live (>= 12.2)
with AbletonOSC enabled, on a fresh set.

    .venv311/bin/python tests/fixtures/build_fixture.py [--host H] [--port P] [--no-normalize]

It builds everything the LOM can. The two things the LOM genuinely cannot do are left to
you afterwards (the script prints the checklist): GROUP Child-A into "Group-G" (Cmd-G),
and SAVE the set (Cmd-S) — there is no save API. See MANIFEST.md.

Note: Live's default set is NOT empty, so by default the script first NORMALIZES (deletes
existing return tracks, creates our tracks then deletes the originals, trims scenes). Pass
--no-normalize if you started from a truly empty set.

This script cannot be unit-tested headlessly (it needs Live); it is authored to be run
once during the fixture build. It read-backs each create as a self-check and exits non-zero
on a mismatch.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))      # AbletonOSC/
sys.path.insert(0, HERE)                            # for `import manifest`
sys.path.insert(0, ROOT)                            # for `from client...`

import manifest                                                      # noqa: E402
from client.jsonrpc_client import JsonRpcClient, LomRpcError         # noqa: E402

WAV_PATH = os.path.join(HERE, "audio", "demo_loop.wav")

_errors = []


def log(msg):
    print("  " + msg)


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        _errors.append(name)


def build(c, normalize=True):
    # ---- preflight ---------------------------------------------------------------
    assert c.ping() == "ok", "AbletonOSC did not answer ping"
    ver = c.get("application.version")
    log("Live version: %s" % ver)
    if tuple(ver[:2]) < (12, 2):
        log("WARNING: Live < 12.2 — create_audio_clip will fail; load_device still works.")

    # ---- normalize a stock default set to a clean slate --------------------------
    if normalize:
        for i in range(int(c.get("song.num_return_tracks")) - 1, -1, -1):
            c.call("song.delete_return_track", [i])
        orig = int(c.get("song.num_tracks"))
        log("normalizing: %d existing track(s) will be removed after ours are created" % orig)
    else:
        orig = 0

    # ---- tracks (pre-group order) ------------------------------------------------
    for spec in manifest.BUILD_TRACK_ORDER:
        if spec["kind"] == "midi":
            c.call("song.create_midi_track", [-1])
        else:
            c.call("song.create_audio_track", [-1])
    for _ in range(orig):
        c.call("song.delete_track", [0])          # drop the originals; ours shift to 0..3
    for i, spec in enumerate(manifest.BUILD_TRACK_ORDER):
        c.set("track.%d.name" % i, spec["name"])
    log("created %d tracks: %s" % (len(manifest.BUILD_TRACK_ORDER),
                                   ", ".join(s["name"] for s in manifest.BUILD_TRACK_ORDER)))

    # ---- return track (gives every track send.0; name is not API-addressable) ----
    c.call("song.create_return_track")
    log("created return track (name it 'A-Reverb' by hand if desired — returns aren't API-addressable)")

    # ---- scenes ------------------------------------------------------------------
    want = len(manifest.SCENES)
    n = int(c.get("song.num_scenes"))
    while n < want:
        c.call("song.create_scene", [-1]); n += 1
    while n > want:
        c.call("song.delete_scene", [n - 1]); n -= 1
    for s in manifest.SCENES:
        i = s["index"]
        c.set("scene.%d.name" % i, s["name"])
        if s.get("tempo_enabled"):
            c.set("scene.%d.tempo" % i, s["tempo"])
            c.set("scene.%d.tempo_enabled" % i, True)
        if s.get("time_signature_enabled"):
            c.set("scene.%d.time_signature_numerator" % i, s["time_signature_numerator"])
            c.set("scene.%d.time_signature_denominator" % i, s["time_signature_denominator"])
            c.set("scene.%d.time_signature_enabled" % i, True)
    log("named %d scenes" % want)

    # ---- song globals ------------------------------------------------------------
    c.set("song.tempo", manifest.SONG["tempo"])
    c.set("song.signature_numerator", manifest.SONG["signature_numerator"])
    c.set("song.signature_denominator", manifest.SONG["signature_denominator"])
    c.set("song.metronome", manifest.SONG["metronome"])
    c.set("song.root_note", manifest.SONG["root_note"])
    c.set("song.scale_name", manifest.SONG["scale_name"])
    log("set tempo/signature/scale/metronome")

    # ---- devices on MIDI-A (pre-group index 0) -----------------------------------
    midi_t = 0
    for dev in manifest.TRACKS[manifest.TARGETS["midi_track"]]["devices"]:
        c.call("track.%d.load_device" % midi_t, [dev["ui_name"]])
        log("loaded device: %s" % dev["ui_name"])

    # ---- MIDI clip + notes (MIDI-A, scene 0) -------------------------------------
    midi_clip = manifest.CLIPS[0]
    c.call("clip_slot.%d.0.create_clip" % midi_t, [midi_clip["length"]])
    ids = c.call("clip.%d.0.add_notes" % midi_t, [midi_clip["notes"]])
    c.set("clip.%d.0.name" % midi_t, midi_clip["name"])
    c.set("clip.%d.0.looping" % midi_t, midi_clip["looping"])
    log("created MidiClipA with %d notes (ids=%s)" % (len(midi_clip["notes"]), ids))

    # ---- audio clip (Audio-A, scene 0) -------------------------------------------
    audio_t = 1
    if not os.path.exists(WAV_PATH):
        raise SystemExit("demo audio missing: %s (commit a short wav there first)" % WAV_PATH)
    audio_clip = manifest.CLIPS[1]
    c.call("clip_slot.%d.0.create_audio_clip" % audio_t, [WAV_PATH])
    c.set("clip.%d.0.name" % audio_t, audio_clip["name"])
    c.set("clip.%d.0.warping" % audio_t, audio_clip["warping"])
    log("created AudioClipA from %s" % os.path.basename(WAV_PATH))

    # ---- cue points --------------------------------------------------------------
    for cue in manifest.CUE_POINTS:
        c.set("song.current_song_time", cue["time"])
        c.call("song.set_or_delete_cue")
        c.call("song.name_cue", [cue["time"], cue["name"]])
    c.set("song.current_song_time", 0.0)
    log("created %d cue points" % len(manifest.CUE_POINTS))

    # ---- self-check read-backs ---------------------------------------------------
    print("\n-- self-check --")
    names = c.get(["track.%d.name" % i for i in range(len(manifest.BUILD_TRACK_ORDER))])
    check("track names match build order",
          names == [s["name"] for s in manifest.BUILD_TRACK_ORDER])
    check("num_tracks == 4 (pre-group; becomes 5 after Cmd-G)", c.get("song.num_tracks") == 4)
    check("MIDI-A has 2 devices", c.get("track.0.num_devices") == 2)
    got_notes = c.call("clip.0.0.get_notes")
    check("MidiClipA has %d notes" % len(midi_clip["notes"]), len(got_notes) == len(midi_clip["notes"]))
    check("Audio-A slot 0 has a clip", c.get("clip_slot.1.0.has_clip") is True)
    check("song.num_scenes == %d" % want, c.get("song.num_scenes") == want)
    check("Cue-1 present at beat 4", any(abs(t - 4.0) < 1e-6 for _, t in c.get("song.cue_points")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11000)
    ap.add_argument("--no-normalize", action="store_true")
    args = ap.parse_args()

    c = JsonRpcClient(hostname=args.host, port=args.port, timeout=10.0)
    try:
        build(c, normalize=not args.no_normalize)
    except LomRpcError as e:
        print("\nERROR (code %s): %s" % (e.code, e))
        return 2
    finally:
        c.close()

    print("\n=== remaining MANUAL steps (LOM can't do these) ===")
    print("  1. Select 'Child-A', press Cmd-G, name the new group track 'Group-G'.")
    print("  2. File -> Collect All and Save -> save as 'AbletonOSCTest' under tests/fixtures/.")
    print("  3. Run: .venv311/bin/python tests/fixtures/capture.py")
    if _errors:
        print("\n%d self-check(s) FAILED — fix before saving." % len(_errors))
        return 1
    print("\nAll self-checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
