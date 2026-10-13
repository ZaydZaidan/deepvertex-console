# Roadmap & known limitations

## Phase 1 — ROV mode (shipped, v1.0.0)

Everything in the README feature table: three streams, full telemetry, teleop with
dead-man, compressed + RAW capture (per camera, all three cameras), storage guard,
per-sensor pre-flight, failsafe tooling, calibration suite, maps, gamepad mapping
with live feedback, session/file management, CI + docs.

## Phase 2 — AUV mode (planned)

- **Onboard autonomy on the Orin companion**: mission waypoint execution without the
  pilot loop; the console becomes supervisor + health monitor.
- **VIO**: visual-inertial odometry for absolute-position mapping (the current map
  track is a dead-reckoning estimate, gated on ARMED, and is labeled ESTIMATE in the UI).
- Position-based capture (take a photo at each waypoint instead of every N seconds).
- Autonomy-specific failsafes (geofence, battery reserve for return, comms-loss plan).

## Known limitations (Phase 1)

| Limitation | Detail / workaround |
|---|---|
| Stereo camera currently being manufactured | Tiles and RAW paths for stereo are ready and tested; the physical unit slots in with no software change |
| Front-camera RAW resolution | ≤ 640×480 (USB2 limit of the Low-Light HD camera) — use stereo RAW for photogrammetry |
| BlueOS extension deployment | The scaffold in `onboard/extension/` is prepared; deployment on the client's ROV happens during handover |
| Map: no GPS indoors | Anchor is placed manually (one click); everything is relative to the anchor until Phase 2 VIO/GPS |
| Single-window UI | Layouts are instant, but the console is one process — no detachable panels yet |
