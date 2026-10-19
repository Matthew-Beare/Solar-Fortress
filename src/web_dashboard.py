#!/usr/bin/env python3

import dbus
import json
import math
import os
import threading
import time

from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from victron_hardware import VictronHardware


HOST = "0.0.0.0"
PORT = 8080

BATTERY_CAPACITY_AH = 600.0
CONFIG_PATH = "/data/solar-fortress/config/controller.json"
TODAY_MAX_SOC_PATH = "/data/solar-fortress/state/today_max_soc.json"
SOC_24H_PATH = "/data/solar-fortress/state/soc_24h.json"
SOLAR_YIELD_LOCAL_PATH = "/data/solar-fortress/state/solar_yield_local.json"

SOLAR_LATITUDE = 36.4624

MONTHLY_PEAK_SUN_HOURS = {
    1: 2.2,
    2: 2.8,
    3: 4.1,
    4: 5.3,
    5: 6.1,
    6: 6.5,
    7: 6.1,
    8: 5.8,
    9: 5.0,
    10: 3.7,
    11: 2.7,
    12: 1.9,
}

state_lock = threading.Lock()

state = {
    "timestamp": "",
    "controller_running": False,
    "victron_connected": False,
    "error": "Starting",
}


SOLAR_STATES = {
    0: "OFF",
    2: "FAULT",
    3: "BULK",
    4: "ABSORB",
    5: "FLOAT",
    6: "STORAGE",
    7: "EQUALIZE",
    245: "WAKE-UP",
    252: "EXT CTRL",
}


def theoretical_solar_info():
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    day_number = now.timetuple().tm_yday

    gamma = (
        2.0
        * math.pi
        / 365.0
        * (day_number - 1)
    )

    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )

    latitude = math.radians(
        SOLAR_LATITUDE
    )

    hour_angle = math.acos(
        -math.tan(latitude)
        * math.tan(declination)
    )

    daylight_hours = (
        24.0
        / math.pi
        * hour_angle
    )

    total_minutes = round(
        daylight_hours * 60
    )

    hours = total_minutes // 60
    minutes = total_minutes % 60

    return {
        "daylight":
            f"{hours}h {minutes:02d}m",

        "peak_sun_hours":
            MONTHLY_PEAK_SUN_HOURS[
                now.month
            ],
    }


def controller_running():
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue

            try:
                with open(
                    f"/proc/{pid}/cmdline",
                    "rb",
                ) as handle:
                    command = (
                        handle.read()
                        .replace(b"\0", b" ")
                        .decode(errors="ignore")
                    )

                if (
                    "solar_fortress.py" in command
                    and "--apply" in command
                ):
                    return True

            except Exception:
                pass

    except Exception:
        pass

    return False


def get_cutoff_soc():
    try:
        with open(CONFIG_PATH, "r") as handle:
            config = json.load(handle)

        return float(
            config["soc_thresholds"]["grid_pass_through"]
        )

    except Exception:
        return 30.0


def estimate_runtime(soc, current):
    cutoff = get_cutoff_soc()

    if soc <= cutoff:
        return "AT CUTOFF"

    if current > 0.5:
        return "CHARGING"

    if current >= -0.5:
        return "IDLE"

    usable_ah = (
        BATTERY_CAPACITY_AH
        * ((soc - cutoff) / 100.0)
    )

    hours = usable_ah / abs(current)

    if hours > 240:
        return ">10 DAYS"

    total_minutes = int(hours * 60)

    h = total_minutes // 60
    m = total_minutes % 60

    return f"{h}h {m:02d}m"


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
            except Exception:
                pass

    return None


def bus_read(bus, service, path):
    try:
        obj = bus.get_object(
            service,
            path,
        )

        value = obj.GetValue(
            dbus_interface=
            "com.victronenergy.BusItem"
        )

        return value

    except Exception:
        return None


def bus_float(bus, service, path):
    value = bus_read(
        bus,
        service,
        path,
    )

    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


soc_24h_lock = threading.Lock()
soc_24h_samples = None
soc_24h_bucket = None
soc_24h_bucket_min = None
soc_24h_bucket_min_time = None


def update_24h_low_soc(soc):
    global soc_24h_samples
    global soc_24h_bucket
    global soc_24h_bucket_min
    global soc_24h_bucket_min_time

    now = datetime.now(
        ZoneInfo("America/New_York")
    ).timestamp()

    current = float(soc)

    bucket = int(
        now // 300
    )

    cutoff = (
        now
        - (24 * 60 * 60)
    )

    with soc_24h_lock:

        if soc_24h_samples is None:

            try:
                with open(
                    SOC_24H_PATH,
                    "r",
                    encoding="utf-8",
                ) as handle:
                    saved = json.load(
                        handle
                    )

                soc_24h_samples = [
                    item
                    for item in saved
                    if (
                        float(
                            item.get(
                                "timestamp",
                                0,
                            )
                        )
                        >= cutoff
                    )
                ]

            except Exception:
                soc_24h_samples = []


        if soc_24h_bucket is None:

            soc_24h_bucket = bucket
            soc_24h_bucket_min = current
            soc_24h_bucket_min_time = now


        elif bucket == soc_24h_bucket:

            if current < soc_24h_bucket_min:
                soc_24h_bucket_min = current
                soc_24h_bucket_min_time = now


        else:

            soc_24h_samples.append({
                "timestamp":
                    soc_24h_bucket_min_time,

                "soc":
                    soc_24h_bucket_min,
            })

            soc_24h_samples = [
                item
                for item in soc_24h_samples
                if (
                    float(
                        item.get(
                            "timestamp",
                            0,
                        )
                    )
                    >= cutoff
                )
            ]

            os.makedirs(
                os.path.dirname(
                    SOC_24H_PATH
                ),
                exist_ok=True,
            )

            temporary = (
                SOC_24H_PATH
                + ".tmp"
            )

            with open(
                temporary,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    soc_24h_samples,
                    handle,
                )

            os.replace(
                temporary,
                SOC_24H_PATH,
            )

            soc_24h_bucket = bucket
            soc_24h_bucket_min = current
            soc_24h_bucket_min_time = now


        values = [
            float(
                item.get(
                    "soc",
                    current,
                )
            )
            for item in soc_24h_samples
            if (
                float(
                    item.get(
                        "timestamp",
                        0,
                    )
                )
                >= cutoff
            )
        ]

        if (
            soc_24h_bucket_min_time
            >= cutoff
        ):
            values.append(
                soc_24h_bucket_min
            )

        if not values:
            return current

        return min(values)


def update_today_max_soc(soc):
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    today = now.date().isoformat()
    current = float(soc)

    os.makedirs(
        os.path.dirname(TODAY_MAX_SOC_PATH),
        exist_ok=True,
    )

    saved_date = None
    saved_max = None

    try:
        with open(
            TODAY_MAX_SOC_PATH,
            "r",
            encoding="utf-8",
        ) as handle:
            saved = json.load(handle)

        saved_date = saved.get("date")
        saved_max = float(
            saved.get("max_soc")
        )

    except Exception:
        pass

    if (
        saved_date == today
        and saved_max is not None
    ):
        maximum = max(
            current,
            saved_max,
        )
    else:
        maximum = current

    if (
        saved_date != today
        or saved_max is None
        or maximum != saved_max
    ):
        temporary = (
            TODAY_MAX_SOC_PATH
            + ".tmp"
        )

        with open(
            temporary,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                {
                    "date": today,
                    "max_soc": maximum,
                },
                handle,
            )

        os.replace(
            temporary,
            TODAY_MAX_SOC_PATH,
        )

    return maximum


