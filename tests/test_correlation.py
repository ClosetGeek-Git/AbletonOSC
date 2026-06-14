from . import client, wait_one_tick, TICK_DURATION
import threading

#--------------------------------------------------------------------------------
# Request correlation (the opt-in "@id:<token>" leading-string marker).
#
# These tests assume the default blank Live set used by the rest of the suite
# (at least 3 tracks, with tracks 0/1 usable for clips and track 2 for mixer
# properties), per CONTRIBUTING.md.
#--------------------------------------------------------------------------------

def _has_marker(seq):
    return any(isinstance(x, str) and x.startswith("@id:") for x in seq)

def test_correlation_marker_invisible(client):
    #--------------------------------------------------------------------------------
    # A correlated query returns the same value shape as before; the marker is
    # stripped on the way out and never leaks into the returned tuple.
    #--------------------------------------------------------------------------------
    rv = client.query("/live/song/get/tempo")
    assert isinstance(rv, tuple)
    assert len(rv) == 1
    assert not _has_marker(rv)

def test_correlation_concurrent_same_address(client):
    #--------------------------------------------------------------------------------
    # The headline fix: two queries to the SAME address, in flight at once, each
    # resolve to their own correct result. Impossible under the old address-keyed
    # one-shot handler, which the second query would have clobbered.
    #--------------------------------------------------------------------------------
    client.send_message("/live/track/set/volume", (0, 0.5))
    client.send_message("/live/track/set/volume", (1, 1.0))
    wait_one_tick()

    results = {}
    def do_query(track_id):
        results[track_id] = client.query("/live/track/get/volume", (track_id,),
                                         timeout=TICK_DURATION * 4)

    threads = [threading.Thread(target=do_query, args=(t,)) for t in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0] == (0, 0.5)
    assert results[1] == (1, 1.0)

def test_correlation_preserves_none(client):
    #--------------------------------------------------------------------------------
    # None is real data in this API (empty clip slots). Proves the correlation
    # layer no longer treats None as a delimiter, unlike the old hack.
    #--------------------------------------------------------------------------------
    track_id = 0
    client.send_message("/live/clip_slot/create_clip", (track_id, 0, 4))
    client.send_message("/live/clip/set/name", (track_id, 0, "Alpha"))
    wait_one_tick()

    rv = client.query("/live/track/get/clips/name", (track_id,), timeout=TICK_DURATION * 4)
    client.send_message("/live/clip_slot/delete_clip", (track_id, 0))

    assert rv[0] == track_id
    assert "Alpha" in rv
    assert None in rv
    assert not _has_marker(rv)

def test_correlation_command_ack(client):
    #--------------------------------------------------------------------------------
    # A correlated set/method gets a marker-only acknowledgement (empty payload)
    # instead of timing out.
    #--------------------------------------------------------------------------------
    rv = client.query("/live/song/set/tempo", (125.0,), timeout=TICK_DURATION * 4)
    assert rv == ()
    wait_one_tick()
    assert client.query("/live/song/get/tempo") == (125.0,)

def test_correlation_command_no_ack_when_uncorrelated(client):
    #--------------------------------------------------------------------------------
    # ...but a NON-correlated set still produces no reply at all (opt-in), so
    # existing clients (e.g. TouchOSC setting a fader) are unaffected.
    #--------------------------------------------------------------------------------
    count = 0
    def on_reply(address, params):
        nonlocal count
        count += 1

    client.set_handler("/live/song/set/tempo", on_reply)
    client.send_message("/live/song/set/tempo", (120.0,))
    wait_one_tick()
    wait_one_tick()
    client.remove_handler("/live/song/set/tempo")
    assert count == 0

def test_correlation_wildcard(client):
    #--------------------------------------------------------------------------------
    # Wildcard queries are now correlated too. Before the fix, the marker leaked
    # into the handler as the first param, so every sub-callback raised and the
    # query timed out; now it round-trips and the reply is marker-stripped.
    #--------------------------------------------------------------------------------
    track_id, clip_id = 0, 0
    client.send_message("/live/clip_slot/create_clip", (track_id, clip_id, 4))
    wait_one_tick()

    rv = client.query("/live/clip/get/*", (track_id, clip_id), timeout=TICK_DURATION * 4)
    client.send_message("/live/clip_slot/delete_clip", (track_id, clip_id))

    assert isinstance(rv, tuple)
    assert rv[0:2] == (track_id, clip_id)
    assert not _has_marker(rv)

def test_correlation_backward_compatible_client(client):
    #--------------------------------------------------------------------------------
    # The legacy pattern (no marker, await by address) still works against the
    # new server.
    #--------------------------------------------------------------------------------
    client.send_message("/live/song/get/tempo")
    rv = client.await_message("/live/song/get/tempo", TICK_DURATION * 4)
    assert isinstance(rv, tuple)
    assert len(rv) == 1
