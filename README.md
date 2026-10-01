# Solar-Fortress
Python-based Victron Venus OS controller for battery-first solar operation, grid pass-through, and SOC-based charging automation.


# Solar Fortress

Solar Fortress is a Python-based controller for Victron Venus OS designed to manage a battery-first solar power system with explicit SOC-based grid behavior.

The project runs directly on a Raspberry Pi running Venus OS and communicates with Victron devices through D-Bus.

## Goal

Replace the default energy-management behavior with a simple, predictable control policy:

- Run battery-first during normal operation.
- Switch to grid pass-through at 30% SOC.
- Do not intentionally charge from grid while SOC remains above 20%.
- If SOC stays at or below 20% for 60 seconds, enable grid charging.
- Continue grid charging until SOC stays at or above 30% for 60 seconds.
- Stop grid charging and remain in grid pass-through.
- Return to battery-first only after SOC reaches 35%.

This creates hysteresis between the operating thresholds and prevents rapid mode switching.

## Core SOC Policy

| SOC / Condition | Action |
|---|---|
| Above 35% | Battery-first operation |
| 30% reached while battery-first | Switch to grid pass-through |
| 20% or lower for 60 seconds | Enable grid charging |
| 30% or higher for 60 seconds while charging | Stop grid charging and remain in pass-through |
| 35% reached while in pass-through | Return to battery-first |

## Reboot Behavior

Solar Fortress does not rely on a stale persisted hysteresis flag.

On startup, the controller reads:

- current SOC
- AC availability
- actual MultiPlus operating state

If SOC is between 30% and 35%, Solar Fortress preserves the operating state already active on the hardware:

- battery-first remains battery-first until SOC falls to 30%
- grid pass-through remains pass-through until SOC rises to 35%

## Architecture

```text
SmartShunt
    |
    v
Victron D-Bus
    |
    v
Solar Fortress Python Controller
    |
    v
Victron D-Bus
    |
    v
MultiPlus
