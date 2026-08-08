# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AbletonOSC is an Ableton Live **MIDI Remote Script** that exposes the Live Object Model
(LOM) to external processes over a path-based **JSON-RPC** wire, carried on the **ZeroMQ
wire protocol (ZMTP)** over `tcp://` / `ipc://`. It runs **inside Live's embedded Python
interpreter** — it is not a standalone program. The dispatcher imports `Live` and
`ableton.v2.*`, which only exist inside Live, so the package cannot run outside Live
(the headless tests install `sys.modules` stubs). Requires Live 11+ (the descriptor is a
Live-12 sweep).

> **JSON-RPC only — the OSC layer has been retired.** Despite the name, this fork no longer
> speaks OSC. The per-object OSC handlers, the OSC dgram path, the `@id:`/`@tag:` string
> markers, and the vendored `pythonosc` are all **gone**. JSON-RPC's native `id` / `sub` /
> `error` carry correlation, subscriptions, and failures. The names `OSCServer` /
> `osc_server.py` / `OSC_*` constants are retained only as the stable transport symbols.

**Hard platform constraint (it shapes the whole transport design).** Live 12.4's embedded
CPython is a **static build with no `ctypes`/`_ctypes` and no dynamic native loading** — it
cannot import any compiled C-extension. That rules out `pyzmq`/`libzmq` (and any FFI)
*inside Live*. So the in-Live transport is **`pyzmtp`**, a vendored **pure-Python,
stdlib-only** ZMTP implementation; the out-of-Live client uses **real `pyzmq`**, which is
wire-compatible with it.

## The single source of truth

[`abletonosc/lom_schema.py`](abletonosc/lom_schema.py) is a **pure-data descriptor** of the
entire exposed LOM surface (9 objects, 144 properties, 50 methods — verify by calling
`export_schema()` directly rather than trusting a cached count, this one has drifted before).
It is the one place LOM knowledge lives, and it drives three consumers:

- the **dispatcher** (`jsonrpc.py`) interprets it to serve `get`/`set`/`call`/`subscribe`;
- the **test battery** (`tests/test_jsonrpc.py`) iterates it for provably-complete coverage;
- **`tools/export_schema.py`** emits `lom_schema.json` (PHP client codegen) and
  `docs/JSONRPC_API.md` (human reference).

Each property has a `kind` (`DIRECT` / `MIXER` / `SEND` / `PARAM` / `COMPUTED` / `LIST`) so
the dispatcher needs **no per-object special-casing** for the common path. **Adding a Live
property/method = one descriptor entry** — then `python tools/export_schema.py` to
regenerate the schema + docs, and the parametrized battery automatically covers it.

> **House rule: LOM facts are descriptor + code + web-verified, never trained-knowledge.**
> The Cycling '74 apiref is the **Max** LOM and differs from the **Python control-surface**
> LOM — verify against the Python LiveAPI docs (structure-void / nsuspray) and the deployed
> behaviour, not from memory.

## Commands

There is no build step and no linter. A Python 3.11 venv with `pyzmq` + `pytest` is checked
in at `.venv311/` (the in-Live server never uses it).

- **Install for manual testing:** copy the repo folder, named exactly `AbletonOSC`, into
  Live's Remote Scripts dir, then enable it under `Preferences > Link / Tempo / MIDI`:
    - macOS: `~/Music/Ableton/User Library/Remote Scripts`
    - Windows: `\Users\<user>\Documents\Ableton\User Library\Remote Scripts`
  - Live does **not** follow symlinks on macOS — use a real copy (the live tier `rsync`s).
- **Reload code without restarting Live:** call `application.reload` (JSON-RPC). It
  re-imports the dispatcher + descriptor and rebuilds the handler. **Caveat:** it does
  **not** rebuild `OSCServer`, so the running pyzmtp ROUTER + its transport thread survive —
  changes to `osc_server.py` / `zmtp_transport.py` need a **full Live restart**.
