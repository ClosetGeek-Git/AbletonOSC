# AbletonOSC: Control Ableton Live over JSON-RPC

AbletonOSC is an [Ableton Live](https://www.ableton.com/) MIDI Remote Script that exposes
the **Live Object Model (LOM)** to external processes over a structured, path-based
**JSON-RPC** wire, carried on the **ZeroMQ wire protocol (ZMTP)** over `tcp://` (or
`ipc://`). It runs inside Live's embedded Python interpreter; clients run anywhere.

> **This fork is JSON-RPC only.** The original AbletonOSC's OSC protocol (positional OSC
> messages, echoed-index replies, the `@id:`/`@tag:` string markers, the vendored
> `pythonosc`) has been **retired**. A single declarative descriptor
> ([`abletonosc/lom_schema.py`](abletonosc/lom_schema.py)) is the source of truth that
> drives the dispatcher, the test battery, and the exported client schema. JSON-RPC's
> native `id` / `sub` / `error` replace the old marker scheme. The **ZMTP transport**
> (per-client routing-id, single full-duplex port, listener registry) is unchanged.

Requires **Ableton Live 11+** (the descriptor is a Live 12 sweep).

---

## Installation

Copy this repo folder, named exactly `AbletonOSC`, into Live's Remote Scripts directory,
then enable it under `Preferences → Link / Tempo / MIDI` as a Control Surface.

- **macOS:** `~/Music/Ableton/User Library/Remote Scripts`
- **Windows:** `\Users\<user>\Documents\Ableton\User Library\Remote Scripts`

macOS does **not** follow symlinks for Remote Scripts — use a real copy (the live-test
harness automates this with `rsync`). On load, AbletonOSC binds a pyzmtp ROUTER on
`tcp://0.0.0.0:11000` and shows *"AbletonOSC: Listening for JSON-RPC on port 11000"*.

---

## Quick start

### Python

```python
from AbletonOSC.client import JsonRpcClient

c = JsonRpcClient(hostname="127.0.0.1", port=11000)

c.get("song.tempo")                       # -> 120.0
c.set("song.tempo", 125.0)                # -> True
c.get("track.0.name")                     # -> "1-MIDI"
c.get(["song.tempo", "song.is_playing"])  # multi-get -> [125.0, False]
c.call("song.start_playing")              # -> True

# subscribe to changes; callback(value, path)
sub = c.subscribe("song.is_playing", lambda v, p: print(p, v))
# ...
c.unsubscribe(sub)
c.close()
```

### PHP (in-house client)

The `closetgeek/stemdj` framework ships `Closetgeek\Stemdj\Lom`, a ReactPHP-friendly
DEALER client for this wire (promise + callback; a subscription-backed mirror for hot
reads). See that package for the typed `Song`/`Track`/`Clip`/… handles.

---

## The JSON-RPC wire

### Transport

A **pyzmtp ROUTER** (in Live) ↔ a real **pyzmq/php-zmq DEALER** (out of Live), one
full-duplex TCP connection. Each client connects with its own ZeroMQ **routing identity**
(`zmq.IDENTITY`); replies *and* unsolicited subscription pushes travel back over the same
socket addressed to that routing-id, so multiple clients (even on one host) are fully
distinguishable. A clean DEALER close is detected directly (no heartbeat) and reaps that
client's subscriptions.

> Why pyzmtp: Live 12.4's embedded CPython is a **static build with no `ctypes` and no
> native loading**, so it cannot import `pyzmq`/`libzmq`. The in-Live transport is
> therefore [`pyzmtp`](pyzmtp/), a vendored **pure-Python** ZMTP implementation,
> wire-compatible with real `libzmq` clients.

### Envelope

One JSON object per ZMTP frame. Requests:

| op | shape | reply `result` |
| --- | --- | --- |
| `get` | `{"op":"get","path":"track.0.volume"}` (or `"path":[...]` for multi-get) | the value (or list of values) |
| `set` | `{"op":"set","path":"song.tempo","value":125.0}` | `true` |
| `call` | `{"op":"call","path":"song.create_scene","args":[-1]}` | method result, or `true` if it returns nothing |
| `subscribe` | `{"op":"subscribe","sub":N,"path":"song.is_playing"}` | (no reply; pushes begin) |
| `unsubscribe` | `{"op":"unsubscribe","sub":N}` | (no reply) |
| `ping` | `{"op":"ping"}` | `"ok"` (readiness probe) |
| `batch` | `{"id":N,"batch":[ {op…}, {op…} ]}` | list of per-op results, one round-trip |

Add `"id":N` to any request to get a correlated reply:

```jsonc
// reply (success)        // reply (error)
{"id": 7, "result": ...}  {"id": 7, "error": {"code": 404, "message": "..."}}
```

Unsolicited frames carry no `id`:

```jsonc
{"sub": N, "path": "song.is_playing", "value": true}   // subscription push
{"event": "error", "message": "..."}                   // server lifecycle event
```

### Path scheme

`object[.index…].leaf`. The number of indices is the object's **arity**:

| arity | objects | example |
| --- | --- | --- |
| 0 | `song`, `view`, `application`, `midimap` | `song.tempo` |
| 1 | `track`, `scene` | `track.0.mute` |
| 2 | `clip`, `clip_slot`, `device` | `clip.0.0.name`, `device.0.1.parameter.2.value` |

Leaf forms:

