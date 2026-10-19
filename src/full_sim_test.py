import json
from pathlib import Path

from controller import (
    GridMode,
    SolarFortressController,
    SystemSnapshot,
)
from victron_hardware import VictronHardware
from solar_fortress import apply_mode, MAX_CHARGE_CURRENT


CONFIG = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "controller.json"
)
checks = 0


def check(condition, message):
    global checks
    checks += 1

    if not condition:
        raise AssertionError(
            f"\nFAILED: {message}"
        )


def fresh():
    return SolarFortressController.from_config_file(
        str(CONFIG)
    )


def evaluate(
    controller,
    soc,
    mode,
    now,
    ac=True,
):
    return controller.evaluate(
        SystemSnapshot(
            soc=soc,
            ac_available=ac,
            operating_mode=mode,
        ),
        now=now,
    ).requested_mode


print("SOLAR FORTRESS FULL SIMULATION")
print("=" * 50)


#
# CONFIGURATION
#

config = json.loads(CONFIG.read_text())

check(
    config["soc_thresholds"]["grid_pass_through"] == 30.0,
    "Pass-through threshold must be 30%",
)

check(
    config["soc_thresholds"]["grid_charge"] == 20.0,
    "Grid-charge threshold must be 20%",
)

check(
    config["soc_thresholds"]["battery_return"] == 35.0,
    "Battery-return threshold must be 35%",
)

check(
    config["qualification_seconds"]["pass_through"] == 60,
    "Pass-through qualification must be 60 seconds",
)

check(
    config["qualification_seconds"]["grid_charge"] == 60,
    "Grid-charge qualification must be 60 seconds",
)

check(
    config["qualification_seconds"]["stop_grid_charge"] == 60,
    "Stop-charge qualification must be 60 seconds",
)

print("PASS: configuration")


#
# EXHAUSTIVE SOC SWEEP
#
# Test every 0.1% SOC from 0 through 100
# in every grid mode.
#

for tenth in range(0, 1001):
    soc = tenth / 10.0

    #
    # BATTERY FIRST
    #

    c = fresh()

    d0 = evaluate(
        c,
        soc,
        GridMode.BATTERY_FIRST,
        0,
    )

    d59 = evaluate(
        c,
        soc,
        GridMode.BATTERY_FIRST,
        59,
    )

    d60 = evaluate(
        c,
        soc,
        GridMode.BATTERY_FIRST,
        60,
    )

    check(
        d0 == GridMode.BATTERY_FIRST,
        f"BF at {soc}% changed immediately",
    )

    check(
        d59 == GridMode.BATTERY_FIRST,
        f"BF at {soc}% changed before 60 sec",
    )

    expected = (
        GridMode.PASS_THROUGH
        if soc <= 30.0
        else GridMode.BATTERY_FIRST
    )

    check(
        d60 == expected,
        f"BF 60-sec result wrong at {soc}%",
    )

    #
    # PASS THROUGH
    #

    c = fresh()

    d0 = evaluate(
        c,
        soc,
        GridMode.PASS_THROUGH,
        0,
    )

    d59 = evaluate(
        c,
        soc,
        GridMode.PASS_THROUGH,
        59,
    )

    d60 = evaluate(
        c,
        soc,
        GridMode.PASS_THROUGH,
        60,
    )

    if soc >= 35.0:
        expected0 = GridMode.BATTERY_FIRST
        expected59 = GridMode.BATTERY_FIRST
        expected60 = GridMode.BATTERY_FIRST

    elif soc <= 20.0:
        expected0 = GridMode.PASS_THROUGH
        expected59 = GridMode.PASS_THROUGH
        expected60 = GridMode.GRID_CHARGING

    else:
        expected0 = GridMode.PASS_THROUGH
        expected59 = GridMode.PASS_THROUGH
        expected60 = GridMode.PASS_THROUGH

    check(
        d0 == expected0,
        f"PT initial result wrong at {soc}%",
    )

    check(
        d59 == expected59,
        f"PT 59-sec result wrong at {soc}%",
    )

    check(
        d60 == expected60,
        f"PT 60-sec result wrong at {soc}%",
    )

    #
    # GRID CHARGING
    #

    c = fresh()

    d0 = evaluate(
        c,
        soc,
        GridMode.GRID_CHARGING,
        0,
    )

    d59 = evaluate(
        c,
        soc,
        GridMode.GRID_CHARGING,
        59,
    )

    d60 = evaluate(
        c,
        soc,
        GridMode.GRID_CHARGING,
        60,
    )

    check(
        d0 == GridMode.GRID_CHARGING,
        f"GC at {soc}% stopped immediately",
    )

    check(
        d59 == GridMode.GRID_CHARGING,
        f"GC at {soc}% stopped before 60 sec",
    )

    expected = (
        GridMode.PASS_THROUGH
        if soc >= 30.0
        else GridMode.GRID_CHARGING
    )

    check(
        d60 == expected,
        f"GC 60-sec result wrong at {soc}%",
    )