- **Regenerate the exported schema + docs after editing the descriptor:**
  `.venv311/bin/python tools/export_schema.py`
- **Tests** (use the checked-in venv). See "Testing" below. Headless tiers:
  `.venv311/bin/python -m pytest tests/ -q`
- **View Live's boot log** for compile/load errors: see `CONTRIBUTING.md`. A genuine
  import failure inside Live is also dumped to `import_error.log` next to `__init__.py`.

## Architecture

**Entry point & lifecycle.** Live calls `create_instance()` in `__init__.py`, which returns
a `Manager` (`manager.py`, a `ControlSurface` subclass). `Manager.__init__` creates the
server and schedules `tick()`. **Handler code runs single-threaded on a ~100ms tick** —
Live beachballs if you do CPU work on a background thread or touch the LOM off the tick, so
`Manager.tick()` calls `osc_server.process()` (a non-blocking **drain of the transport
bridge queues**, no socket I/O) and reschedules itself. Do not introduce threads or blocking
calls in handler code. (There is exactly **one** background thread — the pyzmtp transport's
private asyncio loop — and it is safe *because* it only parks in socket I/O, never touches
the LOM.)

**The server (`abletonosc/osc_server.py`, `OSCServer`).** Owns the transport and routes
every inbound frame to the JSON-RPC dispatcher. `process()` drains three queues populated on
the asyncio thread: inbound frames → `dispatch_frame()` → `json_dispatcher.handle(data,
routing_id)`; peer connect/disconnect events (disconnect → `_drop_client`, reaping that
client's subscriptions); and `HostUnreachable` send-failures (→ `_drop_client`). Replies and
subscription pushes go out via `send_json(routing_id, obj)`; lifecycle events via
`broadcast_json(obj)` over the bounded `_known_clients` set.

**The dispatcher (`abletonosc/jsonrpc.py`, `JsonRpcHandler`).** An `AbletonOSCHandler`
subclass (so it gets `self.song`, the per-client listener registry, and `drop_client`). It
registers no addresses; `init_api()` hooks `self.osc_server.json_dispatcher = self`.
`handle(data, routing_id)` parses the JSON envelope and runs one op or a `batch`:
- **path resolution** (`_split` → root/indices/leaf; `_resolve_object` walks the fixed
  song→track→clip_slot→clip / device / scene / view chain; `_accessor` returns a getter/
  setter/listen-target per `kind`). Indexed leaves: `send.<i>`, `parameter.<i>.<sub>`.
- **computed/list** getters are keyed by the descriptor's `compute` string (`len:<attr>`
  resolves against the **owning** object — `len:tracks` on song, `len:devices` on a track,
  `len:parameters` on a device; `version`, `avg_process_usage`, `selected_*`, `clips.*`,
  `parameters.*`, `track_names`, `cue_points`).
- **methods** are generic `getattr(obj, method)(*args)` plus a few bespoke cases
  (clip notes, `application.*` → the Manager, `midimap.map_cc`, `scene.fire_selected`,
  `clip_slot.duplicate_clip_to`, `track.delete_clip`, `device.set_parameters`).
- **MIDI notes** use the **extended dict** API exclusively — `get_notes_extended` /
  `add_new_notes(MidiNoteSpecification)` / `remove_notes_extended` / `remove_notes_by_id` —
  exposing `note_id`, `probability`, `velocity_deviation`, `release_velocity` (Live 11/12).
- **subscribe** registers a listener only for `observable` props (and the synthetic
  `song.beat`), captures the `routing_id` **by value** in the push closure (so later async
  pushes on future ticks route to the right client), and pushes the current value
  immediately. `_jsonable` guards non-serialisable LOM values into a clean `415` error.

**The handler base (`abletonosc/handler.py`, `AbletonOSCHandler`).** A `Component` subclass
providing the per-client listener registry — `_add_listener` / `_remove_listener` /
`_clear_listeners` / `drop_client` — keyed so the registering client's routing-id is the
last key element. The dispatcher builds its own subscription closures and uses these
primitives; the base no longer carries OSC getter/setter/listen helpers.

