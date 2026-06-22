#--------------------------------------------------------------------------------
# Shared fake-LOM dev server -- the T1 loopback `rig` (tests/test_jsonrpc_loopback.py)
# extracted to a standalone, long-lived process so a NON-Python client (the PHP
# Closetgeek\Stemdj\Lom PHPUnit suite) can drive the REAL descriptor-driven dispatcher
# over a REAL ZMQ wire against the SAME fake LOM the Python headless tiers assert on.
#
# It REUSES, unchanged:
#   - tests.test_jsonrpc.build_song / FakeManager / _install_live_stubs  (the fake LOM)
#   - abletonosc.osc_server.OSCServer                                    (pyzmtp ROUTER)
#   - abletonosc.jsonrpc.JsonRpcHandler                                  (the dispatcher)
# so the PHP wire/battery/value assertions equal the Python loopback/battery/value
# assertions by construction (same build_song fake -> same expected values).
#
# Protocol toward the harness:
#   * On startup it binds tcp://127.0.0.1:0 and prints  "PORT <n>\n"  on stdout (flushed),
#     so the spawning bootstrap learns the ephemeral port. Readiness is then confirmed by
#     the client with an active {"op":"ping"} probe (-> "ok"), exactly as in-Live.
#   * It pumps OSCServer.process() until SIGTERM / SIGINT, then shuts the transport down
#     deterministically (no port/thread leak).
#   * One test-harness control op, intercepted BEFORE dispatch:
#         {"op":"__reset__"}  ->  _clear_listeners() + song = build_song()  ->  {"result":true}
#     This gives the PHP suite the same per-test fresh-fixture isolation the pytest
#     function-scoped `rig` fixture provides. It is test infrastructure only -- NOT a
#     descriptor op, and never reaches the dispatcher.
#
# Run it as a module so the package-relative imports resolve:
#     python -m AbletonOSC.tests.devserver        (cwd = parent of AbletonOSC)
# Running the file directly also works -- it re-enters itself as that module below.
#--------------------------------------------------------------------------------
import os
import sys


#--------------------------------------------------------------------------------
# Allow `python .../AbletonOSC/tests/devserver.py` (no package context): make the
# AbletonOSC package importable from its parent and re-run ourselves as the module
# `<pkg>.tests.devserver`, which DOES have the package context the relative imports
# below need. The re-run has a real __package__, so it skips this branch.
#--------------------------------------------------------------------------------
if not __package__:
    _tests_dir = os.path.dirname(os.path.realpath(__file__))      # .../AbletonOSC/tests
    _pkg_dir = os.path.dirname(_tests_dir)                        # .../AbletonOSC
    _parent = os.path.dirname(_pkg_dir)                           # .../<parent>
    _pkg = os.path.basename(_pkg_dir)                             # "AbletonOSC"
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    import runpy
    runpy.run_module("%s.tests.devserver" % _pkg, run_name="__main__", alter_sys=True)
    sys.exit(0)


import json
import signal
import threading
import time

#--------------------------------------------------------------------------------
# Importing test_jsonrpc runs its module-level _install_live_stubs() BEFORE it imports
# the Live-dependent dispatcher, so the order here is load-bearing: pull the fake LOM
# (which installs the richer stubs) first, then the server + dispatcher.
#--------------------------------------------------------------------------------
from .test_jsonrpc import build_song, FakeManager, _install_live_stubs
_install_live_stubs()  # idempotent; ensures Application version ints + Clip.MidiNoteSpecification
from ..abletonosc.osc_server import OSCServer
from ..abletonosc.jsonrpc import JsonRpcHandler


def main():
    #--------------------------------------------------------------------------------
    # Same wiring as test_jsonrpc_loopback.py::rig, but persistent: real OSCServer
    # (pyzmtp ROUTER on an ephemeral loopback port) + real JsonRpcHandler over the same
    # FakeManager + a fresh build_song().
    #--------------------------------------------------------------------------------
    server = OSCServer(endpoint="tcp://127.0.0.1:0")
    handler = JsonRpcHandler(FakeManager(server))
    handler.song = build_song()

    #--------------------------------------------------------------------------------
    # Intercept the test-harness control ops before the dispatcher sees them. The
    # OSCServer routes every inbound frame to self.json_dispatcher.handle(data, rid),
    # which is this handler; shadowing the bound method with this wrapper lets us serve:
    #
    #   {"op":"__reset__"}                       detach listeners + rebuild the fake LOM
    #                                            -> per-test fresh-fixture isolation (the
    #                                               out-of-process analogue of the pytest
    #                                               function-scoped `rig`).
    #   {"op":"__emit__","path":P,"value":V?}    set V on the listenable behind P (if any)
    #                                            and fire its listener -> the out-of-process
    #                                            analogue of the loopback test's in-process
    #                                            `param.value = V; param._emit("value")`,
    #                                            so on-change subscription pushes are testable.
    #
    # Both ack on the SAME routing-id; the actual subscription push (for __emit__) routes
    # asynchronously to whichever client registered the listener.
    #--------------------------------------------------------------------------------
    _dispatch = handler.handle

    def _ack(routing_id, env, reply):
        if env.get("id") is not None:
            reply["id"] = env["id"]
        server.send_json(routing_id, reply)

    def handle(data, routing_id):
        try:
            env = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data)
        except Exception:
            env = None
        if isinstance(env, dict):
            op = env.get("op")
            if op == "__reset__":
                handler._clear_listeners()
                handler.song = build_song()
                _ack(routing_id, env, {"result": True})
                return
            if op == "__emit__":
                try:
                    root, idxs, leaf = handler._split(env["path"])
                    obj = handler._resolve_object(root, idxs)
                    acc = handler._accessor(root, obj, leaf)
                    if "value" in env and acc.setter is not None:
                        acc.setter(env["value"])
                    if acc.listen_obj is not None and acc.listen_prop is not None:
                        acc.listen_obj._emit(acc.listen_prop)
                    _ack(routing_id, env, {"result": True})
                except Exception as e:
                    _ack(routing_id, env, {"error": {"code": 500, "message": str(e)}})
                return
        _dispatch(data, routing_id)

    handler.handle = handle

    #--------------------------------------------------------------------------------
    # Announce the bound ephemeral port (resolves the ":0") for the spawning bootstrap.
    #--------------------------------------------------------------------------------
    port = int(str(server.bound_endpoint).rsplit(":", 1)[1])
    sys.stdout.write("PORT %d\n" % port)
    sys.stdout.flush()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_a: stop.set())
    signal.signal(signal.SIGINT, lambda *_a: stop.set())

    #--------------------------------------------------------------------------------
    # Pump the transport bridge queues until asked to stop (this is the tick analogue;
    # all socket I/O happens on the transport's own asyncio thread).
    #--------------------------------------------------------------------------------
    try:
        while not stop.is_set():
            server.process()
            time.sleep(0.005)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