def update_local_solar_yield(
    total_yield,
    charger_count,
):
    now = datetime.now(
        ZoneInfo("America/New_York")
    )

    today_date = now.date()
    today = today_date.isoformat()

    os.makedirs(
        os.path.dirname(
            SOLAR_YIELD_LOCAL_PATH
        ),
        exist_ok=True,
    )

    try:
        with open(
            SOLAR_YIELD_LOCAL_PATH,
            "r",
            encoding="utf-8",
        ) as handle:
            saved = json.load(handle)

    except Exception:
        saved = {
            "date": today,
            "baseline": float(total_yield),
            "today": 0.0,
            "history": [],
        }

    current_total = float(total_yield)

    saved_date = str(
        saved.get("date", today)
    )

    baseline = float(
        saved.get(
            "baseline",
            current_total,
        )
    )

    today_yield = float(
        saved.get("today", 0.0)
    )

    history = list(
        saved.get("history", [])
    )


    if saved_date != today:

        history = [
            item
            for item in history
            if item.get("date")
            != saved_date
        ]

        history.append({
            "date": saved_date,
            "yield": today_yield,
        })

        baseline = current_total
        today_yield = 0.0


    elif charger_count == 4:

        calculated = (
            current_total
            - baseline
        )

        if calculated < -0.05:
            baseline = (
                current_total
                - today_yield
            )

        else:
            today_yield = max(
                today_yield,
                calculated,
            )


    history = sorted(
        history,
        key=lambda item:
            item.get("date", ""),
    )[-14:]


    yield_by_date = {
        str(item.get("date")):
            float(item.get("yield", 0.0))
        for item in history
    }


    seven_day = today_yield

    for offset in range(1, 7):

        date_key = (
            today_date
            - timedelta(days=offset)
        ).isoformat()

        seven_day += (
            yield_by_date.get(
                date_key,
                0.0,
            )
        )


    state = {
        "date": today,
        "baseline": baseline,
        "today": today_yield,
        "history": history,
    }

    temporary = (
        SOLAR_YIELD_LOCAL_PATH
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )

    os.replace(
        temporary,
        SOLAR_YIELD_LOCAL_PATH,
    )


    return {
        "today": today_yield,
        "seven_day": seven_day,
    }


def read_last_full_charge_seconds(bus):
    for service in sorted(bus.list_names()):
        if not service.startswith(
            "com.victronenergy.battery."
        ):
            continue

        try:
            value = bus_float(
                bus,
                service,
                "/History/TimeSinceLastFullCharge",
            )

            if value is not None:
                return max(
                    0.0,
                    float(value),
                )

        except Exception:
            continue

    return None


def discover_solar(bus):
    services = [
        name
        for name in bus.list_names()
        if name.startswith(
            "com.victronenergy.solarcharger."
        )
    ]

    chargers = []

    for service in services:
        instance = bus_read(
            bus,
            service,
            "/DeviceInstance",
        )

        try:
            instance = int(instance)
        except Exception:
            instance = 9999

        chargers.append(
            (instance, service)
        )

    chargers.sort()

    results = []

    total_power = 0.0
    total_current = 0.0
    total_yield = 0.0
    daily_yields = [0.0] * 7

    for number, (
        instance,
        service,
    ) in enumerate(chargers, 1):

        custom_name = bus_read(
            bus,
            service,
            "/CustomName",
        )

        product = bus_read(
            bus,
            service,
            "/ProductName",
        )

        raw_state = bus_read(
            bus,
            service,
            "/State",
        )

        try:
            state_number = int(raw_state)

            state_text = SOLAR_STATES.get(
                state_number,
                str(state_number),
            )

        except Exception:
            state_number = None
            state_text = "N/A"

        power = bus_float(
            bus,
            service,
            "/Yield/Power",
        )

        current = bus_float(
            bus,
            service,
            "/Dc/0/Current",
        )

        pv_voltage = bus_float(
            bus,
            service,
            "/Pv/V",
        )

        battery_voltage = bus_float(
            bus,
            service,
            "/Dc/0/Voltage",
        )

        yield_system = bus_float(
            bus,
            service,
            "/Yield/System",
        )

        if power is not None:
            total_power += power

        if current is not None:
            total_current += current

        if yield_system is not None:
            total_yield += yield_system

        for day in range(7):
            daily_value = bus_float(
                bus,
                service,
                f"/History/Daily/{day}/Yield",
            )

            if daily_value is not None:
                daily_yields[day] += float(
                    daily_value
                )

        name = str(custom_name or "").strip()

        if not name:
            name = f"MPPT {number}"

        results.append({
            "name": name,
            "instance": instance,
            "service": service,
            "product": str(product or ""),
            "state": state_text,
            "state_number": state_number,
            "power": power,
            "current": current,
            "pv_voltage": pv_voltage,
            "battery_voltage": battery_voltage,
            "yield_system": yield_system,
        })

    return {
        "chargers": results,
        "power": total_power,
        "current": total_current,
        "yield_system": total_yield,
        "today_yield": daily_yields[0],
        "seven_day_yield": sum(daily_yields),
    }


