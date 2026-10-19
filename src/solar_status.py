#!/usr/bin/env python3

import os
import sys
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from victron_hardware import VictronHardware


CLEAR_SCREEN = "\033[2J\033[H"
HOME = "\033[H"
CLEAR_LINE = "\033[K"

SPINNER = ("|", "/", "-", "\\")


def yes_no(value):
    return "YES" if bool(value) else "NO"


def optional_read(hardware, path):
    try:
        return hardware._read(
            hardware.vebus_service,
            path,
        )
    except Exception:
        return None


def optional_float(hardware, *paths):
    for path in paths:
        value = optional_read(
            hardware,
            path,
        )

        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass

    return None


def format_number(value, suffix, decimals=1):
    if value is None:
        return "N/A"

    return f"{value:.{decimals}f} {suffix}"


def spinner_display(stop_event, started):
    while not stop_event.is_set():
        elapsed = int(
            time.monotonic() - started
        )

        symbol = SPINNER[
            elapsed % len(SPINNER)
        ]

        print(
            "\r"
            f"WAITING FOR VICTRON   "
            f"{elapsed:4d}s {symbol}"
            "          ",
            end="",
            flush=True,
        )

        stop_event.wait(1.0)


def wait_for_victron():
    started = time.monotonic()

    stop_event = threading.Event()

    spinner_thread = threading.Thread(
        target=spinner_display,
        args=(stop_event, started),
        daemon=True,
    )

    spinner_thread.start()

    try:
        while True:
            try:
                hardware = VictronHardware()

                for _ in range(5):
                    hardware.read_snapshot()
                    hardware.battery_status()
                    time.sleep(0.5)

                stop_event.set()
                spinner_thread.join(timeout=2)

                print(
                    "\r"
                    + (" " * 70)
                    + "\r",
                    end="",
                    flush=True,
                )

                return hardware

            except KeyboardInterrupt:
                raise

            except Exception:
                time.sleep(1)

    finally:
        stop_event.set()
        spinner_thread.join(timeout=2)


def build_dashboard(hardware):
    snapshot = hardware.read_snapshot()
    battery = hardware.battery_status()

    ac_connected = optional_read(
        hardware,
        "/Ac/ActiveIn/Connected",
    )

    ignore_ac = optional_read(
        hardware,
        "/Ac/Control/IgnoreAcIn1",
    )

    ac_current = optional_float(
        hardware,
        "/Ac/ActiveIn/L1/I",
        "/Ac/In/1/L1/I",
    )

    ac_power = optional_float(
        hardware,
        "/Ac/ActiveIn/L1/P",
        "/Ac/In/1/L1/P",
    )

    output_power = optional_float(
        hardware,
        "/Ac/Out/L1/P",
    )

    timestamp = datetime.now(
        ZoneInfo("America/New_York")
    ).strftime(
        "%Y-%m-%d %I:%M:%S %p %Z"
    )

    mode = (
        snapshot.operating_mode.value
        .replace("_", " ")
        .upper()
    )

    ac_present = yes_no(
        snapshot.ac_available
    )

    if ac_connected is None:
        ac_connected_text = "N/A"
    else:
        ac_connected_text = yes_no(
            ac_connected
        )

    if ignore_ac is None:
        ac_policy = "N/A"
    elif bool(ignore_ac):
        ac_policy = "IGNORE AC"
    else:
        ac_policy = "ACCEPT AC"

    if snapshot.operating_mode.value == "grid_charging":
        grid_charging = "ENABLED"
    else:
        grid_charging = "DISABLED"

    lines = [
        "============================================================",
        "                    SOLAR FORTRESS",
        "============================================================",
        "",
        f"TIME                 {timestamp}",
        "",
        f"SOC                  {snapshot.soc:6.1f} %",
        f"MODE                 {mode}",
        "",
        f"BATTERY STATE        {battery['flow']}",
        f"BATTERY VOLTAGE      {battery['voltage']:6.2f} V",
        f"BATTERY CURRENT      {battery['current']:+6.1f} A",
        f"BATTERY POWER        {battery['power']:+6.0f} W",
        "",
        "-------------------------- AC -------------------------------",
        "",
        f"AC PRESENT           {ac_present}",
        f"AC CONNECTED         {ac_connected_text}",
        f"AC POLICY            {ac_policy}",
        f"GRID CHARGING        {grid_charging}",
        f"AC INPUT CURRENT     {format_number(ac_current, 'A')}",
        f"AC INPUT POWER       {format_number(ac_power, 'W', 0)}",
        f"AC OUTPUT POWER      {format_number(output_power, 'W', 0)}",
        "",
        "-------------------------------------------------------------",
        "Ctrl+C exits this dashboard only. Controller keeps running.",
    ]

    return lines


def draw_dashboard(lines):
    sys.stdout.write(HOME)

    for line in lines:
        sys.stdout.write(
            CLEAR_LINE
            + line
            + "\n"
        )

    sys.stdout.flush()


def main():
    hardware = wait_for_victron()

    sys.stdout.write(CLEAR_SCREEN)
    sys.stdout.flush()

    while True:
        try:
            lines = build_dashboard(
                hardware
            )

            draw_dashboard(lines)

            time.sleep(1)

        except KeyboardInterrupt:
            print()
            print(
                "Dashboard closed. "
                "Solar Fortress controller is still running."
            )
            return

        except Exception:
            #
            # D-Bus vanished. Return to waiting display,
            # then rebuild the connection.
            #
            sys.stdout.write(
                CLEAR_SCREEN
            )
            sys.stdout.flush()

            hardware = wait_for_victron()

            sys.stdout.write(
                CLEAR_SCREEN
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
