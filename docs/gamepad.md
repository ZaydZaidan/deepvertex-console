# Gamepad — mapping, presets and safety

The console reads the pad directly via **evdev** (no pygame), so raw indices match
Linux exactly and input behaves identically at every frame rate.

## Default mapping (DualShock 4)

| Control | Action | Raw index |
|---|---|---|
| LEFT STICK ↑/↓ | Forward / back | LY `[1]` |
| LEFT STICK ←/→ | Strafe left / right | LX `[0]` |
| RIGHT STICK ↑/↓ | Ascend / descend | RY `[4]` |
| RIGHT STICK ←/→ | Turn (yaw) | RX `[3]` |
| D-PAD ↑/↓ | Camera tilt | `[7]` |
| **LB or RB (hold)** | **DEAD-MAN** — thrust only while held | `[4]`/`[5]` |
| LT (L2, hold) | Depth hold (ALT_HOLD) while held | `[6]` |
| □ (X / left) | Toggle recording (front camera) | `[3]` |
| ○ (B / right) | Take a photo (front camera) | `[1]` |
| × (A / bottom) | ARM / DISARM | `[0]` |
| SHARE (Select) | Cycle flight mode | `[8]` |
| △ (Y / top) | Camera back to center (50%) | `[2]` |
| OPTIONS (Start) | ARM (reserved for pool tests) | `[9]` — *disabled by default* |

Numbers in `[ ]` are the raw Linux button/axis indices, shown for advanced users.

## The GAMEPAD MAPPING page

Open with **Vehicle ▸ Gamepad mapping…** or the **PAD MAPPING…** button in the TELEOP
panel. It contains:

- a **painted DualShock 4 diagram** (shape matched to the physical DS4) with the
  function name of every control;
- **LIVE feedback** — press any button or move any stick on the real pad and the
  matching element lights up and the stick knobs move in real time;
- **CHANGE** on any row → press the button / move the stick you want → bound instantly;
- **named presets**: SAVE AS… (e.g. `Custom A`) stores the full mapping in
  `gui_t1/gamepad_presets.json` on that laptop and reloads automatically next start;
  APPLY switches between saved presets.

### Safety: mapping page blocks the vehicle

While the mapping page is open, the console **mutes all pad output to the ROV**:
neutral manual-control is sent continuously, RC overrides are released, and edge
actions (arm / mode / record / photo) are suppressed. You can hammer every button
during remapping without the thrusters or camera ever moving. Closing the page (any
way — CLOSE, window X, ESC) restores output instantly.

## Practical notes

- Dead-man is enforced in software *and* by habit: no L1/R1 = no thrust, period.
- MAX SPEED (right rail) scales everything the pad sends — use 25% indoors.
- If the pad disconnects mid-dive, the console treats it as "dead-man released"
  (neutral output) and keeps watching for reconnection.
- Two pads? The first one enumerates; the mapping page shows the active device name.