def sample_loop():
    hardware = None
    solar_bus = None

    while True:
        solar_info = theoretical_solar_info()

        current = {
            "daylight":
                solar_info["daylight"],

            "peak_sun_hours":
                solar_info["peak_sun_hours"],

            "timestamp": datetime.now(
                ZoneInfo("America/New_York")
            ).strftime(
                "%Y-%m-%d %I:%M:%S %p %Z"
            ),

            "controller_running":
                controller_running(),

            "victron_connected": False,

            "error": None,
        }

        try:
            if hardware is None:
                hardware = VictronHardware()

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

            ac_input_power = optional_float(
                hardware,
                "/Ac/ActiveIn/L1/P",
                "/Ac/In/1/L1/P",
            )

            ac_output_power = optional_float(
                hardware,
                "/Ac/Out/L1/P",
            )

            ac_connected_bool = (
                None
                if ac_connected is None
                else bool(ac_connected)
            )

            if not ac_connected_bool:
                grid_import_power = 0.0
                grid_import_current = 0.0
            else:
                grid_import_power = (
                    None
                    if ac_input_power is None
                    else max(
                        0.0,
                        float(ac_input_power),
                    )
                )

                grid_import_current = (
                    None
                    if ac_current is None
                    else max(
                        0.0,
                        float(ac_current),
                    )
                )

            mode = (
                snapshot.operating_mode.value
                .replace("_", " ")
                .upper()
            )

            if ignore_ac is None:
                ac_policy = "UNKNOWN"

            elif bool(ignore_ac):
                ac_policy = "IGNORE AC"

            else:
                ac_policy = "ACCEPT AC"

            current.update({
                "victron_connected": True,

                "soc":
                    float(snapshot.soc),

                "runtime":
                    estimate_runtime(
                        float(snapshot.soc),
                        float(battery["current"]),
                    ),

                "cutoff_soc":
                    get_cutoff_soc(),

                "mode":
                    mode,

                "battery_state":
                    str(battery["flow"]),

                "battery_voltage":
                    float(battery["voltage"]),

                "battery_current":
                    float(battery["current"]),

                "battery_power":
                    float(battery["power"]),

            "today_max_soc":
                update_today_max_soc(
                    float(snapshot.soc)
                ),

            "low_soc_24h":
                update_24h_low_soc(
                    float(snapshot.soc)
                ),

                "ac_present":
                    bool(snapshot.ac_available),

                "ac_connected":
                    ac_connected_bool,

                "ac_policy":
                    ac_policy,

                "grid_charging":
                    (
                        snapshot.operating_mode.value
                        == "grid_charging"
                    ),

                "ac_current":
                    grid_import_current,

                "ac_input_power":
                    grid_import_power,

                "ac_output_power":
                    ac_output_power,
            })

            try:
                if solar_bus is None:
                    solar_bus = dbus.SystemBus(
                        private=True
                    )

                solar = discover_solar(
                    solar_bus
                )

                local_solar = update_local_solar_yield(
                    solar["yield_system"],
                    len(solar["chargers"]),
                )

                current.update({
                    "solar_chargers":
                        solar["chargers"],

                    "solar_power":
                        solar["power"],

                    "solar_current":
                        solar["current"],

                    "solar_yield_system":
                        solar["yield_system"],

                    "solar_today_yield":
                        local_solar["today"],

                    "solar_seven_day_yield":
                        local_solar["seven_day"],

                    "last_full_charge_seconds":
                        read_last_full_charge_seconds(
                            solar_bus
                        ),

                    "solar_error":
                        None,
                })

            except Exception as exc:
                solar_bus = None

                current.update({
                    "solar_chargers": [],
                    "solar_power": None,
                    "solar_current": None,
                    "solar_yield_system": None,
                    "solar_today_yield": None,
                    "solar_seven_day_yield": None,
                    "last_full_charge_seconds": None,
                    "solar_error":
                        f"{type(exc).__name__}: {exc}",
                })

        except Exception as exc:
            hardware = None
            solar_bus = None

            current["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        with state_lock:
            state.clear()
            state.update(current)

        time.sleep(1)


HTML = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>Solar Fortress</title>

<style>
:root {
    color-scheme: dark;

    --bg: #090909;
    --panel: #111;
    --panel2: #171717;
    --line: #292929;

    --text: #eee;
    --muted: #999;

    --good: #7cff96;
    --bad: #ff7777;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;

    background: var(--bg);
    color: var(--text);

    font-family:
        ui-monospace,
        SFMono-Regular,
        Menlo,
        Consolas,
        monospace;
}

.wrap {
    max-width: 900px;

    margin: auto;
    padding: 8px;
}

header {
    display: grid;

    grid-template-columns:
        1fr auto;

    gap: 4px 12px;

    align-items: end;

    padding:
        5px 3px 8px 3px;
}

h1 {
    margin: 0;

    font-size: 1.65rem;
    letter-spacing: .11em;
}

.time {
    color: var(--muted);

    font-size: .78rem;
    white-space: nowrap;
}

.health {
    grid-column: 1 / -1;

    display: flex;
    gap: 12px;

    font-size: .75rem;
}

.good {
    color: var(--good);
}

.bad {
    color: var(--bad);
}

.summary {
    display: grid;

    grid-template-columns:
        repeat(4, minmax(0, 1fr));

    border:
        1px solid var(--line);

    border-radius: 9px;

    background: var(--panel);

    overflow: hidden;
}

.metric {
    min-width: 0;

    padding: 8px 7px;

    border-right:
        1px solid var(--line);

    text-align: center;
}

.metric:last-child {
    border-right: 0;
}

.metric-label {
    color: var(--muted);

    font-size: .64rem;
    text-transform: uppercase;

    white-space: nowrap;
}

.metric-value {
    margin-top: 3px;

    font-size: 1.15rem;
    font-weight: 700;

    white-space: nowrap;
}

.status {
    display: grid;

    grid-template-columns:
        1.5fr 1fr 1fr 1fr;

    margin-top: 6px;

    border:
        1px solid var(--line);

    border-radius: 9px;

    background: var(--panel);

    overflow: hidden;
}

.status-item {
    min-width: 0;

    padding: 7px;

    border-right:
        1px solid var(--line);

    text-align: center;
}

.status-item:last-child {
    border-right: 0;
}

.status-value {
    margin-top: 2px;

    font-size: .84rem;
    font-weight: 700;

    white-space: normal;
}

.section {
    margin-top: 6px;

    border:
        1px solid var(--line);

    border-radius: 9px;

    background: var(--panel);

    overflow: hidden;
}

.section-title {
    display: flex;

    justify-content:
        space-between;

    gap: 8px;

    padding: 6px 8px;

    background: var(--panel2);

    border-bottom:
        1px solid var(--line);

    font-size: .84rem;
    font-weight: 700;
}

.inline-grid {
    display: grid;

    grid-template-columns:
        repeat(3, minmax(0, 1fr));
}

.inline-item {
    padding: 7px;

    text-align: center;

    border-right:
        1px solid var(--line);
}

.inline-item:last-child {
    border-right: 0;
}

.inline-label {
    color: var(--muted);

    font-size: .64rem;
}

.inline-value {
    margin-top: 2px;

    font-size: .9rem;
    font-weight: 700;
}

.mppt-head,
.mppt-row,
.mppt-total {
    display: grid;

    grid-template-columns:
        64px 1fr 1fr 1fr 76px;

    align-items: center;

    column-gap: 3px;

    padding: 5px 7px;
}

.mppt-head {
    color: var(--muted);

    font-size: .62rem;

    border-bottom:
        1px solid var(--line);
}

.mppt-row {
    min-height: 29px;

    font-size: .75rem;

    border-bottom:
        1px solid #202020;
}

.mppt-row:last-child {
    border-bottom: 0;
}

.mppt-name {
    font-weight: 700;
}

.num {
    text-align: right;

    white-space: nowrap;
}

.mppt-state {
    text-align: right;

    font-weight: 700;

    white-space: nowrap;
}

.mppt-total {
    background: var(--panel2);

    font-size: .75rem;
    font-weight: 700;

    border-top:
        1px solid var(--line);
}

.sun-reference {
    margin-top: 6px;

    border:
        1px solid #222;

    border-radius: 9px;

    background: #0d0d0d;

    overflow: hidden;
}

.sun-reference-title {
    display: flex;

    justify-content:
        space-between;

    align-items: center;

    gap: 10px;

    padding: 5px 8px;

    border-bottom:
        1px solid #202020;

    color: #888;

    font-size: .68rem;

    font-weight: 700;
}

.sun-reference-note {
    color: #666;

    font-size: .60rem;

    font-weight: 400;
}

.sun-reference-grid {
    display: grid;

    grid-template-columns:
        repeat(2, minmax(0, 1fr));
}

.sun-reference-item {
    padding: 6px 8px;

    text-align: center;

    border-right:
        1px solid #202020;
}

.sun-reference-item:last-child {
    border-right: 0;
}

.sun-reference-label {
    color: #777;

    font-size: .58rem;

    text-transform: uppercase;
}

.sun-reference-value {
    margin-top: 2px;

    color: #bbb;

    font-size: .82rem;

    font-weight: 700;
}


.ac-grid {
    display: grid;

    grid-template-columns:
        repeat(3, minmax(0, 1fr));
}

.ac-item {
    min-width: 0;

    padding: 6px;

    text-align: center;

    border-right:
        1px solid var(--line);

    border-bottom:
        1px solid var(--line);
}

.ac-item:nth-child(3n) {
    border-right: 0;
}

.ac-item:nth-last-child(-n+3) {
    border-bottom: 0;
}

.ac-label {
    color: var(--muted);

    font-size: .61rem;
}

.ac-value {
    margin-top: 2px;

    font-size: .78rem;
    font-weight: 700;

    overflow-wrap: anywhere;
}

#error {
    display: none;

    margin-top: 6px;

    padding: 7px;

    border:
        1px solid #652f2f;

    border-radius: 8px;

    color: var(--bad);

    font-size: .72rem;
}

