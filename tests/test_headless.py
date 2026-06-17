#--------------------------------------------------------------------------------
# Headless protocol tests for the OSC server + client.
#
# Unlike the rest of the suite, these need NO running Ableton Live: abletonosc's
# osc_server.py and client/client.py import only the vendored pythonosc + stdlib.
# The handler modules DO import `Live` / `ableton.v2`, and abletonosc/__init__.py
# imports them all, so we install minimal sys.modules stubs *before* importing the
# package. This makes the correlation / multi-client routing / tagging wire logic
# CI-runnable for the first time.
#
# Three groups:
#   A. server logic, driven by process_message() with send() captured
#   B. one real client over UDP loopback (query / query_all / errors / tagging)
#   C. handler listener registry: multi-client routing + drop_client teardown
#--------------------------------------------------------------------------------

import sys
import time
import types
import socket
import threading

import pytest


def _install_live_stubs():
    """Register just enough of Live / ableton.v2 / _Framework to import the package."""
    def _mod(name):
        m = sys.modules.get(name)
        if m is None:
            m = types.ModuleType(name)
            sys.modules[name] = m
        return m

    class _Stub:
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, *args, **kwargs):
            return _Stub()
        def __getattr__(self, name):
            return _Stub()

    live = _mod("Live")
    # Runtime attribute access (Live.Application...) returns a permissive stub.
    live.__getattr__ = lambda name: _Stub()

    _mod("ableton")
    v2 = _mod("ableton.v2")
    cs = _mod("ableton.v2.control_surface")
    comp = _mod("ableton.v2.control_surface.component")

    class Component:
        # Real class so AbletonOSCHandler can subclass it and call super().__init__().
        def __init__(self, *args, **kwargs):
            pass

    comp.Component = Component
    cs.Component = Component
    cs.ControlSurface = Component
    sys.modules["ableton"].v2 = v2
    v2.control_surface = cs
    cs.component = comp

    fw = _mod("_Framework")
    enc = _mod("_Framework.EncoderElement")
    enc.EncoderElement = _Stub
    fw.EncoderElement = enc


_install_live_stubs()

#--------------------------------------------------------------------------------
# Imports that require the stubs (server package) or the vendored osc lib.
#--------------------------------------------------------------------------------
from ..client.client import AbletonOSCClient, TICK_DURATION, CORRELATION_PREFIX, TAG_PREFIX, ERROR_ADDRESS
from ..abletonosc.osc_server import OSCServer
from ..abletonosc.handler import AbletonOSCHandler
from ..pythonosc.osc_message import OscMessage
from ..pythonosc.osc_message_builder import OscMessageBuilder


def _make_message(address, params):
    builder = OscMessageBuilder(address)
    for param in params:
        builder.add_arg(param)
    return OscMessage(builder.build().dgram)


def _capture_server(response_port=11001):
    """A server bound to an ephemeral port, with send() captured into a list."""
    server = OSCServer(local_addr=('127.0.0.1', 0), remote_addr=('127.0.0.1', response_port))
    sent = []
    server.send = lambda address, params=(), remote_addr=None: sent.append((address, tuple(params), remote_addr))
    return server, sent


def _dispatch(server, address, params, from_host='1.2.3.4', from_port=5005):
    server.process_message(_make_message(address, params), (from_host, from_port))


#================================================================================
# Group A: server logic (process_message + captured send)
#================================================================================

def test_marker_stripped_and_echoed():
    server, sent = _capture_server()
    server.add_handler("/x", lambda params: ("got", *params))
    _dispatch(server, "/x", ("@id:7", 1, 2))
    assert sent == [("/x", ("@id:7", "got", 1, 2), ('1.2.3.4', 11001))]

def test_uncorrelated_reply_has_no_marker():
    server, sent = _capture_server()
    server.add_handler("/x", lambda params: ("got", *params))
    _dispatch(server, "/x", (1, 2))
    assert sent == [("/x", ("got", 1, 2), ('1.2.3.4', 11001))]

def test_correlated_command_ack():
    server, sent = _capture_server()
    server.add_handler("/set", lambda params: None)
    _dispatch(server, "/set", ("@id:9", 5))
    assert sent == [("/set", ("@id:9",), ('1.2.3.4', 11001))]

def test_uncorrelated_command_no_reply():
    server, sent = _capture_server()
    server.add_handler("/set", lambda params: None)
    _dispatch(server, "/set", (5,))
    assert sent == []

def test_correlated_handler_error_replies_on_error_address():
    server, sent = _capture_server()
    def boom(params):
        raise ValueError("kaboom")
    server.add_handler("/boom", boom)
    _dispatch(server, "/boom", ("@id:3",))
    assert len(sent) == 1
    address, params, remote = sent[0]
    assert address == ERROR_ADDRESS
    assert params[0] == "@id:3"
    assert "kaboom" in params[1]
    assert remote == ('1.2.3.4', 11001)

