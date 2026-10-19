"""Read and validate the committed Solar Fortress controller configuration.

Defaults to the repository-relative JSON file so the probe works in CI,
a local clone and a deployed /data/solar-fortress checkout.
No Victron hardware is accessed and no configuration is written.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "controller.json"


def validate(config: dict) -> tuple[dict, dict]:
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be an object")
    thresholds = config["soc_thresholds"]
    qualification = config["qualification_seconds"]
    if not isinstance(thresholds, dict) or not isinstance(qualification, dict):
        raise ValueError("Thresholds and qualification must be objects")

    charge = thresholds["grid_charge"]
    passthrough = thresholds["grid_pass_through"]
    battery_return = thresholds["battery_return"]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and
               math.isfinite(v) for v in (charge, passthrough, battery_return)):
        raise ValueError("SOC thresholds must be finite numbers")
    if not (0 <= charge < passthrough < battery_return <= 100):
        raise ValueError("Expected 0 <= charge < pass-through < return <= 100")

    required_timers = ("pass_through", "grid_charge", "stop_grid_charge")
    for name in required_timers:
        value = qualification[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} qualification must be a finite non-negative number")
    return thresholds, qualification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Path to JSON (defaults to repo/config/controller.json)")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    thresholds, qualification = validate(config)

    print("Solar Fortress configuration valid:", args.config)
    print("Grid pass-through threshold:", thresholds["grid_pass_through"], "%")
    print("Grid charging threshold:", thresholds["grid_charge"], "%")
    print("Battery return threshold:", thresholds["battery_return"], "%")
    print("Pass-through qualification:", qualification["pass_through"], "seconds")
    print("Grid-charge qualification:", qualification["grid_charge"], "seconds")
    print("Stop-charge qualification:", qualification["stop_grid_charge"], "seconds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
