import pytest

#--------------------------------------------------------------------------------
# Install the Live / ableton.v2 / _Framework sys.modules stubs at conftest import
# time -- BEFORE pytest collects any test module -- so that importing the package
# (which eagerly pulls in the Live-dependent handlers via abletonosc/__init__.py)
# succeeds headlessly during collection. Idempotent; the richer per-file stubs in
# test_jsonrpc.py run afterwards and take precedence.
#--------------------------------------------------------------------------------
from ._live_stubs import install_live_stubs
install_live_stubs()

#--------------------------------------------------------------------------------
# The live tier (tests/test_live_battery.py) drives a real, already-running Ableton
# Live + the committed AbletonOSCTest fixture -- slow and side-effectful. It is OPT-IN:
# skipped unless `--run-live` is passed, so the headless suite (and a plain `pytest`)
# never touches Ableton.
#--------------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run the live Ableton tests (launches/quits Ableton Live)")


def pytest_configure(config):
    config.addinivalue_line("markers",
                            "live: requires a running Ableton Live (slow, side-effectful)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live (launches/quits Ableton Live)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