def test_uncorrelated_handler_error_reraises():
    server, sent = _capture_server()
    def boom(params):
        raise ValueError("kaboom")
    server.add_handler("/boom", boom)
    with pytest.raises(ValueError):
        _dispatch(server, "/boom", ())
    # No marker-carrying reply for an uncorrelated error; it propagates to process().
    assert sent == []

def test_correlated_unknown_address_errors():
    server, sent = _capture_server()
    _dispatch(server, "/nope", ("@id:4",))
    assert len(sent) == 1
    address, params, _ = sent[0]
    assert address == ERROR_ADDRESS and params[0] == "@id:4"

def test_uncorrelated_unknown_address_silent():
    server, sent = _capture_server()
    _dispatch(server, "/nope", ())
    assert sent == []

def test_tag_on_command_gets_no_ack():
    # @tag: is for start_listen; on a None-returning command it must NOT produce an ack.
    server, sent = _capture_server()
    server.add_handler("/set", lambda params: None)
    _dispatch(server, "/set", ("@tag:2", 5))
    assert sent == []

def test_request_context_published_during_dispatch():
    server, sent = _capture_server()
    seen = {}
    def grab(params):
        seen["addr"] = server.current_request_addr()
        seen["tag"] = server.current_request_tag()
        return None
    server.add_handler("/grab", grab)
    _dispatch(server, "/grab", ("@tag:5", 1))
    assert seen["addr"] == ('1.2.3.4', 11001)
    assert seen["tag"] == "@tag:5"
    # Cleared after dispatch.
    assert server.current_request_addr() is None
    assert server.current_request_tag() is None

def test_overlong_marker_is_treated_as_data():
    server, sent = _capture_server()
    server.add_handler("/x", lambda params: ("seen", *params))
    long_marker = CORRELATION_PREFIX + ("x" * 70)
    _dispatch(server, "/x", (long_marker,))
    # Not stripped: passed through to the handler, no marker echoed.
    assert sent == [("/x", ("seen", long_marker), ('1.2.3.4', 11001))]

def test_wildcard_fans_out_with_marker():
    server, sent = _capture_server()
    server.add_handler("/a/x", lambda params: ("X",))
    server.add_handler("/a/y", lambda params: ("Y",))
    _dispatch(server, "/a/*", ("@id:1",))
    addrs = sorted((a, p) for (a, p, _) in sent)
    assert addrs == [("/a/x", ("@id:1", "X")), ("/a/y", ("@id:1", "Y"))]

def test_broadcast_targets_all_known_clients():
    server, sent = _capture_server()
    server._note_client(('10.0.0.1', 1111))
    server._note_client(('10.0.0.2', 2222))
    server.broadcast("/live/startup")
    targets = sorted(remote for (_, _, remote) in sent)
    assert targets == [('10.0.0.1', 11001), ('10.0.0.2', 11001)]

def test_broadcast_falls_back_when_no_clients():
    server, sent = _capture_server(response_port=11001)
    server.broadcast("/live/startup")
    assert sent == [("/live/startup", (), server._remote_addr)]


#================================================================================
# Group B: one real client over UDP loopback
#================================================================================

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_loopback():
    server_port = _free_port()
    client_port = _free_port()
    server = OSCServer(local_addr=('127.0.0.1', server_port),
                       remote_addr=('127.0.0.1', client_port))

    stop = threading.Event()
    def pump():
        while not stop.is_set():
            server.process()
            time.sleep(0.005)
    thread = threading.Thread(target=pump, daemon=True)
    thread.start()

    client = AbletonOSCClient(hostname='127.0.0.1', port=server_port, client_port=client_port)
    try:
        yield server, client
    finally:
        stop.set()
        thread.join()
        client.stop()
        server.shutdown()


def test_roundtrip_query(live_loopback):
    server, client = live_loopback
    server.add_handler("/echo", lambda params: ("v", *params))
    assert client.query("/echo", (1,), timeout=TICK_DURATION * 4) == ("v", 1)

