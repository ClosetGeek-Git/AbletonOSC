# Canonical test project — build instructions

The live test battery (T2) runs against **one** canonical Ableton Live 12 project,
`AbletonOSCTest`, that is deterministic ground truth for every API. You build it **once**
to match [`manifest.py`](manifest.py), then commit it. Re-do this only if the manifest
changes.

> **How it's built:** almost entirely **via the API** — `build_fixture.py` drives the
> JSON-RPC client to create the tracks, clips, notes, devices, audio clip, scenes, and
> cues. This deliberately exercises the create/load/notes/cue plumbing. Only the two
> things the Live Object Model genuinely **cannot** do are manual (both verified against
> the Cycling '74 apiref + Python introspection):
> 1. **Group tracks** — there is no grouping API at all (Cmd-G is UI-only).
> 2. **Save the set** — there is no `Song.save`/`Application.save`; saving is Cmd-S only
>    (a trial license re-enables the *UI* Save, not an API one). No audio export exists
>    either — which is why the demo `.wav` is a committed file, not rendered.
>
> Building once and committing also keeps the test **oracle independent**: every later T2
> run opens the *committed* `.als`, not a freshly-API-built set, so a bug shared between
> builder and dispatcher can't hide.

## 0. Prerequisites

- **Ableton Live ≥ 12.2** (`manifest.MIN_LIVE_VERSION`) — `create_audio_clip` lands in
  12.2. A trial within its save window is fine. `Track.load_device` (used for devices)
  works on Live 9+.
- **AbletonOSC installed and enabled** as a Control Surface (see top-level README), on a
  **new empty default set**.
- **Demo audio committed** at `tests/fixtures/audio/demo_loop.wav` — a short (~2–4 s)
  royalty-free mono/stereo loop, kept small. The builder loads it via
  `create_audio_clip(<abs path>)`, so it must exist first.

## 1. Run the API builder

With the empty set open and AbletonOSC listening:

```
.venv311/bin/python tests/fixtures/build_fixture.py
```

It builds, in order (pre-group layout — see `manifest.BUILD_TRACK_ORDER`):
**MIDI-A**, **Audio-A**, **Child-A**, **Scratch** tracks + the **A-Reverb** return; sets
names; loads **Operator** then **Reverb** onto MIDI-A; creates **MidiClipA** (the three
known notes) and **AudioClipA** (from the demo wav); creates scenes **Scene-A**/
**Scene-B** (tempo 126, 3/4)/**Scratch**; sets song tempo/sig/scale/metronome; and adds
named cues **Cue-1** (beat 4) / **Cue-2** (beat 8). It read-backs each create as a
self-check and prints the remaining manual checklist.

> Colors are **free** — set any you like (or none). Live's UI exposes color *swatches*,
> not the numeric `color_index`, so nothing requires a specific index; the capture step
> records whatever Live assigned and the battery asserts against that.

## 2. Manual step A — group Child-A (the one thing the LOM can't do)

Select **Child-A**, press **Cmd-G** (Group), and name the new group track **Group-G**.
This inserts Group-G at index 2 and shifts the layout to the committed/post-group form
(`0 MIDI-A, 1 Audio-A, 2 Group-G, 3 Child-A, 4 Scratch`) — see `manifest.GROUP_STEP` /
`POST_GROUP_ASSERTIONS`.

## 3. Manual step B — save the project

There is no save API, so save by hand. **File → Collect All and Save** (include all
samples) so the demo sample is packed inside the project, and save it as
**`AbletonOSCTest`** under `tests/fixtures/` so the path resolves to
`manifest.PROJECT_RELPATH` (`tests/fixtures/AbletonOSCTest Project/AbletonOSCTest.als`).
Commit the created `Samples/` folder too.

## 4. Capture the CAPTURED values

Some ground truth is read from real Live rather than guessed: device `class_name`/`type`,
parameter names + min/max, the audio clip's `sample_length`, the at-rest `gain`, and the
actual `color_index`es. With the saved project open and AbletonOSC enabled:

```
.venv311/bin/python tests/fixtures/capture.py
```

This drives the client, reads those values, and writes
`tests/fixtures/manifest_captured.json`, which the battery merges over the `None`/CAPTURED
placeholders in `manifest.py`. Re-run capture whenever you rebuild.

## 5. Commit

Commit `tests/fixtures/`:
- `manifest.py`, `MANIFEST.md`, `build_fixture.py`, `capture.py`
- `AbletonOSCTest Project/` (the `.als` + `Samples/`)
- `audio/demo_loop.wav`
- `manifest_captured.json`

(`.gitignore` is configured to allow `tests/fixtures/**` despite the global audio/project
ignores.)

## Verify-loaded guard

On every live run the harness opens this project and asserts the live state matches
`manifest.py` (track count + names, key clips/devices). If you open the wrong set, the run
**fails fast** with a clear message instead of testing the wrong project.
