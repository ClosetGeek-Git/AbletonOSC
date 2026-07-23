#--------------------------------------------------------------------------------
# Vendoring tier: AbletonOSC vendors pyzmtp as a pure-Python TOP-LEVEL package (like
# pythonosc). The crucial invariant -- the whole reason pyzmtp exists -- is that NO
# native code is vendored: Live's static Python cannot load any .so/.dylib. Wire-compat
# with real libzmq is proven by pyzmtp's OWN suite (tests/test_capabilities.py); this
# file only proves AbletonOSC's vendoring + the ZmtpTransport bind/shutdown are sound.
#--------------------------------------------------------------------------------
import os
import sys
import glob

from ._live_stubs import install_live_stubs
install_live_stubs()

_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pyzmtp


def test_pyzmtp_is_vendored_top_level():
    assert os.path.commonpath([os.path.realpath(pyzmtp.__file__), _REPO]) == _REPO
    assert os.path.realpath(pyzmtp.__file__) == os.path.join(_REPO, "pyzmtp", "__init__.py")

def test_no_native_code_vendored():
    sos = glob.glob(os.path.join(_REPO, "pyzmtp", "**", "*.so"), recursive=True)
    dylibs = glob.glob(os.path.join(_REPO, "pyzmtp", "**", "*.dylib"), recursive=True)
    assert sos == [] and dylibs == [], "pyzmtp must be pure Python; found %s %s" % (sos, dylibs)

def test_api_surface_present():
    for attr in ("Context", "ROUTER", "DEALER", "RouterSocket", "DealerSocket",
                 "PeerEvent", "HostUnreachable", "ZMTPError"):
        assert hasattr(pyzmtp, attr), "pyzmtp missing %s" % attr

def test_transport_binds_and_shuts_down():
    from ..abletonosc.zmtp_transport import ZmtpTransport
    t = ZmtpTransport("tcp://127.0.0.1:0")
    try:
        assert t.bound_endpoint and t.bound_endpoint.startswith("tcp://127.0.0.1:")
        assert int(t.bound_endpoint.rsplit(":", 1)[1]) > 0
    finally:
        t.shutdown()