@media (max-width: 520px) {

    .wrap {
        padding: 5px;
    }

    header {
        padding:
            4px 2px 6px 2px;
    }

    h1 {
        font-size: 1.18rem;
    }

    .time {
        font-size: .66rem;
    }

    .health {
        gap: 8px;
        font-size: .65rem;
    }

    .metric {
        padding:
            6px 2px;
    }

    .metric-label {
        font-size: .55rem;
    }

    .metric-value {
        font-size: .94rem;
    }

    .status-item {
        padding:
            6px 2px;
    }

    .status-value {
        font-size: .67rem;
    }

    .section-title {
        padding:
            5px 6px;

        font-size: .73rem;
    }

    .inline-item {
        padding:
            5px 2px;
    }

    .inline-label {
        font-size: .55rem;
    }

    .inline-value {
        font-size: .76rem;
    }

    .mppt-head,
    .mppt-row,
    .mppt-total {
        grid-template-columns:
            52px 1fr 1fr 1fr 62px;

        padding:
            4px 4px;

        column-gap: 2px;
    }

    .mppt-head {
        font-size: .52rem;
    }

    .mppt-row,
    .mppt-total {
        font-size: .64rem;
    }

    .ac-item {
        padding:
            5px 2px;
    }

    .ac-label {
        font-size: .53rem;
    }

    .ac-value {
        font-size: .65rem;
    }
}
</style>

<style id="solar-fortress-premium">

:root {
    --bg: #07090c;
    --panel: #0d1117;
    --panel2: #111820;

    --line:
        rgba(255, 255, 255, 0.085);

    --line-strong:
        rgba(255, 255, 255, 0.14);

    --text: #f3f6f9;
    --muted: #87929f;

    --good: #6ee790;
    --bad: #ff7474;

    --cyan: #62d9ff;
    --blue: #8ba7ff;
    --amber: #ffc968;
}


/* ---------- overall ---------- */

body {
    background:
        radial-gradient(
            circle at 50% -250px,
            #17202b 0,
            #090c10 430px,
            #07090c 760px
        );

    color: var(--text);

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Roboto,
        Helvetica,
        Arial,
        sans-serif;

    font-variant-numeric:
        tabular-nums;
}


.wrap {
    max-width: 980px;
}


/* ---------- header ---------- */

header {
    padding-top: 10px;
    padding-bottom: 10px;
}


h1 {
    color: #ffffff;

    font-size: 1.75rem;

    font-weight: 750;

    letter-spacing: .14em;

    text-shadow:
        0 0 24px
        rgba(255, 255, 255, .06);
}


h1::after {
    content: "";

    display: inline-block;

    width: 7px;
    height: 7px;

    margin-left: 12px;
    margin-bottom: 4px;

    border-radius: 50%;

    background: var(--good);

    box-shadow:
        0 0 10px
        rgba(110, 231, 144, .65);
}


.time {
    color: #8d99a6;
}


/* ---------- numbers ---------- */

.metric-value,
.status-value,
.inline-value,
.mppt-row,
.mppt-total,
.ac-value,
.sun-reference-value {
    font-family:
        "SFMono-Regular",
        Consolas,
        "Liberation Mono",
        Menlo,
        monospace;

    font-variant-numeric:
        tabular-nums;
}


/* ---------- main surfaces ---------- */

.summary,
.status,
.section,
.sun-reference {
    border:
        1px solid var(--line);

    background:
        linear-gradient(
            180deg,
            rgba(18, 24, 32, .96),
            rgba(11, 15, 20, .98)
        );

    border-radius: 12px;

    box-shadow:
        0 8px 24px
        rgba(0, 0, 0, .24),
        inset 0 1px 0
        rgba(255, 255, 255, .025);
}


.summary {
    overflow: hidden;
}


/* subtle accent for the four headline numbers */

.summary .metric {
    position: relative;

    padding-top: 10px;
    padding-bottom: 10px;
}


.summary .metric::before {
    content: "";

    position: absolute;

    left: 18%;
    right: 18%;
    top: 0;

    height: 2px;

    border-radius: 0 0 2px 2px;

    opacity: .85;
}


.summary .metric:nth-child(1)::before {
    background: var(--cyan);
}


.summary .metric:nth-child(2)::before {
    background: var(--blue);
}


.summary .metric:nth-child(3)::before {
    background: var(--amber);
}


.summary .metric:nth-child(4)::before {
    background: #b6c0cb;
}


/* ---------- labels ---------- */

.metric-label,
.inline-label,
.ac-label,
.mppt-head,
.sun-reference-label {
    color: #7f8b98;

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    font-weight: 650;

    letter-spacing: .075em;

    text-transform: uppercase;
}


.metric-value {
    color: #f7f9fb;

    font-weight: 750;
}


#soc {
    color: var(--cyan);
}


#solar_power {
    color: var(--amber);
}


/* ---------- status strip ---------- */

.status-item {
    background:
        rgba(255, 255, 255, .008);
}


.status-item + .status-item {
    border-left:
        1px solid var(--line);
}


#grid_import_short {
    color: #dbe4ed;
}


/* ---------- section headers ---------- */

.section-title {
    background:
        linear-gradient(
            90deg,
            rgba(255, 255, 255, .035),
            rgba(255, 255, 255, .012)
        );

    border-bottom:
        1px solid var(--line);

    color: #e6ebf0;

    letter-spacing: .035em;
}


/* battery state gets enough emphasis without screaming */

#battery_state {
    color: var(--amber);
}


/* ---------- MPPT table ---------- */

.mppt-head {
    background:
        rgba(255, 255, 255, .018);
}


.mppt-row {
    transition:
        background .12s ease;
}


@media (hover: hover) and (pointer: fine) {

    .mppt-row:hover {
        background:
            rgba(255, 255, 255, .035);
    }
}


.mppt-total {
    background:
        rgba(255, 255, 255, .028);

    border-top:
        1px solid var(--line-strong);
}


.mppt-name {
    color: #edf2f6;
}


.mppt-state {
    color: #aeb8c2;
}


/* ---------- sun reference intentionally subordinate ---------- */

.sun-reference {
    background:
        rgba(8, 11, 15, .72);

    border-color:
        rgba(255, 255, 255, .055);

    box-shadow: none;
}


.sun-reference-title {
    border-bottom:
        1px solid
        rgba(255, 255, 255, .05);
}


