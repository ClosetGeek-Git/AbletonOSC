#--------------------------------------------------------------------------------
# Transport teardown -- deterministic shutdown, no port/thread leak on Live reload.
# Transport-level (pyzmtp ROUTER + ZmtpTransport), independent of the JSON-RPC wire
# above it. Migrated from the retired OSC headless tier; NO Ableton Live.
#--------------------------------------------------------------------------------

import threading

import pytest

from ._live_stubs import install_live_stubs
install_live_stubs()

from ..abletonosc.osc_server import OSCServer
from ..client.jsonrpc_client import JsonRpcClient


def _bound_server():
    server = OSCServer(endpoint="tcp://127.0.0.1:0")
    port = int(server.bound_endpoint.rsplit(":", 1)[1])
    return server, port


def test_server_shutdown_does_not_hang():
    server, _port = _bound_server()
    done = threading.Event()

    def shutdown():
        server.shutdown()
        done.set()

    t = threading.Thread(target=shutdown)
    t.start()
    t.join(timeout=3)
    assert done.is_set(), "OSCServer.shutdown() hung"


def test_transport_shutdown_releases_thread_and_port():
    server, _port = _bound_server()
    ep = server.bound_endpoint
    loop_thread = server._transport._thread
    server.shutdown()
    assert loop_thread.is_alive() is False, "transport loop thread did not exit"
    # Port released -> a fresh ROUTER can re-bind the same endpoint.
    server2 = OSCServer(endpoint=ep)
    try:
        assert server2.bound_endpoint == ep
    finally:
        server2.shutdown()


def test_client_close_does_not_hang():
    server, port = _bound_server()
    try:
        client = JsonRpcClient(port=port)
        done = threading.Event()

        def close():
            client.close()
            done.set()

        t = threading.Thread(target=close)
        t.start()
        t.join(timeout=3)
        assert done.is_set(), "JsonRpcClient.close() hung"
    finally:
        server.shutdown()
