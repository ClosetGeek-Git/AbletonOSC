#--------------------------------------------------------------------------------
# T1 -- JSON-RPC over a REAL socket, NO Ableton Live.
#
# A real pyzmq DEALER (client/jsonrpc_client.py) talks to AbletonOSC's pyzmtp ROUTER
# (real OSCServer + ZmtpTransport on its asyncio thread) over tcp loopback; process()
# is pumped on a thread; the descriptor-driven JsonRpcHandler dispatches against the
# same fake LOM tree the T0 battery uses. This proves the end-to-end wire -- envelope
# encode -> transport -> dispatch_frame -> dispatcher -> send_json -> demux --
# without Live. (T0 proves dispatch logic in-process; T2 proves it against real Live.)
#--------------------------------------------------------------------------------

import threading
import time

import pytest

from ._live_stubs import install_live_stubs
install_live_stubs()

# Reuse the fake LOM tree + fake manager from the T0 battery (importing the module also
# installs the richer Live stubs -- Application version ints, Clip.MidiNoteSpecification).
from .test_jsonrpc import build_song, FakeManager
from ..abletonosc.osc_server import OSCServer
from ..abletonosc.jsonrpc import JsonRpcHandler
from ..client.jsonrpc_client import JsonRpcClient, LomRpcError


@pytest.fixture
def rig():
    server = OSCServer(endpoint="tcp://127.0.0.1:0")
    port = int(server.bound_endpoint.rsplit(":", 1)[1])
    # JsonRpcHandler.__init__ -> init_api() hooks server.json_dispatcher = handler and
    # registers it as a component (so drop_client reaps its listeners).
    handler = JsonRpcHandler(FakeManager(server))
    handler.song = build_song()

    stop = threading.Event()

    def pump():
        while not stop.is_set():
            server.process()
            time.sleep(0.005)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()

    clients = []

    def make_client(**kwargs):
        c = JsonRpcClient(port=port, timeout=2.0, **kwargs)
        clients.append(c)
        return c

    try:
        yield server, handler, make_client
    finally:
        stop.set()
        thread.join(timeout=2)
        for c in clients:
            try:
                c.close()
            except Exception:
                pass
        server.shutdown()


#================================================================================
# Round-trips over the real socket
#================================================================================

def test_get_scalar(rig):
    _, _, make_client = rig
    c = make_client()
    assert c.get("song.tempo") == 120.0
    assert c.get("track.0.name") == "MIDI-A"

def test_get_mixer_send_param(rig):
    _, _, make_client = rig
    c = make_client()
    assert c.get("track.0.volume") == 0.8
    assert c.get("track.0.send.0") == 0.1
    assert c.get("device.0.0.parameter.1.value") == 0.2

def test_set_roundtrips(rig):
    _, handler, make_client = rig
    c = make_client()
    assert c.set("song.tempo", 140.0) is True
    assert c.get("song.tempo") == 140.0
    assert handler.song.tempo == 140.0

def test_computed_and_list(rig):
    _, _, make_client = rig
    c = make_client()
    assert c.get("application.version") == [12, 0]
    assert c.get("song.num_tracks") == 2
    assert c.get("track.0.clips.name") == ["MidiClipA", None]

def test_multi_get(rig):
    _, _, make_client = rig
    c = make_client()
    assert c.get(["song.tempo", "track.0.name"]) == [120.0, "MIDI-A"]

def test_call_method(rig):
    _, handler, make_client = rig
    c = make_client()
    assert c.call("track.0.stop_all_clips") is True
    assert ("stop_all_clips", ()) in handler.song.tracks[0].__dict__["calls"]

def test_ping(rig):
    _, _, make_client = rig
    c = make_client()
    assert c.ping() == "ok"

def test_batch(rig):
    _, handler, make_client = rig
    c = make_client()
    out = c.batch([
        {"op": "get", "path": "track.0.name"},
        {"op": "get", "path": "track.1.name"},
        {"op": "set", "path": "song.tempo", "value": 128.0},
    ])
    assert out == ["MIDI-A", "Audio-A", True]
    assert handler.song.tempo == 128.0

def test_notes_roundtrip(rig):
    _, _, make_client = rig
    c = make_client()
    notes = [{"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100,
              "probability": 0.8, "velocity_deviation": 5.0, "release_velocity": 70}]
    ids = c.call("clip.0.0.add_notes", [notes])
    assert ids == [1]
    got = c.call("clip.0.0.get_notes")
    assert len(got) == 1 and got[0]["pitch"] == 60 and got[0]["probability"] == 0.8
    c.call("clip.0.0.remove_notes_by_id", [[1]])
    assert c.call("clip.0.0.get_notes") == []


#================================================================================
# Errors propagate as exceptions
#================================================================================

def test_error_unknown_object(rig):
    _, _, make_client = rig
    c = make_client()
    with pytest.raises(LomRpcError) as exc:
        c.get("track.99.volume")
    assert exc.value.code == 404

def test_error_readonly_set(rig):
    _, _, make_client = rig
    c = make_client()
    with pytest.raises(LomRpcError) as exc:
        c.set("song.is_playing", True)
    assert exc.value.code == 400


#================================================================================
# Subscriptions push over the socket and route per-client
#================================================================================

def test_subscribe_immediate_and_on_change(rig):
    _, handler, make_client = rig
    c = make_client()
    received = []
    ev = threading.Event()

    def cb(value, path):
        received.append((path, value))
        ev.set()

    c.subscribe("track.0.volume", cb)
    # immediate push on subscribe
    assert ev.wait(2.0), "no immediate subscription push"
    assert received[-1] == ("track.0.volume", 0.8)

    # mutate server-side and emit -> the listener routes a push to this client
    ev.clear()
    vol = handler.song.tracks[0].mixer_device.volume
    vol.value = 0.42
    vol._emit("value")
    assert ev.wait(2.0), "no push after change"
    assert received[-1] == ("track.0.volume", 0.42)

def test_unsubscribe_silences(rig):
    _, handler, make_client = rig
    c = make_client()
    received = []
    ev = threading.Event()
    sub = c.subscribe("track.0.mute", lambda v, p: (received.append(v), ev.set()))
    assert ev.wait(2.0)                       # immediate push
    c.unsubscribe(sub)
    time.sleep(0.2)                           # let the unsubscribe land server-side
    received.clear()
    handler.song.tracks[0]._emit("mute")
    time.sleep(0.3)
    assert received == []


#================================================================================
# Multi-client routing + reaping (shared transport machinery, JSON wire)
#================================================================================

def test_two_clients_distinct_identities(rig):
    server, _, make_client = rig
    a = make_client(identity="lom-A")
    b = make_client(identity="lom-B")
    assert a.get("track.0.name") == "MIDI-A"
    assert b.get("track.1.name") == "Audio-A"
    assert b"lom-A" in server._known_clients
    assert b"lom-B" in server._known_clients

def test_disconnect_reaps_listeners(rig):
    server, handler, make_client = rig
    c = make_client(identity="lom-bye")
    c.subscribe("track.0.volume", lambda v, p: None)
    # let the subscribe register server-side
    deadline = time.time() + 2.0
    while not handler.listeners and time.time() < deadline:
        time.sleep(0.02)
    assert handler.listeners, "listener never registered"

    c.close()                                # clean DEALER close -> connection_lost
    deadline = time.time() + 4.0
    while handler.listeners and time.time() < deadline:
        time.sleep(0.02)
    assert not handler.listeners, "listeners not reaped on disconnect"
