# Security policy

## Reporting a vulnerability

Email **security@deepvertex.id** (or contact the maintainer directly) with steps to
reproduce. Please do not open public issues for security matters. We acknowledge
within 3 business days.

## Scope & threat model (ROV mode)

The console is a **field tool** operating on a private tether network:

- The MAVLink link (UDP 14550) and the RAW agent (HTTP 8765) are designed for the
  point-to-point tether between laptop and ROV; they assume a trusted LAN segment.
  The RAW agent supports an optional `--token` (sent as `X-Token`) for deployments
  where the segment is not trusted.
- The console never actuates the vehicle without a held dead-man (gamepad) or an
  explicit confirmed command (arm/surface/motor-test flows).
- Secrets: none are stored in this repo. Client-specific tokens live in the local
  launch environment, not in source.
- Outbound network use: OSM tile downloads for the map (can be fully offline —
  tiles cache locally). Nothing else phones home.

## Supported versions

Only the latest tagged release is supported with security fixes.
