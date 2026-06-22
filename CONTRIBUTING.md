# Contributing

## Tests

The suite is **descriptor-driven** (see `abletonosc/lom_schema.py`) and tiered:

- **Headless (no Live, CI) — the fast inner loop.** Use the checked-in venv:

  ```
  .venv311/bin/python -m pytest tests/ -q
  ```

  This runs the descriptor self-consistency check, the dispatcher battery against a fake
  LOM, the socket loopback (real pyzmq DEALER ↔ pyzmtp ROUTER), and the transport/vendoring
  checks — no Ableton required.

- **Live (on-demand, human-attended).** The descriptor-driven battery against real Ableton
  Live 12 + the hand-authored canonical project. Build the fixture once per
  `tests/fixtures/MANIFEST.md`, then run the live tier. For live runs, Live must be
  configured with default audio input/output devices and, in
  `Preferences > Record, Warp & Launch`, `Count-In` set to `None`.

## Live reloading

The dispatcher + descriptor can be reloaded without restarting Live: call the JSON-RPC
method `application.reload` (envelope `{"op":"call","path":"application.reload"}`). This
re-imports the handler modules and rebuilds the handler. **Caveat:** it does not rebuild the
server, so changes to `osc_server.py` / `zmtp_transport.py` need a full Live restart.

After changing `abletonosc/lom_schema.py`, regenerate the exported schema + docs:

```
.venv311/bin/python tools/export_schema.py
```

## Logging

Logging can be performed from any handler via the `self.logger` property. AbletonOSC logs
internal events to `logs/abletonosc.log` relative to the AbletonOSC directory, and relays
error-level records to connected clients as JSON `{"event":"error","message":...}` frames.

## Debugging compile-time issues

To view the Live boot log:

```
LOG_DIR="$HOME/Library/Application Support/Ableton/Live Reports/Usage"
LOG_FILE=$(ls -atr "$LOG_DIR"/*.log | tail -1)
echo "Log path: $LOG_FILE"
tail -5000f "$LOG_FILE" | grep AbletonOSC
```

A genuine import failure inside Live is also written to `import_error.log` next to
`__init__.py`.