.sun-reference-note {
    color: #67717c;
}


.sun-reference-value {
    color: #aab4be;
}


/* ---------- health ---------- */

.health {
    color: #aab4be;

    letter-spacing: .03em;
}


.good {
    color: var(--good);

    text-shadow:
        0 0 10px
        rgba(110, 231, 144, .16);
}


/* ---------- mobile ---------- */

@media (max-width: 520px) {

    body {
        background:
            linear-gradient(
                #090c10,
                #07090c
            );
    }


    .wrap {
        padding: 5px;
    }


    h1 {
        font-size: 1.22rem;
    }


    h1::after {
        width: 5px;
        height: 5px;

        margin-left: 7px;
        margin-bottom: 3px;
    }


    .summary,
    .status,
    .section,
    .sun-reference {
        border-radius: 9px;

        box-shadow:
            0 4px 12px
            rgba(0, 0, 0, .18);
    }


    .summary .metric {
        padding-top: 8px;
        padding-bottom: 7px;
    }


    .summary .metric::before {
        left: 28%;
        right: 28%;
    }
}

</style>


<style id="solar-fortress-premium-v2">

:root {
    --surface-0: #06080b;
    --surface-1: rgba(15, 20, 27, .92);
    --surface-2: rgba(19, 26, 35, .88);

    --edge: rgba(255,255,255,.075);
    --edge-hi: rgba(255,255,255,.13);

    --white: #f7f9fb;
    --soft: #b4bdc7;
    --dim: #74808c;

    --green: #73f59a;
    --cyan: #65dfff;
    --blue: #8faaff;
    --amber: #ffd06f;
    --red: #ff7474;
}


/* -------------------------------------------------
   PAGE
------------------------------------------------- */

body {
    background:
        radial-gradient(
            ellipse at 50% -240px,
            rgba(57, 85, 116, .24),
            transparent 520px
        ),
        linear-gradient(
            180deg,
            #090d12,
            #06080b 500px
        );

    min-height: 100%;
}


.wrap {
    max-width: 1040px;

    padding:
        10px 10px 18px;

    position: relative;
}


/* very subtle top highlight */

.wrap::before {
    content: "";

    position: absolute;

    top: 0;
    left: 12%;
    right: 12%;

    height: 1px;

    background:
        linear-gradient(
            90deg,
            transparent,
            rgba(101,223,255,.32),
            transparent
        );
}


/* -------------------------------------------------
   HEADER
------------------------------------------------- */

header {
    padding:
        12px 4px 11px;
}


h1 {
    font-weight: 760;

    letter-spacing: .16em;

    background:
        linear-gradient(
            180deg,
            #ffffff,
            #d9e0e7
        );

    -webkit-background-clip: text;
    background-clip: text;

    color: transparent;
}


.time {
    font-family:
        "SFMono-Regular",
        Consolas,
        monospace;

    color: #7f8b97;

    letter-spacing: .025em;
}


.health {
    margin-top: 2px;

    color: var(--soft);

    font-size: .72rem;

    font-weight: 600;
}


/* -------------------------------------------------
   PANELS
------------------------------------------------- */

.summary,
.status,
.section,
.sun-reference {
    position: relative;

    background:
        linear-gradient(
            180deg,
            rgba(20, 27, 36, .92),
            rgba(11, 15, 21, .96)
        );

    border:
        1px solid var(--edge);

    box-shadow:
        inset 0 1px 0
        rgba(255,255,255,.025),

        0 9px 26px
        rgba(0,0,0,.20);

    backdrop-filter:
        blur(8px);

    -webkit-backdrop-filter:
        blur(8px);
}


/* subtle edge illumination */

.summary::after,
.section::after {
    content: "";

    position: absolute;

    inset: 0;

    pointer-events: none;

    border-radius: inherit;

    box-shadow:
        inset 0 0 0 1px
        rgba(255,255,255,.012);
}


/* -------------------------------------------------
   MAIN METRICS
------------------------------------------------- */

.metric {
    transition:
        background .18s ease;
}


.metric-value {
    margin-top: 5px;

    font-size: 1.32rem;

    letter-spacing: -.035em;

    text-shadow:
        0 1px 14px
        rgba(255,255,255,.045);
}


.metric-label {
    letter-spacing: .11em;

    font-size: .60rem;
}


#soc {
    color: var(--cyan);

    text-shadow:
        0 0 18px
        rgba(101,223,255,.14);
}


#solar_power {
    color: var(--amber);

    text-shadow:
        0 0 18px
        rgba(255,208,111,.12);
}


#runtime {
    color: #d8e0ff;
}


#load_power {
    color: #e7ebef;
}


/* -------------------------------------------------
   SECOND STATUS ROW
------------------------------------------------- */

.status {
    border-color:
        rgba(255,255,255,.065);
}


.status-item {
    position: relative;

    background:
        linear-gradient(
            180deg,
            rgba(255,255,255,.014),
            transparent
        );
}


.status-value {
    margin-top: 4px;

    letter-spacing: .015em;
}


/* -------------------------------------------------
   SECTION HEADERS
------------------------------------------------- */

.section-title {
    min-height: 34px;

    align-items: center;

    padding:
        7px 10px;

    background:
        linear-gradient(
            90deg,
            rgba(255,255,255,.045),
            rgba(255,255,255,.012) 70%,
            transparent
        );

    color: #f0f3f6;

    font-size: .78rem;

    font-weight: 720;

    letter-spacing: .055em;
}


/* tiny accent rail */

.section-title::before {
    content: "";

    width: 2px;
    height: 14px;

    margin-right: 7px;

    border-radius: 2px;

    background:
        linear-gradient(
            var(--cyan),
            var(--blue)
        );

    box-shadow:
        0 0 8px
        rgba(101,223,255,.25);
}


/* -------------------------------------------------
   BATTERY
------------------------------------------------- */

#battery_state {
    transition:
        color .2s ease,
        text-shadow .2s ease;
}


.battery-charging {
    color: var(--green) !important;

    text-shadow:
        0 0 12px
        rgba(115,245,154,.18);
}


.battery-discharging {
    color: var(--amber) !important;

    text-shadow:
        0 0 12px
        rgba(255,208,111,.14);
}


.battery-idle {
    color: var(--soft) !important;
}


/* -------------------------------------------------
   GRID / AC STATES
------------------------------------------------- */

.state-good {
    color: var(--green) !important;
}


.state-muted {
    color: #9ca7b2 !important;
}


.state-warning {
    color: var(--amber) !important;
}


.state-bad {
    color: var(--red) !important;
}


/* -------------------------------------------------
   MPPT TABLE
------------------------------------------------- */

.mppt-head {
    padding-top: 7px;
    padding-bottom: 7px;

    background:
        rgba(0,0,0,.14);

    color: #737f8b;

    letter-spacing: .09em;
}


.mppt-row {
    position: relative;

    transition:
        background .15s ease,
        transform .15s ease;
}


.mppt-row:nth-child(even) {
    background:
        rgba(255,255,255,.008);
}


.mppt-name {
    color: #eef2f5;

    letter-spacing: .02em;
}


.mppt-total {
    padding-top: 7px;
    padding-bottom: 7px;

    background:
        linear-gradient(
            90deg,
            rgba(101,223,255,.035),
            rgba(255,208,111,.025)
        );
}


