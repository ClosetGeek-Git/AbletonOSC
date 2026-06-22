#!/usr/bin/env python3
"""
Capture the CAPTURED ground-truth values from the **committed** AbletonOSCTest project
into manifest_captured.json, which the live battery merges over the `None`/CAPTURED
placeholders in manifest.py.

Run against the saved project open in Live (>= 12.2) with AbletonOSC enabled:

    .venv311/bin/python tests/fixtures/capture.py [--host H] [--port P]

Captured (read from real Live rather than guessed): device class_name/type + parameter
names/min/max, the audio clip's sample_length + at-rest gain + file_path, and every track
and scene color_index (Live's UI sets colors by swatch, not by the numeric index).

Reads the POST-GROUP committed layout (0 MIDI-A, 1 Audio-A, 2 Group-G, 3 Child-A,
4 Scratch). Cannot be unit-tested headlessly (needs Live).
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import manifest                                                      # noqa: E402
from client.jsonrpc_client import JsonRpcClient, LomRpcError         # noqa: E402

OUT = os.path.join(HERE, manifest.CAPTURED_FILE)


def capture(c):
    out = {"tracks": {}, "scenes": {}, "devices": {}, "audio_clip": {}}

    n_tracks = int(c.get("song.num_tracks"))
    for i in range(n_tracks):
        out["tracks"][str(i)] = {"color_index": c.get("track.%d.color_index" % i)}

    n_scenes = int(c.get("song.num_scenes"))
    for i in range(n_scenes):
        out["scenes"][str(i)] = {"color_index": c.get("scene.%d.color_index" % i)}

    # Devices on MIDI-A (post-group index 0): Operator (0,0), Reverb (0,1).
    for key in ("device", "device_effect"):
        t, d = manifest.TARGETS[key]
        base = "device.%d.%d" % (t, d)
        out["devices"]["%d.%d" % (t, d)] = {
            "class_name": c.get(base + ".class_name"),
            "type": c.get(base + ".type"),
            "num_parameters": c.get(base + ".num_parameters"),
            "parameter_names": c.get(base + ".parameters.name"),
            "parameter_mins": c.get(base + ".parameters.min"),
            "parameter_maxes": c.get(base + ".parameters.max"),
        }

    # Audio clip (Audio-A, scene 0).
    t, s = manifest.TARGETS["audio_clip"]
    base = "clip.%d.%d" % (t, s)
    out["audio_clip"]["%d.%d" % (t, s)] = {
        "sample_length": c.get(base + ".sample_length"),
        "gain": c.get(base + ".gain"),
        "file_path": c.get(base + ".file_path"),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11000)
    args = ap.parse_args()

    c = JsonRpcClient(hostname=args.host, port=args.port, timeout=10.0)
    try:
        assert c.ping() == "ok", "AbletonOSC did not answer ping"
        data = capture(c)
    except LomRpcError as e:
        print("ERROR (code %s): %s" % (e.code, e))
        return 2
    finally:
        c.close()

    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, OUT)
    print("wrote %s" % os.path.relpath(OUT, ROOT))
    print("  tracks=%d scenes=%d devices=%d" % (len(data["tracks"]), len(data["scenes"]),
                                                len(data["devices"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
