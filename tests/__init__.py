import time
import pytest

#--------------------------------------------------------------------------------
# Add . to the path so that pythonosc can be imported, enabling unit testing
# without any external dependencies
#--------------------------------------------------------------------------------
import sys
sys.path.append(".")

from ..client import AbletonOSCClient, TICK_DURATION

# Live tick is 100ms. TICK_DURATION (imported from client.py) is the single source of
# truth for that interval plus a short processing buffer; do not redefine it here.

@pytest.fixture(scope="module")
def client() -> AbletonOSCClient:
    client = AbletonOSCClient()
    yield client
    client.stop()

def wait_one_tick():
    """
    Sleep for one Ableton Live tick (100ms).
    """
    time.sleep(TICK_DURATION)

c = AbletonOSCClient()
c.send_message("/live/api/reload")
c.stop()