print("PASS: 0.0-100.0% SOC sweep in all modes")


#
# TIMER RESET: FALLING THROUGH 30
#

c = fresh()

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 0
    ) == GridMode.BATTERY_FIRST,
    "30% timer failed to start",
)

check(
    evaluate(
        c, 30.1, GridMode.BATTERY_FIRST, 30
    ) == GridMode.BATTERY_FIRST,
    "30% timer failed to reset",
)

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 40
    ) == GridMode.BATTERY_FIRST,
    "30% timer restart failed",
)

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 99.9
    ) == GridMode.BATTERY_FIRST,
    "30% timer fired early after reset",
)

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 100
    ) == GridMode.PASS_THROUGH,
    "30% timer failed after restart",
)

print("PASS: 30% timer reset")


#
# TIMER RESET: FALLING THROUGH 20
#

c = fresh()

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 0
    ) == GridMode.PASS_THROUGH,
    "20% timer failed to start",
)

check(
    evaluate(
        c, 20.1, GridMode.PASS_THROUGH, 30
    ) == GridMode.PASS_THROUGH,
    "20% timer failed to reset",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 40
    ) == GridMode.PASS_THROUGH,
    "20% timer restart failed",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 99.9
    ) == GridMode.PASS_THROUGH,
    "20% timer fired early after reset",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 100
    ) == GridMode.GRID_CHARGING,
    "20% timer failed after restart",
)

print("PASS: 20% timer reset")


#
# TIMER RESET: GRID CHARGING UP THROUGH 30
#

c = fresh()

check(
    evaluate(
        c, 30, GridMode.GRID_CHARGING, 0
    ) == GridMode.GRID_CHARGING,
    "Stop-charge timer failed to start",
)

check(
    evaluate(
        c, 29.9, GridMode.GRID_CHARGING, 30
    ) == GridMode.GRID_CHARGING,
    "Stop-charge timer failed to reset",
)

check(
    evaluate(
        c, 30, GridMode.GRID_CHARGING, 40
    ) == GridMode.GRID_CHARGING,
    "Stop-charge timer restart failed",
)

check(
    evaluate(
        c, 30, GridMode.GRID_CHARGING, 99.9
    ) == GridMode.GRID_CHARGING,
    "Stop-charge timer fired early",
)

check(
    evaluate(
        c, 30, GridMode.GRID_CHARGING, 100
    ) == GridMode.PASS_THROUGH,
    "Stop-charge timer failed after restart",
)

print("PASS: charging 30% timer reset")


#
# CRITICAL 32% HYSTERESIS TEST
#

c = fresh()

check(
    evaluate(
        c, 32, GridMode.BATTERY_FIRST, 0
    ) == GridMode.BATTERY_FIRST,
    "32% battery-first was not preserved",
)

c = fresh()

check(
    evaluate(
        c, 32, GridMode.PASS_THROUGH, 0
    ) == GridMode.PASS_THROUGH,
    "32% pass-through was not preserved",
)

print("PASS: 32% remembers which side it came from")


#
# COMPLETE RISING SEQUENCE:
# GRID CHARGING -> PASS THROUGH -> BATTERY FIRST
#

c = fresh()

check(
    evaluate(
        c, 32, GridMode.GRID_CHARGING, 0
    ) == GridMode.GRID_CHARGING,
    "Rising sequence stopped charging early",
)

check(
    evaluate(
        c, 32, GridMode.GRID_CHARGING, 59
    ) == GridMode.GRID_CHARGING,
    "Rising sequence stopped before 60 sec",
)

check(
    evaluate(
        c, 32, GridMode.GRID_CHARGING, 60
    ) == GridMode.PASS_THROUGH,
    "Rising sequence failed to enter pass-through",
)

check(
    evaluate(
        c, 32, GridMode.PASS_THROUGH, 61
    ) == GridMode.PASS_THROUGH,
    "32% failed to remain pass-through",
)

check(
    evaluate(
        c, 34.9, GridMode.PASS_THROUGH, 62
    ) == GridMode.PASS_THROUGH,
    "34.9% failed to remain pass-through",
)

check(
    evaluate(
        c, 35, GridMode.PASS_THROUGH, 63
    ) == GridMode.BATTERY_FIRST,
    "35% failed to return battery-first",
)

print("PASS: charging -> 30 -> pass-through -> 35 -> battery-first")


#
# COMPLETE FALLING SEQUENCE:
# BATTERY FIRST -> PASS THROUGH -> GRID CHARGING
#

