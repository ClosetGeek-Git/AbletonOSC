#--------------------------------------------------------------------------------
# Minimal Live / ableton.v2 / _Framework stubs so the abletonosc package imports with
# NO running Ableton. Shared by the headless test tiers. Idempotent.
#--------------------------------------------------------------------------------
import sys
import types


def install_live_stubs():
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
