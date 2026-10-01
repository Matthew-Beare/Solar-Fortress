from controller import (
    OperatingMode,
    SolarFortressController,
    SystemSnapshot,
)

CONFIG = "/data/solar-fortress/config/controller.json"


def new_controller():
    return SolarFortressController.from_config_file(CONFIG)


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(
            f"{name}: expected {expected.value}, got {actual.value}"
        )

    print(f"PASS: {name}")


controller = new_controller()

decision = controller.evaluate(
    SystemSnapshot(
        soc=80.0,
        ac_available=True,
        operating_mode=OperatingMode.BATTERY_FIRST,
    ),
    now=0,
)

check(
    "80% stays battery-first",
    decision.requested_mode,
    OperatingMode.BATTERY_FIRST,
)


controller = new_controller()

decision = controller.evaluate(
    SystemSnapshot(
        soc=30.0,
        ac_available=True,
        operating_mode=OperatingMode.BATTERY_FIRST,
    ),
    now=0,
)

check(
    "30% enters pass-through",
    decision.requested_mode,
    OperatingMode.PASS_THROUGH,
)


controller = new_controller()

snapshot = SystemSnapshot(
    soc=20.0,
    ac_available=True,
    operating_mode=OperatingMode.PASS_THROUGH,
)

check(
    "20% at 0 seconds stays pass-through",
    controller.evaluate(snapshot, now=100).requested_mode,
    OperatingMode.PASS_THROUGH,
)

check(
    "20% at 59 seconds stays pass-through",
    controller.evaluate(snapshot, now=159).requested_mode,
    OperatingMode.PASS_THROUGH,
)

check(
    "20% at 60 seconds starts charging",
    controller.evaluate(snapshot, now=160).requested_mode,
    OperatingMode.CHARGING,
)


controller = new_controller()

snapshot = SystemSnapshot(
    soc=30.0,
    ac_available=True,
    operating_mode=OperatingMode.CHARGING,
)

check(
    "30% charging at 0 seconds keeps charging",
    controller.evaluate(snapshot, now=200).requested_mode,
    OperatingMode.CHARGING,
)

check(
    "30% charging at 59 seconds keeps charging",
    controller.evaluate(snapshot, now=259).requested_mode,
    OperatingMode.CHARGING,
)

check(
    "30% charging at 60 seconds stops charging",
    controller.evaluate(snapshot, now=260).requested_mode,
    OperatingMode.PASS_THROUGH,
)


controller = new_controller()

decision = controller.evaluate(
    SystemSnapshot(
        soc=35.0,
        ac_available=True,
        operating_mode=OperatingMode.PASS_THROUGH,
    ),
    now=0,
)

check(
    "35% pass-through returns battery-first",
    decision.requested_mode,
    OperatingMode.BATTERY_FIRST,
)


controller = new_controller()

decision = controller.evaluate(
    SystemSnapshot(
        soc=32.0,
        ac_available=True,
        operating_mode=OperatingMode.BATTERY_FIRST,
    ),
    now=0,
)

check(
    "reboot at 32% preserves battery-first",
    decision.requested_mode,
    OperatingMode.BATTERY_FIRST,
)


controller = new_controller()

decision = controller.evaluate(
    SystemSnapshot(
        soc=32.0,
        ac_available=True,
        operating_mode=OperatingMode.PASS_THROUGH,
    ),
    now=0,
)

check(
    "reboot at 32% preserves pass-through",
    decision.requested_mode,
    OperatingMode.PASS_THROUGH,
)


print()
print("ALL SOLAR FORTRESS CORE POLICY TESTS PASSED")