c = fresh()

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 0
    ) == GridMode.BATTERY_FIRST,
    "Falling sequence left battery-first early",
)

check(
    evaluate(
        c, 30, GridMode.BATTERY_FIRST, 60
    ) == GridMode.PASS_THROUGH,
    "Falling sequence failed at 30%",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 61
    ) == GridMode.PASS_THROUGH,
    "Falling sequence charged immediately at 20%",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 120
    ) == GridMode.PASS_THROUGH,
    "Falling sequence charged before 60 sec",
)

check(
    evaluate(
        c, 20, GridMode.PASS_THROUGH, 121
    ) == GridMode.GRID_CHARGING,
    "Falling sequence failed to grid charge",
)

print("PASS: battery-first -> 30 -> pass-through -> 20 -> grid-charge")


#
# AC AVAILABILITY MUST NOT CHANGE POLICY STATE
#

for soc in range(0, 101):
    for mode in (
        GridMode.BATTERY_FIRST,
        GridMode.PASS_THROUGH,
        GridMode.GRID_CHARGING,
    ):
        c1 = fresh()
        c2 = fresh()

        with_ac = evaluate(
            c1, soc, mode, 0, ac=True
        )

        without_ac = evaluate(
            c2, soc, mode, 0, ac=False
        )

        check(
            with_ac == without_ac,
            f"AC availability changed policy at "
            f"{soc}% in {mode.value}",
        )

print("PASS: AC-present/AC-absent policy parity")


#
# BATTERY FLOW CLASSIFICATION
#
# Fake _read so NO D-BUS hardware access occurs.
#

def fake_battery(current, voltage=13.2):
    hw = object.__new__(VictronHardware)
    hw.battery_service = "SIMULATED"

    values = {
        "/Dc/0/Voltage": voltage,
        "/Dc/0/Current": current,
    }

    hw._read = lambda service, path: values[path]

    return hw.battery_status()


check(
    fake_battery(10)["flow"] == "CHARGING",
    "Positive battery current not CHARGING",
)

check(
    fake_battery(-10)["flow"] == "DISCHARGING",
    "Negative battery current not DISCHARGING",
)

check(
    fake_battery(0)["flow"] == "IDLE",
    "Zero battery current not IDLE",
)

check(
    fake_battery(0.5)["flow"] == "IDLE",
    "+0.5 A deadband incorrect",
)

check(
    fake_battery(-0.5)["flow"] == "IDLE",
    "-0.5 A deadband incorrect",
)

check(
    fake_battery(0.51)["flow"] == "CHARGING",
    "+0.51 A should be CHARGING",
)

check(
    fake_battery(-0.51)["flow"] == "DISCHARGING",
    "-0.51 A should be DISCHARGING",
)

check(
    fake_battery(10)["power"] > 0,
    "Charging power sign incorrect",
)

check(
    fake_battery(-10)["power"] < 0,
    "Discharging power sign incorrect",
)

print("PASS: battery-flow reporting")


#
# ACTUATOR DISPATCH
#
# Fake the hardware object. This tests which actuator
# WOULD be called without touching the real MultiPlus.
#

class FakeHardware:
    def __init__(self):
        self.calls = []

    def set_battery_first(self):
        self.calls.append(
            ("battery_first",)
        )

    def set_pass_through(self):
        self.calls.append(
            ("pass_through",)
        )

    def set_charging(self, amps):
        self.calls.append(
            ("grid_charging", amps)
        )


fake = FakeHardware()
apply_mode(fake, GridMode.BATTERY_FIRST)

check(
    fake.calls == [("battery_first",)],
    "BATTERY_FIRST actuator dispatch wrong",
)

fake = FakeHardware()
apply_mode(fake, GridMode.PASS_THROUGH)

check(
    fake.calls == [("pass_through",)],
    "PASS_THROUGH actuator dispatch wrong",
)

fake = FakeHardware()
apply_mode(fake, GridMode.GRID_CHARGING)

check(
    fake.calls == [
        ("grid_charging", MAX_CHARGE_CURRENT)
    ],
    "GRID_CHARGING actuator dispatch wrong",
)

print("PASS: actuator dispatch")


#
# INVALID SOC MUST FAIL
#

for bad_soc in (-0.1, 100.1):
    c = fresh()

    try:
        evaluate(
            c,
            bad_soc,
            GridMode.BATTERY_FIRST,
            0,
        )
    except ValueError:
        checks += 1
    else:
        raise AssertionError(
            f"Invalid SOC {bad_soc} was accepted"
        )

print("PASS: invalid-SOC protection")


print()
print("=" * 50)
print(f"ALL TESTS PASSED: {checks:,} checks")
print("NO VICTRON HARDWARE WAS WRITTEN")
