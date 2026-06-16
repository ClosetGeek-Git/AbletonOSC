# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AbletonOSC is an Ableton Live **MIDI Remote Script** that exposes the Live Object Model (LOM)
over OSC. It runs **inside Live's embedded Python interpreter** — it is not a standalone program.
Most modules import `Live` and `ableton.v2.*`, which only exist inside Live, so the package
cannot be imported or run outside of Live. Requires Live 11+.

## Commands

There is no build step and no linter/formatter configured.

- **Install for manual testing:** copy (or symlink) the repo folder, named exactly `AbletonOSC`,
  into Live's Remote Scripts dir, then enable it under `Preferences > Link / Tempo / MIDI`:
    - Windows: `\Users\<user>\Documents\Ableton\User Library\Remote Scripts`
    - macOS: `~/Music/Ableton/User Library/Remote Scripts`
- **Reload code without restarting Live:** send OSC `/live/api/reload` (the console clients send
  this on startup). This re-imports the handler modules and re-registers all handlers.
- **Tests** (`pip3 install pytest` first) **require a running Live instance** — there is no
  headless/CI path. Live must have a blank default set, default audio in/out devices, and
  `Preferences > Record, Warp & Launch > Count-In = None`. Run from the repo root:
    - All tests: `pytest`
    - Single file: `pytest tests/test_track.py`
    - Single test: `pytest tests/test_track.py::test_track_property_mute`
- **Interactive console** (against a running Live): `python run-console.py` — a REPL that sends
  `/live/...` commands and prints replies.
- **View Live's boot log** for compile/load errors: see `CONTRIBUTING.md` (greps the Live Usage
  log for `AbletonOSC`).

## Architecture

**Entry point & lifecycle.** Live calls `create_instance()` in `__init__.py`, which returns a
`Manager` (`manager.py`, a `ControlSurface` subclass). `Manager.__init__` creates the OSC server
and schedules `tick()`. **Everything runs single-threaded on a ~100ms tick** — Live beachballs if
you start a thread, so `Manager.tick()` calls `osc_server.process()` (a non-blocking socket drain)
and reschedules itself every tick. Do not introduce threads or blocking calls in handler code.

**Custom OSC server.** `abletonosc/osc_server.py` (`OSCServer`) is a hand-rolled OSC server. The
vendored `pythonosc/` package is used **only** for message build/parse (and by the test client) —
it is checked in, not a pip dependency, so treat edits there as vendoring changes. `process()`
drains the UDP socket; `process_message()` dispatches to a registered handler by exact address, or,
if the address contains `*`, fans out to every handler matching the wildcard regex.

**Handlers.** Each subsystem is an `AbletonOSCHandler` subclass (`abletonosc/{song,track,clip,
clip_slot,device,scene,view,application,midimap}.py`), instantiated once in `Manager.init_api()`.
Each subclass's `init_api()` registers callbacks via `self.osc_server.add_handler(address, fn)`.
The base class (`abletonosc/handler.py`) provides the generic wrappers that most endpoints reuse:
`_call_method`, `_set_property`, `_get_property`, `_start_listen`, `_stop_listen`.

**Address & reply conventions** (these are a public contract — tests assert exact reply tuples):
- Addresses are `/live/<object>/<action>/<property>`, e.g. `/live/track/set/volume`.
- **Getters echo their context params back, then the value.** A `_get_property` returns
  `(value, *params)`, and the per-object wrapper factories (`create_track_callback` in `track.py`,
  `create_clip_callback` in `clip.py`, and the equivalents in `clip_slot.py`/`device.py`/
  `scene.py`) resolve the object index, call the handler, and **prepend that index to the reply**.
  So a reply to `/live/clip/get/is_playing 0 0` is `(0, 0, True)`.
- **Setters and methods return `None` → no reply is sent** (unless the request is correlated; see
  below).
- **Listeners reuse the getter address.** `start_listen/<prop>` pushes unsolicited updates to
  `/live/<object>/get/<prop>` with the same `(*params, *value)` shape as a query reply.