def test_roundtrip_concurrent_same_address(live_loopback):
    server, client = live_loopback
    server.add_handler("/echo", lambda params: ("v", *params))
    results = {}
    def do(i):
        results[i] = client.query("/echo", (i,), timeout=TICK_DURATION * 8)
    threads = [threading.Thread(target=do, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {i: ("v", i) for i in range(5)}

def test_roundtrip_command_ack(live_loopback):
    server, client = live_loopback
    server.add_handler("/cmd", lambda params: None)
    assert client.query("/cmd", (1,), timeout=TICK_DURATION * 4) == ()

def test_roundtrip_error_raises(live_loopback):
    server, client = live_loopback
    def boom(params):
        raise ValueError("nope")
    server.add_handler("/boom", boom)
    with pytest.raises(RuntimeError) as exc:
        client.query("/boom", timeout=TICK_DURATION * 4)
    assert "nope" in str(exc.value)

def test_roundtrip_query_all_collects(live_loopback):
    server, client = live_loopback
    server.add_handler("/a/x", lambda params: ("X",))
    server.add_handler("/a/y", lambda params: ("Y",))
    server.add_handler("/a/z", lambda params: ("Z",))
    replies = client.query_all("/a/*", timeout=TICK_DURATION * 4)
    got = sorted((addr, params) for (addr, params) in replies)
    assert got == [("/a/x", ("X",)), ("/a/y", ("Y",)), ("/a/z", ("Z",))]

def test_roundtrip_tagged_listener(live_loopback):
    server, client = live_loopback
    #--------------------------------------------------------------------------------
    # Emulate a start_listen handler: push an immediate value to the requesting client,
    # tagged if the request carried @tag:. Exercises current_request_addr/tag + the
    # client's persistent @tag: demux end-to-end.
    #--------------------------------------------------------------------------------
    def fake_start(params):
        addr = server.current_request_addr()
        tag = server.current_request_tag()
        payload = (123,) if tag is None else (tag, 123)
        server.send("/live/test/get/foo", payload, remote_addr=addr)
        return None
    server.add_handler("/live/test/start_listen/foo", fake_start)

    received = []
    handle = client.start_listen("/live/test/start_listen/foo", (), lambda a, p: received.append((a, p)))
    time.sleep(TICK_DURATION * 3)
    assert received == [("/live/test/get/foo", (123,))]
    client.stop_listen(handle)


#================================================================================
# Group C: handler listener registry (multi-client + drop_client)
#================================================================================

class _FakeTarget:
    def __init__(self, value=7):
        self.foo = value
        self._cbs = []
    def add_foo_listener(self, cb):
        self._cbs.append(cb)
    def remove_foo_listener(self, cb):
        self._cbs.remove(cb)
    def fire(self):
        for cb in list(self._cbs):
            cb()


class _BareHandler(AbletonOSCHandler):
    def init_api(self):
        pass


def _make_handler():
    server, sent = _capture_server()
    handler = _BareHandler(types.SimpleNamespace(osc_server=server))
    handler.class_identifier = "test"
    return server, sent, handler


def _set_request(server, host, tag=None):
    server._req_remote_addr = (host, server._response_port)
    server._req_tag = tag


def test_listener_routes_per_client():
    server, sent, handler = _make_handler()
    target = _FakeTarget(value=7)

    _set_request(server, '10.0.0.1')
    handler._start_listen(target, "foo", (0,))
    _set_request(server, '10.0.0.2')
    handler._start_listen(target, "foo", (0,))

    # Two independent listeners on the same property, distinct clients.
    assert len(target._cbs) == 2
    assert len(handler.listeners) == 2

    sent.clear()
    target.fire()
    targets = sorted(remote for (_, _, remote) in sent)
    assert targets == [('10.0.0.1', 11001), ('10.0.0.2', 11001)]
    # Each carries the untagged shape (track_index, value).
    for (address, params, _) in sent:
        assert address == "/live/test/get/foo" and params == (0, 7)

def test_stop_listen_only_affects_caller():
    server, sent, handler = _make_handler()
    target = _FakeTarget()
    _set_request(server, '10.0.0.1')
    handler._start_listen(target, "foo", (0,))
    _set_request(server, '10.0.0.2')
    handler._start_listen(target, "foo", (0,))

    _set_request(server, '10.0.0.1')
    handler._stop_listen(target, "foo", (0,))
    assert len(handler.listeners) == 1
    assert len(target._cbs) == 1
    remaining_key = next(iter(handler.listeners))
    assert remaining_key[-1] == ('10.0.0.2', 11001)

def test_drop_client_reaps_listeners():
    server, sent, handler = _make_handler()
    target = _FakeTarget()
    _set_request(server, '10.0.0.1')
    handler._start_listen(target, "foo", (0,))
    _set_request(server, '10.0.0.2')
    handler._start_listen(target, "foo", (0,))

    server._drop_client(('10.0.0.1', 11001))
    assert len(handler.listeners) == 1
    assert len(target._cbs) == 1

    sent.clear()
    target.fire()
    assert [remote for (_, _, remote) in sent] == [('10.0.0.2', 11001)]

def test_tagged_listener_prepends_tag():
    server, sent, handler = _make_handler()
    target = _FakeTarget(value=42)
    _set_request(server, '10.0.0.1', tag="@tag:9")
    sent.clear()
    handler._start_listen(target, "foo", (0,))
    # Immediate push is tagged.
    assert sent == [("/live/test/get/foo", ("@tag:9", 0, 42), ('10.0.0.1', 11001))]

def test_same_client_reregister_replaces():
    server, sent, handler = _make_handler()
    target = _FakeTarget()
    _set_request(server, '10.0.0.1')
    handler._start_listen(target, "foo", (0,))
    handler._start_listen(target, "foo", (0,))
    # Re-registration replaces: still exactly one listener / one Live callback.
    assert len(handler.listeners) == 1
    assert len(target._cbs) == 1

def test_component_registered_with_server():
    server, sent, handler = _make_handler()
    assert handler in server._components