**Network transport.** AbletonOSC speaks ZMTP over TCP, ROUTER↔DEALER:
- **In Live (server):** a **pyzmtp ROUTER** bound on `tcp://0.0.0.0:11000` (`OSC_ENDPOINT`
  in `constants.py`). pyzmtp is the vendored pure-Python ZMTP (top-level `pyzmtp/`).
- **Out of Live (client):** a real **pyzmq DEALER** ([`client/jsonrpc_client.py`](client/jsonrpc_client.py),
  `JsonRpcClient`) or php-zmq (`Closetgeek\Stemdj\Lom`). Each connects with its own ZeroMQ
  **routing identity**; query/command replies *and* unsolicited subscription pushes travel
  back over the **same** connection addressed to the requesting client's routing-id. There
  is no separate response port; multiple clients (even on one host) are distinguishable.
- **The asyncio-thread bridge (`abletonosc/zmtp_transport.py`, `ZmtpTransport`).** pyzmtp is
  asyncio-native and the tick must never block, so the ROUTER runs on a **private asyncio
  loop on a daemon thread** (`abletonosc-zmtp`), bridged to the tick by thread-safe
  `queue.Queue`s. **Load-bearing rule:** only the loop thread ever touches a pyzmtp object;
  the tick thread touches only the queues + `run_coroutine_threadsafe` /
  `call_soon_threadsafe`. The bind is the one synchronous step (at `OSCServer.__init__`, off
  the tick), raising `TransportBindError` on failure. `shutdown()` is deterministic
  (cancel drainers → `router.close()` → `ctx.term()` → stop loop → join thread) — the
  `LINGER=0` analog, so there is no port/thread leak on a Live restart.
- **Disconnect detection.** A clean DEALER close arrives as a pyzmtp `disconnect` event; a
  send that fails `HostUnreachable` is likewise reaped. No heartbeat/TTL. Half-open TCP (a
  vanished peer that never sends FIN) is best-effort.

## Wire summary

Envelope (one JSON object per frame): `{"id":N,"op":"get|set|call","path":"track.0.volume",
"value":…,"args":[…]}`; `path` may be a list for multi-get; `{"id":N,"batch":[…]}` runs many
ops in one round-trip. Replies: `{"id":N,"result":…}` or `{"id":N,"error":{"code":C,
"message":M}}`. Subscriptions: `{"op":"subscribe","sub":N,"path":…}` → unsolicited
`{"sub":N,"path":…,"value":…}`, reaped on disconnect. `{"op":"ping"}` → `"ok"` (readiness).
Error codes: `400` malformed/not-readable/not-writable/bad-index · `404` unknown
object/property/method/index · `408` client timeout · `415` non-serialisable · `500`
internal. Full surface: [`docs/JSONRPC_API.md`](docs/JSONRPC_API.md) / `lom_schema.json`.

## Testing

The suite is **descriptor-driven** so coverage is provably complete and self-maintaining.

- **T0 — headless, no Live, no socket** (CI):
  - `tests/test_descriptor.py` — descriptor self-consistency (every `kind`/`compute`
    resolves; `observable`/`writable` only on supported kinds; clip notes + beat declared).
  - `tests/test_jsonrpc.py` — the real dispatcher driven against a generic **fake LOM tree**,
    **parametrized over the descriptor** (every property get/set/subscribe, every method
    call) + explicit notes/batch/error/computed/list/drop_client cases + a **meta-test that
    enforces every descriptor entry produced a case** (no silent gaps).
