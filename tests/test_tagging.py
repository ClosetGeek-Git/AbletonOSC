from . import client, wait_one_tick, TICK_DURATION

#--------------------------------------------------------------------------------
# Listener tagging (the opt-in "@tag:<token>" marker) against a real Live instance.
#
# Note on multi-client routing: AbletonOSC replies on a fixed port (11001), so two
# clients cannot both run on one host. True per-client routing is therefore covered
# at the protocol level in tests/test_headless.py (which simulates distinct clients);
# here we verify, against real Live, that tagging lets a SINGLE client demultiplex
# several concurrent listeners -- including multiple listeners on the SAME address.
#--------------------------------------------------------------------------------

def test_tagged_listener_immediate_value(client):
    received = []
    handle = client.start_listen("/live/song/start_listen/tempo", (),
                                 lambda address, params: received.append(params))
    wait_one_tick()
    wait_one_tick()
    client.stop_listen(handle)
    #--------------------------------------------------------------------------------
    # The immediate push arrives, with the tag already stripped by the client demux.
    #--------------------------------------------------------------------------------
    assert len(received) >= 1
    assert all(not (p and isinstance(p[0], str) and p[0].startswith("@tag:")) for p in received)

def test_two_tagged_listeners_same_address_demux(client):
    #--------------------------------------------------------------------------------
    # Two tagged listeners on the same address (volume of tracks 0 and 1) must not
    # collide: each callback sees only its own track's updates. Impossible to
    # disambiguate by address alone (both push to /live/track/get/volume).
    #--------------------------------------------------------------------------------
    got = {0: [], 1: []}
    h0 = client.start_listen("/live/track/start_listen/volume", (0,),
                             lambda address, params: got[0].append(params))
    h1 = client.start_listen("/live/track/start_listen/volume", (1,),
                             lambda address, params: got[1].append(params))
    wait_one_tick()
    wait_one_tick()

    client.send_message("/live/track/set/volume", (0, 0.4))
    client.send_message("/live/track/set/volume", (1, 0.6))
    wait_one_tick()
    wait_one_tick()

    client.stop_listen(h0)
    client.stop_listen(h1)

    #--------------------------------------------------------------------------------
    # Each listener only ever saw its own track index, and saw its own new value.
    #--------------------------------------------------------------------------------
    assert got[0] and all(p[0] == 0 for p in got[0])
    assert got[1] and all(p[0] == 1 for p in got[1])
    assert any(abs(p[-1] - 0.4) < 1e-6 for p in got[0])
    assert any(abs(p[-1] - 0.6) < 1e-6 for p in got[1])

def test_untagged_listener_unaffected(client):
    #--------------------------------------------------------------------------------
    # Backward-compat: a legacy (untagged) start_listen still pushes on the bare get
    # address with no marker, caught by await_message.
    #--------------------------------------------------------------------------------
    client.send_message("/live/song/start_listen/tempo")
    rv = client.await_message("/live/song/get/tempo", TICK_DURATION * 4)
    client.send_message("/live/song/stop_listen/tempo")
    assert isinstance(rv, tuple)
    assert len(rv) == 1
    assert not (isinstance(rv[0], str) and rv[0].startswith("@tag:"))