**Transport.** Listens on UDP **11000**, replies on **11001**. Query/command replies are addressed
to the host that sent the request. *Unsolicited* messages (listeners, beat, `/live/error`,
`/live/startup`) go to the last client that sent anything (`OSCServer._remote_addr` is overwritten
on every received packet) — i.e. multiple simultaneous clients are not properly supported for
async traffic. This is a known limitation, deliberately out of scope.

**Tests.** `tests/` is a subpackage with relative imports; `tests/__init__.py` sends
`/live/api/reload` before the suite runs and exposes the `client` fixture. The test harness is the
real client library `client/client.py` (`AbletonOSCClient`), using `query()` /
`await_message()` / `send_bundle()`.

### Gotcha: handlers receive `params` as a `list`

`process_message` passes the incoming params to handlers as a **list**, and several handlers depend
on that. In particular `create_track_callback` (`track.py`) builds the forwarded params by list
concatenation — historically `[track_index] + params[1:]`. **Do not normalise `params` to a tuple
centrally in `osc_server.py`** without auditing every per-object factory: a previous attempt to do
so raised `TypeError` and was reverted. (The track factory has since been made tuple-safe with
`(track_index, *params[1:])`, but the per-object factories still have mildly inconsistent param
handling — a deliberate non-goal to unify them right now.)

## Request correlation

Replies are matched only by OSC address, which makes it hard to pair a reply with its request when
several are in flight — especially concurrent queries to the *same* address. AbletonOSC supports an
**opt-in correlation marker** to solve this. (This replaced an earlier, broken "custom fields" hack
that overloaded OSC `Nil`/`None` as a delimiter — unusable because `None` is real data in this API,
e.g. empty clip slots and inaccessible properties.)

**Protocol.** A client may prepend a single reserved string argument `@id:<token>` as the **first**
param of any request. The server strips it before the handler runs and re-prepends the identical
string to the reply, so the client can match them:

```
REQUEST  /live/clip/get/is_playing   "@id:42"  0 0
REPLY    /live/clip/get/is_playing   "@id:42"  0 0 True
```

A correlated **command** (`set`/method, which normally sends nothing) instead returns a marker-only
**acknowledgement**, so completion can be confirmed:

```
REQUEST  /live/song/set/tempo   "@id:7"   125.0
REPLY    /live/song/set/tempo   "@id:7"
```

**Where it lives:**
- Server: `abletonosc/osc_server.py`. `CORRELATION_PREFIX = "@id:"` (module-level so it survives
  `importlib.reload`). `process_message()` strips a leading `@id:` marker into `corr`; the `_reply()`
  helper re-prepends it. Both the exact-match and wildcard branches route through `_reply()`. The
  marker-gated ACK is sent only when a correlated command returns `None`.
- Client: `client/client.py`. Its `CORRELATION_PREFIX` **must match** the server's. `query()`
  allocates a unique `@id:<n>` token, registers a one-shot waiter keyed by that token, and prepends
  the marker; `handle_osc()` demuxes replies by token (not address). This makes `query()`
  **concurrency-safe**, including for the same address. Messages with no marker (listeners, beat,
  errors, legacy replies) fall through to the existing address-keyed dispatch.

**Invariants to preserve:**
- The feature is **opt-in and invisible**: a request without an `@id:` marker behaves exactly as
  before, and a non-correlated command still sends no reply. Don't break this — the existing test
  suite pins exact non-correlated reply shapes.
- The `@id:` leading-string namespace is **reserved**; the marker is detected purely by the
  `@id:` prefix on the first arg.
- Handlers never see the marker — encode/decode is centralized in `OSCServer` and the client, so
  no per-handler changes are needed to support correlation.

Tests for this live in `tests/test_correlation.py` (marker invisibility, concurrency, `None`
preservation, command ACK, opt-in, wildcard, backward-compat).
