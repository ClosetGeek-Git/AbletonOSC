#!/usr/bin/env python3
"""
Emit the machine-readable schema + human API reference from the single source of truth
(abletonosc/lom_schema.py), so the PHP client, the README, and the docs never drift from
the dispatcher.

Outputs (relative to the repo root):
  * lom_schema.json        -- the descriptor as JSON (consumed by the PHP Lom client codegen)
  * docs/JSONRPC_API.md    -- a generated, exhaustive JSON-RPC API reference

lom_schema.py is PURE DATA (no `Live` import, no relative imports), so we load it in
isolation -- this avoids triggering abletonosc/__init__.py (which imports the dispatcher,
which imports `Live`) and lets the tool run headlessly with no Ableton and no stubs.

Run:  .venv311/bin/python tools/export_schema.py
"""

import importlib.util
import json
import os

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)


def _load_schema_module():
    path = os.path.join(ROOT, "abletonosc", "lom_schema.py")
    spec = importlib.util.spec_from_file_location("lom_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(schema):
    out = os.path.join(ROOT, "lom_schema.json")
    with open(out, "w") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
        f.write("\n")
    return out


# Kind -> how the path leaf is written and what it does, for the reference.
_KIND_NOTE = {
    "direct": "getattr/setattr",
    "mixer": "mixer_device.<leaf>.value",
    "send": "leaf `send.<i>` -> mixer_device.sends[i].value",
    "param": "leaf `parameter.<i>.<sub>` -> parameters[i].<sub>",
    "computed": "computed scalar",
    "list": "aggregate list",
}


def _flags(p):
    f = []
    if p["readable"]:
        f.append("r")
    if p["writable"]:
        f.append("w")
    if p["observable"]:
        f.append("sub")
    return "/".join(f)


def _path_for_object(obj, arity):
    return obj + "".join(".%d" % i for i in range(arity)) if arity else obj


def write_markdown(schema):
    out = os.path.join(ROOT, "docs", "JSONRPC_API.md")
    lines = []
    a = lines.append
    a("# AbletonOSC JSON-RPC API reference\n")
    a("> **Generated** from `abletonosc/lom_schema.py` by `tools/export_schema.py` -- "
      "do not edit by hand. The descriptor is the single source of truth that also drives "
      "the dispatcher and the test battery.\n")
    a("Flags: **r** readable (`get`), **w** writable (`set`), **sub** observable "
      "(`subscribe`). `<i>`/`<j>` are zero-based indices in the path.\n")

    totals_props = sum(len(s["props"]) for s in schema.values())
    totals_methods = sum(len(s["methods"]) for s in schema.values())
    a("**%d objects, %d properties, %d methods.**\n" % (len(schema), totals_props, totals_methods))

    for obj in schema:
        spec = schema[obj]
        arity = spec["arity"]
        base = _path_for_object(obj, arity)
        a("## `%s`  (arity %d)\n" % (obj, arity))
        if spec.get("notes"):
            a("_MIDI notes: extended-dict ops `add_notes` / `get_notes` / `remove_notes` / "
              "`remove_notes_by_id` (see API conventions)._\n")
        if spec.get("special_listeners"):
            a("_Special listeners: %s._\n" % ", ".join("`%s`" % s for s in spec["special_listeners"]))

        props = spec["props"]
        if props:
            a("### Properties\n")
            a("| path | flags | kind |")
            a("| --- | --- | --- |")
            for name in sorted(props):
                p = props[name]
                a("| `%s.%s` | %s | %s |" % (base, name, _flags(p), _KIND_NOTE.get(p["kind"], p["kind"])))
            a("")

        methods = spec["methods"]
        if methods:
            a("### Methods\n")
            a("| path | args |")
            a("| --- | --- |")
            for name in sorted(methods):
                args = methods[name]
                argstr = ", ".join(args) if args else "(none)"
                a("| `%s.%s` | %s |" % (base, name, argstr))
            a("")

    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return out


def main():
    module = _load_schema_module()
    schema = module.export_schema()
    j = write_json(schema)
    m = write_markdown(schema)
    print("wrote %s" % os.path.relpath(j, ROOT))
    print("wrote %s" % os.path.relpath(m, ROOT))
    print("objects=%d props=%d methods=%d" % (
        len(schema),
        sum(len(s["props"]) for s in schema.values()),
        sum(len(s["methods"]) for s in schema.values()),
    ))


if __name__ == "__main__":
    main()
