#--------------------------------------------------------------------------------
# Test package marker.
#
# The headless tiers install the Live / ableton.v2 / _Framework sys.modules stubs via
# conftest.py (and each tier's own module-level install_live_stubs()), and every tier
# defines its own fixtures + JSON-RPC client. There is intentionally no global client or
# /live/api/reload step here -- those were OSC-era and have been retired with the OSC
# layer (see abletonosc/osc_server.py).
#--------------------------------------------------------------------------------
