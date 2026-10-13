# Testing

Everything runs **without hardware**. Two kinds of tools: the assertion suite, and
the probes (visual/geometry checks that produce screenshots).

## The suite

```bash
cd gui_t1
# no hardware required; uses tests/fake_vehicle.py + GStreamer test pipelines.venv/bin/python../tests/test_console.py     # 30 numbered checks — PASS expected.venv/bin/python../tests/test_video.py       # pipeline smoke tests.venv/bin/python../tests/test_raw.py         # RAW agent client tests
```

`test_console.py` boots the real console offscreen against a fake autopilot and
verifies, among others: three-stream frames, telemetry & sub values, recording stop +
size, snapshot in `compressed/`, preset switching **without stuck windows** (stress),
gamepad named presets, PAD MAPPING dialog construction, mapping-page output blocking,
session renaming, the storage guard (REC/SNAP refused + alert), per-sensor pre-flight
logic, failsafe defaults round-trip, and the file-flow folder structure.

One check needs the local RAW agent running in test mode:

```bash.venv/bin/python../onboard/raw_agent.py --test --port 8791 &
```

## Probes (geometry & visual)

Run on a real display (they measure fonts at the real DPI and save screenshots):

```bash.venv/bin/python../tests/probe_layout.py     # truncation scan across all 4 layouts.venv/bin/python../tests/probe_scroll.py     # no horizontal clipping + scroll reachable.venv/bin/python../tests/probe_padmap.py     # gamepad page shots + live-highlight check
```

`probe_layout.py` asserts **0 truncated labels/buttons/checkboxes** in every layout;
`probe_scroll.py` asserts every rail fits its widest panel (`klip-H=False`) and the
scrollbars reach the bottom of the content.

## Real-robot checks (tether required)

```bash.venv/bin/python../tests/real_rov_linkcheck.py     # ping + BlueOS web + MAVLink stream.venv/bin/python../tests/real_rov_paramdump.py     # read-only parameter dump
```

## CI

`.github/workflows/ci.yml` runs the compile check, the full suite under Xvfb, and the
geometry probes on every push — the same commands, headless.