- **T1 — headless socket loopback, no Live** (CI):
  - `tests/test_jsonrpc_loopback.py` — a real pyzmq DEALER ↔ pyzmtp ROUTER ↔ real
    `OSCServer` ↔ dispatcher over tcp loopback (`process()` pumped on a thread); proves the
    whole wire: encode → transport → `dispatch_frame` → dispatcher → `send_json` → demux,
    plus per-client subscription routing and disconnect reaping.
  - `tests/test_transport_teardown.py` — deterministic shutdown / port re-bind / client close.
  - `tests/test_pyzmtp_vendoring.py` — pyzmtp is vendored, contains **no** native `.so`/
    `.dylib`, and `ZmtpTransport` binds + shuts down.
- **T2 — live, on-demand, human-attended** (not in CI): the descriptor-driven battery
  against real Live 12 + a hand-authored canonical project. The fixture is specified in
  [`tests/fixtures/manifest.py`](tests/fixtures/manifest.py) + `MANIFEST.md` (build it once,
  Collect-All-and-Save, commit). A *trial* Live shows modal launch dialogs a **human must
  dismiss**, so this tier needs someone present at launch.

Run headless: `.venv311/bin/python -m pytest tests/ -q`. The Live stubs are installed by
`tests/conftest.py` before collection, plus each headless module's own `install_live_stubs()`.

**`tests/devserver.py`** extracts the T1 rig into a standalone, long-lived process — reusing,
unchanged, `tests.test_jsonrpc.build_song`/`FakeManager`/`_install_live_stubs` (the fake LOM)
plus the real `OSCServer`/`JsonRpcHandler`. This is what lets a **non-Python client** — the
in-house PHP `Closetgeek\Stemdj\Lom` PHPUnit suite, in the sibling `php_root` repo — drive the
real dispatcher over a real ZMQ wire against the exact same fixture the Python tiers assert
on. Run standalone with `python -m AbletonOSC.tests.devserver`; it binds an ephemeral port,
prints `PORT <n>` on startup, confirms readiness via `{"op":"ping"}`, and accepts one
test-only control op intercepted before the dispatcher — `{"op":"__reset__"}` (detach
listeners + rebuild the fixture, for per-test isolation) — which never reaches
`JsonRpcHandler`. See [core/docs/AbletonBridge/17-test-tiers-and-the-shared-harness.md](/Users/jason/Documents/dev/MacosVDJPort/core/docs/AbletonBridge/17-test-tiers-and-the-shared-harness.md)
for the full picture across both languages.

## Invariants to preserve

- **The descriptor is the source of truth.** Don't hardcode LOM knowledge in `jsonrpc.py`;
  add a descriptor entry and let the dispatcher's `kind`/`compute` machinery serve it. After
  any descriptor change, regenerate (`tools/export_schema.py`) and keep the battery green.
- **Single-threaded tick.** No threads/blocking in handler/dispatcher code; the only
  background thread is the pyzmtp loop, which never touches the LOM.
- **Subscriptions capture `routing_id` at registration** (a local, by value), or async
  pushes mis-route. Beat is the one fan-out exception.
- **Transport changes need a full Live restart** (the server is built once and survives
  `application.reload`); descriptor/dispatcher changes hot-reload.
- **No native code in `pyzmtp`** — it must stay pure-Python/stdlib (the static-Python
  constraint); `tests/test_pyzmtp_vendoring.py` pins this.

## Cross-references

- **The PHP client**: `/Users/jason/Documents/dev/MacosVDJPort/php_root/src/Lom/`
  (`Closetgeek\Stemdj\Lom`) — a ReactPHP DEALER client for the VDJ-plugin PHP userspace,
  wire-compatible with this repo's ROUTER. Its `LomSchema.php` reads this repo's exported
  `lom_schema.json`; its `tests/Lom/` PHPUnit suite drives this repo's `tests/devserver.py`
  over a real ZMQ wire. See `php_root/CLAUDE.md`'s "Ableton bridge (`src/Lom/`)" section.
- **The didactic doc set**: `/Users/jason/Documents/dev/MacosVDJPort/core/docs/AbletonBridge/`
  — a 20-chapter set covering this repo and the PHP client together as one bridge, since
  they're the two ends of one wire sharing one descriptor and one test harness.