/* -------------------------------------------------
   SUN REFERENCE
------------------------------------------------- */

.sun-reference {
    opacity: .82;

    box-shadow: none;

    background:
        rgba(7,10,14,.74);
}


.sun-reference-title {
    color: #77828e;

    letter-spacing: .08em;
}


.sun-reference-value {
    font-size: .85rem;

    color: #abb5bf;
}


/* -------------------------------------------------
   AC GRID
------------------------------------------------- */

.ac-item {
    transition:
        background .15s ease;
}


.ac-value {
    margin-top: 3px;

    letter-spacing: .01em;
}


/* -------------------------------------------------
   DESKTOP MICRO-INTERACTION
------------------------------------------------- */

@media (hover: hover) and (pointer: fine) {

    .metric:hover,
    .status-item:hover,
    .ac-item:hover {
        background:
            rgba(255,255,255,.025);
    }


    .mppt-row:hover {
        background:
            rgba(101,223,255,.035);

        transform:
            translateX(2px);
    }
}


/* -------------------------------------------------
   MOBILE
------------------------------------------------- */

@media (max-width: 520px) {

    .wrap {
        padding:
            5px 5px 12px;
    }


    header {
        padding:
            8px 3px 7px;
    }


    h1 {
        font-size: 1.17rem;

        letter-spacing: .135em;
    }


    .metric-value {
        font-size: .98rem;

        margin-top: 3px;
    }


    .metric-label {
        font-size: .52rem;
    }


    .section-title {
        min-height: 29px;

        padding:
            5px 7px;

        font-size: .68rem;
    }


    .section-title::before {
        height: 11px;

        margin-right: 4px;
    }


    .summary,
    .status,
    .section,
    .sun-reference {
        border-radius: 8px;

        backdrop-filter: none;

        -webkit-backdrop-filter: none;
    }


    .mppt-row {
        min-height: 27px;
    }
}


/* -------------------------------------------------
   MOTION ACCESSIBILITY
------------------------------------------------- */

@media (prefers-reduced-motion: reduce) {

    * {
        transition: none !important;
    }
}

</style>


<style id="solar-fortress-header-alignment-fix">

.section-title {
    position: relative;

    justify-content:
        space-between;

    padding-left: 18px;
}


/*
The accent rail is decorative only.
Do not let it participate in flex layout.
*/
.section-title::before {
    position: absolute;

    left: 8px;
    top: 50%;

    transform:
        translateY(-50%);

    margin: 0;
}


/*
If a section has only one content span,
it naturally stays on the left.
*/
.section-title > span:first-child {
    margin-right: auto;
}


/*
If a second span exists, such as the
solar-controller total, keep it right.
*/
.section-title > span:last-child:not(:first-child) {
    margin-left: auto;
}

</style>


<style id="solar-fortress-history-metrics">

.history-strip {
    display: grid;

    margin-top: 6px;

    border:
        1px solid rgba(255,255,255,.065);

    border-radius: 9px;

    overflow: hidden;

    background:
        linear-gradient(
            180deg,
            rgba(17,23,31,.92),
            rgba(10,14,19,.96)
        );

    box-shadow:
        inset 0 1px 0
        rgba(255,255,255,.02);
}


.history-single {
    grid-template-columns:
        repeat(3, minmax(0,1fr));
}

.history-single .history-item + .history-item {
    border-left:
        1px solid rgba(255,255,255,.06);
}


.history-solar {
    grid-template-columns:
        repeat(2, minmax(0,1fr));
}

.history-solar::before {
    content: "SOLAR YIELD";

    grid-column:
        1 / -1;

    padding:
        6px 10px 4px;

    border-bottom:
        1px solid rgba(255,255,255,.05);

    color: #77828e;

    font-size: .62rem;

    font-weight: 700;

    letter-spacing: .09em;

    text-align: left;
}


.history-item {
    display: flex;

    align-items: center;

    justify-content:
        space-between;

    gap: 10px;

    padding:
        7px 10px;
}


.history-solar .history-item {
    flex-direction: column;

    justify-content: center;

    gap: 2px;

    text-align: center;
}


.history-solar .history-item + .history-item {
    border-left:
        1px solid rgba(255,255,255,.06);
}


.history-label {
    color: #75818d;

    font-size: .58rem;

    font-weight: 650;

    letter-spacing: .085em;
}


.history-value {
    color: #dce3e9;

    font-family:
        "SFMono-Regular",
        Consolas,
        "Liberation Mono",
        monospace;

    font-size: .82rem;

    font-weight: 700;

    font-variant-numeric:
        tabular-nums;
}


#solar_today_yield {
    color: var(--amber);
}


#solar_seven_day_yield {
    color: #dce3e9;
}


#last_full_charge {
    color: #aeb9c3;
}


@media (max-width: 520px) {

    .history-strip {
        margin-top: 4px;

        border-radius: 8px;
    }


    .history-item {
        padding:
            6px 8px;
    }


    .history-value {
        font-size: .76rem;
    }
}

</style>

</head>

<body>
<div class="wrap">

<header>
    <h1>SOLAR FORTRESS</h1>

    <div
        class="time"
        id="timestamp"
    >
        Connecting...
    </div>

    <div class="health">
        <span>
            CTRL
            <b id="controller">
                ...
            </b>
        </span>

        <span>
            VICTRON
            <b id="victron">
                ...
            </b>
        </span>
    </div>
</header>


<div class="summary">

    <div class="metric">
        <div class="metric-label">
            SOC
        </div>

        <div
            class="metric-value"
            id="soc"
        >
            --
        </div>
    </div>


    <div class="metric">
        <div
            class="metric-label"
            id="eta-label"
        >
            ETA → 30%
        </div>

        <div
            class="metric-value"
            id="runtime"
        >
            --
        </div>
    </div>


    <div class="metric">
        <div class="metric-label">
            Solar
        </div>

        <div
            class="metric-value"
            id="solar_power"
        >
            --
        </div>
    </div>


    <div class="metric">
        <div class="metric-label">
            Load
        </div>

        <div
            class="metric-value"
            id="load_power"
        >
            --
        </div>
    </div>

</div>


<div class="status">

    <div class="status-item">
        <div class="metric-label">
            Mode
        </div>

        <div
            class="status-value"
            id="mode"
        >
            --
        </div>
    </div>


    <div class="status-item">
        <div class="metric-label">
            Battery
        </div>

        <div
            class="status-value"
            id="battery_power"
        >
            --
        </div>
    </div>


    <div class="status-item">
        <div class="metric-label">
            Grid import
        </div>

        <div
            class="status-value"
            id="grid_import_short"
        >
            --
        </div>
    </div>


    <div class="status-item">
        <div class="metric-label">
            AC present
        </div>

        <div
            class="status-value"
            id="ac_present_short"
        >
            --
        </div>
    </div>

</div>


<div class="section">

    <div class="section-title">
        <span>
            BATTERY:
            <span id="battery_state">
                --
            </span>
        </span>
    </div>


    <div class="inline-grid">

        <div class="inline-item">
            <div class="inline-label">
                Voltage
            </div>

            <div
                class="inline-value"
                id="battery_voltage"
            >
                --
            </div>
        </div>


        <div class="inline-item">
            <div class="inline-label">
                Current
            </div>

            <div
                class="inline-value"
                id="battery_current"
            >
                --
            </div>
        </div>


        <div class="inline-item">
            <div class="inline-label">
                Power
            </div>

            <div
                class="inline-value"
                id="battery_power2"
            >
                --
            </div>
        </div>

    </div>
