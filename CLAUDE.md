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
- **Tests** (`pip3 install pytest` first). Most of the suite **requires a running Live instance** —
  Live must have a blank default set, default audio in/out devices, and
  `Preferences > Record, Warp & Launch > Count-In = None`. Run from the repo root:
    - All tests: `pytest`
    - Single file: `pytest tests/test_track.py`
    - Single test: `pytest tests/test_track.py::test_track_property_mute`
  - **Exception — headless tier:** `pytest tests/test_headless.py` needs **no Live**. It exercises the
    OSC server + client protocol logic (correlation, multi-client routing, listener tagging, error
    replies, dead-client teardown) over UDP loopback, installing `Live`/`ableton.v2` `sys.modules`
    stubs so the package imports. This is the CI-runnable safety net for that wire logic.
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
to the host that sent the request. *Unsolicited* messages are now routed per-client: each listener's
updates go to the client that registered it (captured at registration via the per-request context;
see "Multi-client async routing" below), and true broadcasts (`/live/startup`, `/live/error`, the
init-time `average_process_usage`) go to every client in `OSCServer._known_clients`. `_remote_addr`
remains only as the single-client fallback. **Caveat:** replies use the fixed response port 11001,
so two clients on the *same host* are not distinguishable for async traffic (per-host, not
per-(host,port)).

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

A correlated request that **fails** (handler raises, or unknown address) gets a marker-carrying error
reply on `/live/error`, so the client fails fast instead of timing out:

```
REQUEST  /live/api/show_message   "@id:8"
REPLY    /live/error   "@id:8"   "Error handling /live/api/show_message: ..."
```

**Where it lives:**
- Server: `abletonosc/osc_server.py`. `CORRELATION_PREFIX = "@id:"` and `LISTEN_TAG_PREFIX = "@tag:"`
  (module-level so they survive `importlib.reload`). `process_message()` strips a single leading
  marker — `@id:` into `corr`, or `@tag:` into the per-request tag — via one `if/elif` on `params[0]`
  (guarded by `MAX_MARKER_LENGTH`); `_reply()` re-prepends it. Both exact-match and wildcard branches
  route through `_reply()`. The marker-gated ACK fires only for `@id:` commands returning `None`.
  `_reply_error()` sends the correlated error on `ERROR_ADDRESS`.
- Client: `client/client.py`. Its prefixes **must match** the server's. `query()` allocates a unique
  `@id:<n>` token, registers a one-shot waiter keyed by that token, and prepends the marker;
  `query_all()` collects *all* replies for a token (wildcards). `handle_osc()` is a 3-way demux:
  `@id:` → one-shot `_pending` waiter, `@tag:` → persistent `_tags` callback, else → address-keyed
  dispatch (legacy listeners, beat, errors). `query()` **raises** on a reply addressed to
  `/live/error`. This makes `query()` concurrency-safe, including for the same address.

**Listener tagging (`@tag:`).** A `start_listen` request may carry `@tag:<token>`; every update for
that listener (the immediate push and all later changes) is prefixed with the tag, so a client can
demultiplex several listeners on the same getter address. A tagged `start_listen` is confirmed by its
immediate tagged push (no separate ack — the ACK is gated on `@id:`). Client API:
`start_listen(address, params, callback)` → handle, `stop_listen(handle)`. Untagged `start_listen` is
byte-identical to before.

**Multi-client async routing.** `OSCServer` publishes a *per-request context* — `_req_remote_addr` and
`_req_tag`, set at the top of `process_message` and cleared in a `finally` — readable via
`current_request_addr()` / `current_request_tag()`. This is safe **only** because of the
single-threaded tick (one message dispatched fully before the next). Listener registration
(`handler.py _start_listen`, `track.py _start_mixer_listen`, `device.py` device-parameter listener,
`song.py` beat) **captures these by value** into the listener's closure, so later async pushes (on
future ticks, when the context is gone) route to the right client. The listener registry is keyed
`(prop, tuple(params), client_addr)` with a uniform `(callback, unsubscribe)` value, so two clients
listening to the same property don't collide. Each handler registers as a component
(`OSCServer.register_component`); a client detected unreachable on send (`_dead_pending` →
`_drop_client` → `component.drop_client(addr)`) has all its listeners reaped. Broadcasts use
`OSCServer.broadcast()` over the bounded `_known_clients` set.

**Invariants to preserve:**
- The feature is **opt-in and invisible**: a request without a marker behaves exactly as before, an
  untagged `start_listen` pushes the same untagged shape, and a non-correlated command sends no reply.
  Don't break this — the suite pins exact non-correlated/untagged reply shapes.
- The `@id:`/`@tag:` leading-string namespaces are **reserved**; markers are detected purely by prefix
  on the first arg (length-capped).
- Handlers never see the marker — encode/decode is centralized in `OSCServer` and the client.
- Listener closures must capture `remote_addr`/`tag` **at registration** (a local, not a late
  `self.osc_server._req_*` read), or async pushes mis-route. Beat is the one fan-out exception (one
  global Live listener → a `_beat_subscribers` map).

**Reload caveat.** `OSCServer` is created once in `Manager.__init__` and is **not** rebuilt by
`/live/api/reload` (which re-imports modules + rebuilds handlers). So changes to `osc_server.py`
require a **full Live restart**; handler-only changes hot-reload.

Tests: `tests/test_headless.py` runs the full correlation/routing/tagging/error/teardown protocol with
**no Ableton** (sys.modules stubs + UDP loopback) — the CI-runnable tier. `tests/test_correlation.py`
and `tests/test_tagging.py` confirm the same against a real Live instance.
