#--------------------------------------------------------------------------------
# T0a — LOM descriptor self-consistency (pure data; no Live, no socket).
# Proves the single source of truth (abletonosc/lom_schema.py) is well-formed, so
# the dispatcher and the test battery can rely on it.
#--------------------------------------------------------------------------------

from ..abletonosc import lom_schema
from ..abletonosc.lom_schema import OBJECTS, DIRECT, MIXER, SEND, PARAM, COMPUTED, LIST

VALID_KINDS = {DIRECT, MIXER, SEND, PARAM, COMPUTED, LIST}

# compute keys the dispatcher (jsonrpc.py) knows how to evaluate.
KNOWN_COMPUTE_PREFIXES = ("len:", "clips.", "arrangement_clips.", "devices.", "parameters.")
KNOWN_COMPUTE_EXACT = {"track_names", "cue_points", "version", "avg_process_usage",
                       "selected_scene", "selected_track", "selected_clip", "selected_device"}


def _compute_known(compute):
    return compute in KNOWN_COMPUTE_EXACT or any(compute.startswith(p) for p in KNOWN_COMPUTE_PREFIXES)


def test_every_object_has_arity_and_shape():
    for name, spec in OBJECTS.items():
        assert spec["arity"] in (0, 1, 2), name
        assert isinstance(spec.get("props", {}), dict), name
        assert isinstance(spec.get("methods", {}), dict), name


def test_every_prop_kind_is_valid():
    for obj, spec in OBJECTS.items():
        for pname, p in spec["props"].items():
            assert p.kind in VALID_KINDS, "%s.%s kind=%s" % (obj, pname, p.kind)
            assert p.readable or p.writable, "%s.%s neither readable nor writable" % (obj, pname)


def test_computed_and_list_have_known_compute_keys():
    for obj, spec in OBJECTS.items():
        for pname, p in spec["props"].items():
            if p.kind in (COMPUTED, LIST):
                assert p.compute is not None, "%s.%s missing compute" % (obj, pname)
                assert _compute_known(p.compute), "%s.%s unknown compute=%s" % (obj, pname, p.compute)
            else:
                assert p.compute is None, "%s.%s should not have compute" % (obj, pname)


def test_writable_implies_supported_kind():
    # Only DIRECT/MIXER/SEND/PARAM(value)/COMPUTED(selected_*) are settable.
    for obj, spec in OBJECTS.items():
        for pname, p in spec["props"].items():
            if p.writable and p.kind == COMPUTED:
                assert p.compute in ("selected_scene", "selected_track", "selected_clip",
                                     "selected_device"), "%s.%s writable computed" % (obj, pname)
            if p.writable:
                assert p.kind != LIST, "%s.%s LIST cannot be writable" % (obj, pname)


def test_observable_only_on_listenable_kinds():
    # Subscriptions resolve a listen target for DIRECT/MIXER/SEND/PARAM(value) and the
    # two view selected_* COMPUTED props. LIST and other COMPUTED are not observable.
    for obj, spec in OBJECTS.items():
        for pname, p in spec["props"].items():
            if not p.observable:
                continue
            if p.kind == COMPUTED:
                assert p.compute in ("selected_scene", "selected_track"), \
                    "%s.%s observable computed must be a view selection" % (obj, pname)
            else:
                assert p.kind in (DIRECT, MIXER, SEND, PARAM), \
                    "%s.%s observable kind=%s" % (obj, pname, p.kind)


def test_param_value_observable_others_not():
    dprops = OBJECTS["device"]["props"]
    assert dprops["parameter.value"].observable is True
    assert dprops["parameter.name"].observable is False
    assert dprops["parameter.value_string"].observable is False


def test_clip_declares_notes():
    assert OBJECTS["clip"].get("notes") is True
    # The dispatcher implements these note methods for clip.
    from ..abletonosc.jsonrpc import _NOTE_METHODS
    assert set(_NOTE_METHODS) == {"get_notes", "add_notes", "remove_notes", "remove_notes_by_id"}


def test_song_special_listener_beat():
    assert "beat" in OBJECTS["song"].get("special_listeners", [])


def test_export_schema_roundtrips():
    import json
    exp = lom_schema.export_schema()
    blob = json.dumps(exp)
    back = json.loads(blob)
    assert set(back.keys()) == set(OBJECTS.keys())
    # spot check a known prop survives with its flags
    assert back["track"]["props"]["volume"]["kind"] == MIXER
    assert back["clip"]["notes"] is True