</div>


<div class="history-strip history-single">

    <div class="history-item">
        <div class="history-label">
            LAST FULL CHARGE
        </div>

        <div
            class="history-value"
            id="last_full_charge"
        >
            --
        </div>
    </div>

    <div class="history-item">
        <div class="history-label">
            TODAY'S MAX SOC
        </div>

        <div
            class="history-value"
            id="today_max_soc"
        >
            --
        </div>
    </div>

    <div class="history-item">
        <div class="history-label">
            LAST 24 HOURS LOW SOC
        </div>

        <div
            class="history-value"
            id="low_soc_24h"
        >
            --
        </div>
    </div>


</div>


<div class="history-strip history-solar">

    <div class="history-item">
        <div class="history-label">
            TODAY
        </div>

        <div
            class="history-value"
            id="solar_today_yield"
        >
            --
        </div>
    </div>

    <div class="history-item">
        <div class="history-label">
            LAST 7 DAYS
        </div>

        <div
            class="history-value"
            id="solar_seven_day_yield"
        >
            --
        </div>
    </div>

</div>


<div class="section">

    <div class="section-title">
        <span>
            SOLAR CONTROLLERS
        </span>

        <span id="solar_summary">
            --
        </span>
    </div>


    <div class="mppt-head">
        <span>MPPT</span>
        <span class="num">W</span>
        <span class="num">A</span>
        <span class="num">PV V</span>
        <span class="num">STATE</span>
    </div>


    <div id="mppt_rows"></div>


    <div class="mppt-total">
        <span>TOTAL</span>

        <span
            class="num"
            id="solar_total_w"
        >
            --
        </span>

        <span
            class="num"
            id="solar_total_a"
        >
            --
        </span>

        <span></span>
        <span></span>
    </div>

</div>


<div class="sun-reference">

    <div class="sun-reference-title">
        <span>SUN REFERENCE</span>

        <span class="sun-reference-note">
            THEORETICAL · 37663
        </span>
    </div>

    <div class="sun-reference-grid">

        <div class="sun-reference-item">
            <div class="sun-reference-label">
                Daylight
            </div>

            <div
                class="sun-reference-value"
                id="daylight"
            >
                --
            </div>
        </div>

        <div class="sun-reference-item">
            <div class="sun-reference-label">
                Peak sun
            </div>

            <div
                class="sun-reference-value"
                id="peak_sun"
            >
                --
            </div>
        </div>

    </div>

</div>


<div class="section">

    <div class="section-title">
        <span>AC</span>
    </div>


    <div class="ac-grid">

        <div class="ac-item">
            <div class="ac-label">
                Present
            </div>

            <div
                class="ac-value"
                id="ac_present"
            >
                --
            </div>
        </div>


        <div class="ac-item">
            <div class="ac-label">
                Connected
            </div>

            <div
                class="ac-value"
                id="ac_connected"
            >
                --
            </div>
        </div>


        <div class="ac-item">
            <div class="ac-label">
                Policy
            </div>

            <div
                class="ac-value"
                id="ac_policy"
            >
                --
            </div>
        </div>


        <div class="ac-item">
            <div class="ac-label">
                Grid charge
            </div>

            <div
                class="ac-value"
                id="grid_charging"
            >
                --
            </div>
        </div>


        <div class="ac-item">
            <div class="ac-label">
                Grid import
            </div>

            <div
                class="ac-value"
                id="ac_input"
            >
                --
            </div>
        </div>


        <div class="ac-item">
            <div class="ac-label">
                Output
            </div>

            <div
                class="ac-value"
                id="ac_output"
            >
                --
            </div>
        </div>

    </div>
</div>


<div id="error"></div>

</div>


<script>
function setText(id, value) {
    document.getElementById(id).textContent =
        value;
}


function setStateClass(
    id,
    className
) {
    const el =
        document.getElementById(id);

    if (!el) {
        return;
    }

    el.classList.remove(
        "state-good",
        "state-muted",
        "state-warning",
        "state-bad",
        "battery-charging",
        "battery-discharging",
        "battery-idle"
    );

    if (className) {
        el.classList.add(
            className
        );
    }
}


function number(value, decimals, unit) {
    if (
        value === null ||
        value === undefined
    ) {
        return "--";
    }

    return (
        Number(value).toFixed(decimals)
        + unit
    );
}


function yesNo(value) {
    if (
        value === null ||
        value === undefined
    ) {
        return "--";
    }

    return value ? "YES" : "NO";
}


function renderMppts(chargers) {
    const host =
        document.getElementById(
            "mppt_rows"
        );

    host.replaceChildren();

    if (
        !Array.isArray(chargers) ||
        chargers.length === 0
    ) {
        const row =
            document.createElement(
                "div"
            );

        row.className = "mppt-row";

        row.textContent =
            "No solar controllers";

        host.appendChild(row);

        return;
    }

    for (const charger of chargers) {

        const row =
            document.createElement(
                "div"
            );

        row.className =
            "mppt-row";

        const values = [
            charger.name || "--",

            number(
                charger.power,
                0,
                ""
            ),

            number(
                charger.current,
                1,
                ""
            ),

            (
                charger.state === "OFF"
                && (
                    charger.power === null
                    || charger.power === undefined
                    || Number(charger.power) <= 1
                )
            )
                ? "--"
                : number(
                    charger.pv_voltage,
                    1,
                    ""
                ),

            charger.state || "--",
        ];

        values.forEach(
            (value, index) => {

                const cell =
                    document.createElement(
                        "span"
                    );

                cell.textContent =
                    value;

                if (index === 0) {
                    cell.className =
                        "mppt-name";
                }

                else if (index === 4) {
                    cell.className =
                        "mppt-state";
                }

                else {
                    cell.className =
                        "num";
                }

                row.appendChild(cell);
            }
        );

        host.appendChild(row);
    }
}


