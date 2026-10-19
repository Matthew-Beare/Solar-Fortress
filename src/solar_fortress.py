import argparse
import os
import sys
import time

from controller import (
    GridMode,
    SolarFortressController,
)
from victron_hardware import VictronHardware


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

CONFIG_PATH = os.path.join(
    PROJECT_ROOT,
    "config",
    "controller.json",
)

MAX_CHARGE_CURRENT = 30.0


def new_controller():
    return SolarFortressController.from_config_file(
        CONFIG_PATH
    )


def wait_with_spinner(
    seconds,
    waiting_since,
):
    frames = ("|", "/", "-", "\\")
    end_time = time.monotonic() + seconds
    frame = 0
    last_logged_second = -1

    while time.monotonic() < end_time:
        elapsed = int(
            time.monotonic() - waiting_since
        )

        symbol = frames[
            frame % len(frames)
        ]

        if sys.stdout.isatty():
            print(
                "\rWAITING FOR VICTRON "
                f"{elapsed:4d}s {symbol}",
                end="",
                flush=True,
            )

            time.sleep(0.15)

        else:
            if (
                elapsed % 5 == 0
                and elapsed != last_logged_second
            ):
                print(
                    "WAITING FOR VICTRON "
                    f"{elapsed}s {symbol}",
                    flush=True,
                )

                last_logged_second = elapsed

            time.sleep(0.25)

        frame += 1


def wait_for_hardware(
    retry_seconds=5,
    stable_reads=5,
):
    waiting_since = time.monotonic()

    while True:
        try:
            hardware = VictronHardware()

            snapshot = None
            battery = None

            for attempt in range(
                1,
                stable_reads + 1,
            ):
                snapshot = hardware.read_snapshot()
                battery = hardware.battery_status()

                print(
                    "VICTRON STARTUP CHECK "
                    f"{attempt}/{stable_reads} OK",
                    flush=True,
                )

                if attempt < stable_reads:
                    time.sleep(1)

            if sys.stdout.isatty():
                print(
                    "\r"
                    + (" " * 70)
                    + "\r",
                    end="",
                    flush=True,
                )

            print(
                "VICTRON READY | "
                f"SOC {snapshot.soc:.1f}% | "
                f"Mode "
                f"{snapshot.operating_mode.value.upper()} | "
                f"Battery {battery['flow']}",
                flush=True,
            )

            return hardware

        except KeyboardInterrupt:
            raise

        except Exception as exc:
            print(
                "WAITING FOR VICTRON | "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

            wait_with_spinner(
                retry_seconds,
                waiting_since,
            )

def apply_mode(hardware, mode):
    if mode == GridMode.BATTERY_FIRST:
        hardware.set_battery_first()
        return

    if mode == GridMode.PASS_THROUGH:
        hardware.set_pass_through()
        return

    if mode == GridMode.GRID_CHARGING:
        hardware.set_charging(
            MAX_CHARGE_CURRENT
        )
        return

    raise RuntimeError(
        f"Unsupported mode: {mode}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
    )

    parser.add_argument(
        "--status-every",
        type=float,
        default=1.0,
    )

    args = parser.parse_args()

    print(
        "Solar Fortress starting",
        flush=True,
    )

    print(
        "CONTROL: "
        + (
            "LIVE"
            if args.apply
            else "DRY RUN"
        ),
        flush=True,
    )

    controller = new_controller()

    #
    # Do not enter the control loop until Venus,
    # MultiPlus and SmartShunt are actually readable.
    #
    hardware = wait_for_hardware()

    last_requested_mode = None
    last_status_time = 0.0

    while True:
        try:
            snapshot = hardware.read_snapshot()
            battery = hardware.battery_status()

            decision = controller.evaluate(
                snapshot
            )

            now = time.monotonic()

            if (
                now - last_status_time
                >= args.status_every
            ):
                print(
                    f"SOC {snapshot.soc:.1f}% | "
                    f"AC "
                    f"{'AVAILABLE' if snapshot.ac_available else 'UNAVAILABLE'} | "
                    f"Mode "
                    f"{snapshot.operating_mode.value.upper()} | "
                    f"Battery {battery['flow']} | "
                    f"{battery['current']:+.1f} A | "
                    f"{battery['power']:+.0f} W | "
                    f"Requested "
                    f"{decision.requested_mode.value}",
                    flush=True,
                )

                last_status_time = now

            if (
                decision.requested_mode
                != snapshot.operating_mode
            ):
                if (
                    decision.requested_mode
                    != last_requested_mode
                ):
                    print(
                        "MODE CHANGE REQUEST: "
                        f"{snapshot.operating_mode.value}"
                        " -> "
                        f"{decision.requested_mode.value}",
                        flush=True,
                    )

                    print(
                        "Reason: "
                        f"{decision.reason}",
                        flush=True,
                    )

                if args.apply:
                    apply_mode(
                        hardware,
                        decision.requested_mode,
                    )

                    print(
                        "MODE CHANGE APPLIED: "
                        f"{decision.requested_mode.value}",
                        flush=True,
                    )

                elif (
                    decision.requested_mode
                    != last_requested_mode
                ):
                    print(
                        "DRY RUN: no hardware change",
                        flush=True,
                    )

            last_requested_mode = (
                decision.requested_mode
            )

            time.sleep(args.interval)

        except KeyboardInterrupt:
            print(
                "Solar Fortress stopped",
                flush=True,
            )
            break

        except Exception as exc:
            print(
                "HARDWARE ERROR | "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

            print(
                "RESETTING QUALIFICATION TIMERS "
                "AND RECONNECTING",
                flush=True,
            )

            #
            # A missing measurement breaks the meaning
            # of "continuously for 60 seconds".
            #
            controller = new_controller()

            last_requested_mode = None
            last_status_time = 0.0

            hardware = wait_for_hardware()


if __name__ == "__main__":
    main()