- **direct** — `getattr`/`setattr`, e.g. `track.0.name`.
- **mixer** — `track.0.volume`, `track.0.panning` (→ `mixer_device.<x>.value`).
- **send** — `track.0.send.0` (→ `mixer_device.sends[0].value`).
- **parameter** — `device.0.0.parameter.<i>.<sub>`, `<sub>` ∈ `value` (r/w/sub),
  `value_string`, `name`, `min`, `max`, `is_quantized`.
- **computed** — scalars such as `song.num_tracks`, `application.version` (`[major, minor]`),
  `view.selected_track` (the index; settable).
- **list** — aggregates such as `track.0.clips.name`, `device.0.0.parameters.name`,
  `song.track_names`, `song.cue_points`.

### Subscriptions

`subscribe` is allowed only for **observable** properties (flag `sub` in the reference).
The server immediately pushes the current value, then pushes again on every change, to the
subscribing client's routing-id. `song.beat` is a special synthetic subscription (an
edge-detected beat counter), not a LOM property.

### MIDI notes (extended dict)

`clip` declares the extended-dict note ops (`call` on a `clip.<t>.<s>` path):

- `add_notes` — `args: [[ {pitch, start_time, duration, velocity, mute, probability,
  velocity_deviation, release_velocity}, … ]]`; returns the new `note_id`s.
- `get_notes` — `args: [from_pitch, pitch_span, from_time, time_span]` (optional; default
  whole clip); returns a list of note dicts including `note_id` + the Live-11/12 fields
  above.
- `remove_notes` — `args:` same optional range; clears matching notes.
- `remove_notes_by_id` — `args: [[note_id, …]]`.

### Error codes

`400` malformed op / not readable / not writable / bad index · `404` unknown
object/property/method or index out of range · `408` client-side RPC timeout · `415`
value not JSON-serialisable · `500` internal handler error.

---

## API reference

The complete, exhaustive surface is **generated from the descriptor** — never hand-edited,
so it cannot drift from the dispatcher:

- **[`docs/JSONRPC_API.md`](docs/JSONRPC_API.md)** — human-readable: every object, property
  (with `r`/`w`/`sub` flags), and method.
- **[`lom_schema.json`](lom_schema.json)** — machine-readable, for client codegen.

Regenerate both after changing [`abletonosc/lom_schema.py`](abletonosc/lom_schema.py):

```
.venv311/bin/python tools/export_schema.py
```

---

## Testing

A Python 3.11 venv with `pyzmq` + `pytest` is checked in at `.venv311/`. Tiers:

- **T0 — headless, no Live, no socket** (CI):
  - `tests/test_descriptor.py` — descriptor self-consistency (every kind/compute resolves;
    `observable`/`writable` only where supported).
  - `tests/test_jsonrpc.py` — the dispatcher driven against a fake LOM tree, **parametrized
    over the descriptor** so every property (get/set/subscribe) and method (call) is
    exercised, plus a meta-test that *enforces* complete coverage.
- **T1 — headless socket loopback, no Live** (CI): `tests/test_jsonrpc_loopback.py` — a real
  pyzmq DEALER ↔ pyzmtp ROUTER ↔ real `OSCServer` ↔ dispatcher over tcp; proves the whole
  wire end-to-end. `tests/test_transport_teardown.py` + `tests/test_pyzmtp_vendoring.py`
  cover deterministic shutdown and the pure-Python vendoring invariant.
- **T2 — live, on-demand, human-attended**: the descriptor-driven battery against real Live
  12 + a hand-authored canonical project (see [`tests/fixtures/MANIFEST.md`](tests/fixtures/MANIFEST.md)).

**Shared dev server.** [`tests/devserver.py`](tests/devserver.py) extracts the T1 rig into a
standalone, long-lived process — the same fake LOM (`build_song`), the same real
`OSCServer`/`JsonRpcHandler` — so a non-Python client can drive the real dispatcher over a
real ZMQ wire without Live running at all. This is what lets the in-house PHP client's own
PHPUnit suite (`Closetgeek\Stemdj\Lom`, in a sibling project) assert against the exact same
fixture the Python tiers do, rather than a second, hand-maintained one. Run it standalone
with `python -m AbletonOSC.tests.devserver`; it binds an ephemeral port, prints `PORT <n>` on
startup for the spawning harness to discover it, and accepts one test-only control op,
`{"op":"__reset__"}`, to rebuild the fixture between tests (never reaches the dispatcher).

Run the headless tiers:

```
.venv311/bin/python -m pytest tests/ -q
```

---

# Acknowledgements

AbletonOSC began as an OSC remote script; this fork retains its LOM groundwork while moving
the wire to JSON-RPC. Thanks to [Stu Fisher](https://github.com/stufisher/) (and other
authors) for [LiveOSC](https://livecontrol.q3f.org/ableton-liveapi/liveosc/), the spiritual
predecessor. Thanks to [Julien Bayle](https://structure-void.com/ableton-live-midi-remote-scripts/#liveAPI)
and [NSUSpray](https://nsuspray.github.io/Live_API_Doc/) for the XML API docs, based on
original work by [Hanz Petrov](http://remotescripts.blogspot.com/p/support-files.html).

For code contributions and feedback, many thanks to:
- Jörn Lengwenings ([Coupe70](https://github.com/Coupe70))
- Bill Moser ([billmoser](https://github.com/billmoser))
- [stevmills](https://github.com/stevmills)
- Marco Buongiorno Nardelli ([marcobn](https://github.com/marcobn)) and Colin Stokes
- Mark Marijnissen ([markmarijnissen](https://github.com/markmarijnissen))
- [capturcus](https://github.com/capturcus)
- Esa Ruoho a.k.a. Lackluster ([esaruoho](https://github.com/esaruoho))

## License

See [LICENSE.md](LICENSE.md).
