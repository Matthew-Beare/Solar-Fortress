from controller import (
    GridMode,
    SolarFortressController,
    SystemSnapshot,
)


def controller():
    return SolarFortressController(
        grid_pass_through_soc=30.0,
        grid_charge_soc=20.0,
        battery_return_soc=35.0,
        pass_through_qualification=60,
        grid_charge_qualification=60,
        stop_grid_charge_qualification=60,
    )


def evaluate(
    c,
    soc,
    mode,
    now,
    ac=True,
):
    return c.evaluate(
        SystemSnapshot(
            soc=soc,
            ac_available=ac,
            operating_mode=mode,
        ),
        now=now,
    ).requested_mode


#
# Above 30% stays battery-first.
#
c = controller()

assert evaluate(
    c, 80, GridMode.BATTERY_FIRST, 0
) == GridMode.BATTERY_FIRST


#
# 30% must remain <=30 for 60 continuous seconds.
#
c = controller()

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 0
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 59
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 60
) == GridMode.PASS_THROUGH


#
# Going back above 30 resets that timer.
#
c = controller()

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 0
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 31, GridMode.BATTERY_FIRST, 30
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 40
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 99
) == GridMode.BATTERY_FIRST

assert evaluate(
    c, 30, GridMode.BATTERY_FIRST, 100
) == GridMode.PASS_THROUGH


#
# 20% must remain <=20 for 60 continuous seconds
# before grid charging begins.
#
c = controller()

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 0
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 59
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 60
) == GridMode.GRID_CHARGING


#
# Going back above 20 resets that timer.
#
c = controller()

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 0
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 21, GridMode.PASS_THROUGH, 30
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 40
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 99
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 20, GridMode.PASS_THROUGH, 100
) == GridMode.GRID_CHARGING


#
# Grid charging must remain >=30 for 60 seconds
# before charging stops.
#
c = controller()

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 0
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 59
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 60
) == GridMode.PASS_THROUGH


#
# Falling below 30 resets the stop-charge timer.
#
c = controller()

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 0
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 29, GridMode.GRID_CHARGING, 30
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 40
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 99
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 30, GridMode.GRID_CHARGING, 100
) == GridMode.PASS_THROUGH


#
# 35% returns immediately to battery-first.
#
c = controller()

assert evaluate(
    c, 35, GridMode.PASS_THROUGH, 0
) == GridMode.BATTERY_FIRST


#
# Reboot between 30 and 35 preserves the
# currently configured state.
#
c = controller()

assert evaluate(
    c, 32, GridMode.BATTERY_FIRST, 0
) == GridMode.BATTERY_FIRST

c = controller()

assert evaluate(
    c, 32, GridMode.PASS_THROUGH, 0
) == GridMode.PASS_THROUGH


#
# Even at high SOC, active grid charging must first
# satisfy the 60-second stop-charge qualification.
#
c = controller()

assert evaluate(
    c, 86, GridMode.GRID_CHARGING, 0
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 86, GridMode.GRID_CHARGING, 59
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 86, GridMode.GRID_CHARGING, 60
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 86, GridMode.PASS_THROUGH, 61
) == GridMode.BATTERY_FIRST


#
# Exact hysteresis case:
#
# If already battery-first at 32%, stay battery-first.
# If coming up from grid charging, stop charging after
# 60 seconds >=30%, remain pass-through at 32-34.9%,
# and only return to battery-first at 35%.
#
c = controller()

assert evaluate(
    c, 32, GridMode.BATTERY_FIRST, 0
) == GridMode.BATTERY_FIRST

c = controller()

assert evaluate(
    c, 32, GridMode.GRID_CHARGING, 0
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 32, GridMode.GRID_CHARGING, 59
) == GridMode.GRID_CHARGING

assert evaluate(
    c, 32, GridMode.GRID_CHARGING, 60
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 32, GridMode.PASS_THROUGH, 61
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 34.9, GridMode.PASS_THROUGH, 62
) == GridMode.PASS_THROUGH

assert evaluate(
    c, 35, GridMode.PASS_THROUGH, 63
) == GridMode.BATTERY_FIRST


#
# AC availability does not change the desired
# configuration. The mode defines what should happen
# whenever AC becomes available.
#
c = controller()

assert evaluate(
    c,
    80,
    GridMode.BATTERY_FIRST,
    0,
    ac=False,
) == GridMode.BATTERY_FIRST


print(
    "ALL SOLAR FORTRESS 30/20/30/35 POLICY TESTS PASSED"
)