async function update() {
    try {
        const response =
            await fetch(
                "/api/status",
                {
                    cache: "no-store"
                }
            );

        const data =
            await response.json();


        setText(
            "timestamp",
            data.timestamp || "--"
        );


        const controller =
            document.getElementById(
                "controller"
            );

        controller.textContent =
            data.controller_running
                ? "● RUNNING"
                : "● STOPPED";

        controller.className =
            data.controller_running
                ? "good"
                : "bad";


        const victron =
            document.getElementById(
                "victron"
            );

        victron.textContent =
            data.victron_connected
                ? "● CONNECTED"
                : "● WAITING";

        victron.className =
            data.victron_connected
                ? "good"
                : "bad";


        setText(
            "daylight",
            data.daylight || "--"
        );

        setText(
            "peak_sun",
            data.peak_sun_hours == null
                ? "--"
                : Number(
                    data.peak_sun_hours
                ).toFixed(1) + "h/day"
        );


        setText(
            "soc",
            number(
                data.soc,
                1,
                "%"
            )
        );


        setText(
            "runtime",
            data.runtime || "--"
        );


        if (
            data.cutoff_soc !== null &&
            data.cutoff_soc !== undefined
        ) {
            setText(
                "eta-label",
                "ETA → "
                + Number(
                    data.cutoff_soc
                ).toFixed(0)
                + "%"
            );
        }


        setText(
            "solar_power",
            number(
                data.solar_power,
                0,
                "W"
            )
        );


        setText(
            "load_power",
            number(
                data.ac_output_power,
                0,
                "W"
            )
        );


        setText(
            "mode",
            data.mode || "--"
        );


        const batteryPower =
            number(
                data.battery_power,
                0,
                "W"
            );

        setText(
            "battery_power",
            batteryPower
        );

        setText(
            "battery_power2",
            batteryPower
        );


        setText(
            "battery_state",
            data.battery_state || "--"
        );

        if (
            data.battery_state
            === "CHARGING"
        ) {
            setStateClass(
                "battery_state",
                "battery-charging"
            );
        }

        else if (
            data.battery_state
            === "DISCHARGING"
        ) {
            setStateClass(
                "battery_state",
                "battery-discharging"
            );
        }

        else {
            setStateClass(
                "battery_state",
                "battery-idle"
            );
        }


        setText(
            "battery_voltage",
            number(
                data.battery_voltage,
                2,
                "V"
            )
        );


        setText(
            "battery_current",
            number(
                data.battery_current,
                1,
                "A"
            )
        );


        const gridImportPower =
            number(
                data.ac_input_power,
                0,
                "W"
            );

        const gridImportCurrent =
            number(
                data.ac_current,
                1,
                "A"
            );

        const gridImportText =
            data.ac_policy === "IGNORE AC"
                ? "DISABLED 0W"
                : (
                    "ACTIVE "
                    + gridImportPower
                    + " / "
                    + gridImportCurrent
                );

        setText(
            "grid_import_short",
            gridImportText
        );


        setText(
            "ac_present_short",
            data.ac_present
                ? "YES"
                : "NO"
        );

        setStateClass(
            "ac_present_short",
            data.ac_present
                ? "state-good"
                : "state-muted"
        );

        setStateClass(
            "grid_import_short",
            data.ac_policy === "IGNORE AC"
                ? "state-muted"
                : (
                    data.ac_input_power > 0
                        ? "state-good"
                        : "state-warning"
                )
        );


        setText(
            "solar_summary",
            number(
                data.solar_power,
                0,
                "W"
            )
            + " / "
            + number(
                data.solar_current,
                1,
                "A"
            )
        );


        setText(
            "solar_total_w",
            number(
                data.solar_power,
                0,
                ""
            )
        );


        setText(
            "solar_total_a",
            number(
                data.solar_current,
                1,
                ""
            )
        );


        renderMppts(
            data.solar_chargers
        );


        setText(
            "ac_present",
            yesNo(
                data.ac_present
            )
        );


        setText(
            "ac_connected",
            yesNo(
                data.ac_connected
            )
        );


        setText(
            "ac_policy",
            data.ac_policy || "--"
        );


        setText(
            "grid_charging",
            data.grid_charging
                ? "ENABLED"
                : "DISABLED"
        );


        setText(
            "ac_input",
            gridImportText
        );


        setText(
            "ac_output",
            number(
                data.ac_output_power,
                0,
                "W"
            )
        );


        const error =
            document.getElementById(
                "error"
            );

        if (
            data.error ||
            data.solar_error
        ) {
            error.style.display =
                "block";

            error.textContent =
                data.error
                || data.solar_error;
        }

        else {
            error.style.display =
                "none";

            error.textContent =
                "";
        }

    }

    catch (error) {
        const box =
            document.getElementById(
                "error"
            );

        box.style.display =
            "block";

        box.textContent =
            "Dashboard connection lost";
    }
}


update();

setInterval(
    update,
    1000
);
</script>


<script id="solar-fortress-history-live">

function historyDuration(value) {

    if (
        value === null
        || value === undefined
    ) {
        return "--";
    }

    const seconds =
        Number(value);

    if (!Number.isFinite(seconds)) {
        return "--";
    }

    const totalMinutes =
        Math.floor(
            seconds / 60
        );

    const days =
        Math.floor(
            totalMinutes / 1440
        );

    const hours =
        Math.floor(
            (totalMinutes % 1440)
            / 60
        );

    const minutes =
        totalMinutes % 60;

    if (days > 0) {
        return (
            days
            + "d "
            + hours
            + "h ago"
        );
    }

    if (hours > 0) {
        return (
            hours
            + "h "
            + minutes
            + "m ago"
        );
    }

    return (
        minutes
        + "m ago"
    );
}


function historyKwh(value) {

    if (
        value === null
        || value === undefined
    ) {
        return "--";
    }

    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "--";
    }

    return (
        number.toFixed(2)
        + " kWh"
    );
}


async function updateHistoryMetrics() {

    try {

        const response =
            await fetch(
                "/api/status",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();


        const full =
            document.getElementById(
                "last_full_charge"
            );

        const maxSoc =
            document.getElementById(
                "today_max_soc"
            );

        const lowSoc24h =
            document.getElementById(
                "low_soc_24h"
            );

        const today =
            document.getElementById(
                "solar_today_yield"
            );

        const seven =
            document.getElementById(
                "solar_seven_day_yield"
            );


        if (full) {
            full.textContent =
                historyDuration(
                    data.last_full_charge_seconds
                );
        }


        if (maxSoc) {

            const value =
                Number(
                    data.today_max_soc
                );

            maxSoc.textContent =
                Number.isFinite(value)
                    ? value.toFixed(1) + "%"
                    : "--";
        }


        if (lowSoc24h) {

            const value =
                Number(
                    data.low_soc_24h
                );

            lowSoc24h.textContent =
                Number.isFinite(value)
                    ? value.toFixed(1) + "%"
                    : "--";
        }


        if (today) {
            today.textContent =
                historyKwh(
                    data.solar_today_yield
                );
        }


        if (seven) {
            seven.textContent =
                historyKwh(
                    data.solar_seven_day_yield
                );
        }

    }

    catch (error) {
        console.error(
            "History metrics:",
            error
        );
    }
}


updateHistoryMetrics();

setInterval(
    updateHistoryMetrics,
    5000
);

</script>

</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == "/api/status":

            with state_lock:
                payload = json.dumps(
                    state
                ).encode()

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "application/json",
            )

            self.send_header(
                "Cache-Control",
                "no-store",
            )

            self.send_header(
                "Content-Length",
                str(len(payload)),
            )

            self.end_headers()

            self.wfile.write(
                payload
            )

            return


        if (
            self.path == "/"
            or self.path
            == "/index.html"
        ):

            payload = HTML.encode()

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8",
            )

            self.send_header(
                "Cache-Control",
                "no-store",
            )

            self.send_header(
                "Content-Length",
                str(len(payload)),
            )

            self.end_headers()

            self.wfile.write(
                payload
            )

            return


        self.send_error(404)


    def log_message(
        self,
        format,
        *args,
    ):
        pass


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():

    thread = threading.Thread(
        target=sample_loop,
        daemon=True,
    )

    thread.start()

    print(
        "Solar Fortress dashboard "
        f"listening on port {PORT}",
        flush=True,
    )

    server = Server(
        (HOST, PORT),
        Handler,
    )

    server.serve_forever()


if __name__ == "__main__":
    main()
