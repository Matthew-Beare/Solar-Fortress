#!/usr/bin/env python3
"""Loopback-only SmartShunt charging-history observer (NO inverter writes).

This sidecar polls the already deployed Pi status endpoint each configured
minute, records continuous grid charge sessions, and learns actual time to
the SmartShunt full-charge event. It does not start/stop a charger, enable
Storm Mode, serve HTTP or change the existing dashboard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sys
import time
import urllib.request

from charge_learning import record_observation, training_status
from operator_policy import _aware_time, number, validate_config

STATUS_URL = "http://127.0.0.1:8080/api/status"
DEFAULT_CONFIG = "/data/solar-fortress/config/controller.json"
DEFAULT_DB = "/data/solar-fortress/state/charge-learning.sqlite"
MAX_JSON = 256 * 1024
UTC = dt.timezone.utc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing a redirect from local telemetry")


def parse_pi_time(value):
    """Existing Pi exposes ISO or an Eastern EST/EDT timestamp."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Invalid Pi timestamp")
        return dt.datetime.fromtimestamp(value, UTC)
    if not isinstance(value, str):
        raise ValueError("Missing Pi timestamp")
    try:
        return _aware_time(value).astimezone(UTC)
    except ValueError:
        tokens = value.rsplit(" ", 1)
        offset = {"EST": -5, "EDT": -4}.get(tokens[-1])
        if offset is None:
            raise ValueError("Unsupported Pi timestamp format") from None
        stamp = dt.datetime.strptime(tokens[0], "%Y-%m-%d %I:%M:%S %p")
        return stamp.replace(tzinfo=dt.timezone(dt.timedelta(hours=offset))
                             ).astimezone(UTC)


def decode_status(payload, now=None, max_age_seconds=120):
    if not isinstance(payload, dict) or payload.get("victron_connected") is not True:
        raise ValueError("Victron status disconnected or malformed")
    now = now or dt.datetime.now(UTC)
    now = _aware_time(now)
    source_at = parse_pi_time(payload.get("timestamp"))
    age = (now - source_at).total_seconds()
    if not -30 <= age <= max_age_seconds:
        raise ValueError("Pi telemetry source is stale or future dated")
    if not isinstance(payload.get("grid_charging"), bool):
        raise ValueError("Missing grid charging state")
    soc = number(payload.get("soc"), "soc", 0, 100)
    battery_amps = number(payload.get("battery_current"), "battery_current", -10000, 10000)
    ac_present = payload.get("ac_present")
    if ac_present is not None and not isinstance(ac_present, bool):
        raise ValueError("Invalid AC availability")
    if ac_present is False and payload["grid_charging"]:
        # Do not treat impossible AC/charger status as a trusted training sample.
        raise ValueError("Inconsistent AC availability and grid charger state")
    full_time = None
    full_age = payload.get("last_full_charge_seconds")
    if full_age is not None:
        seconds = number(full_age, "last_full_charge_seconds", 0, 10**10)
        full_time = source_at - dt.timedelta(seconds=seconds)
    return {
        "when": source_at,
        "soc": soc,
        "grid_charging": payload["grid_charging"],
        "battery_current_amps": battery_amps,
        "smartshunt_full_at": full_time,
        "ac_available": ac_present,
    }


def fetch_local_status():
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    request = urllib.request.Request(
        STATUS_URL, headers={"Accept": "application/json"},
        method="GET"
    )
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise ValueError("Pi status did not return HTTP 200")
        raw = response.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Pi status exceeds allowed size")
    return json.loads(raw.decode("utf-8"))


def observe_once(db_path, config, payload, now=None):
    status = decode_status(payload, now=now)
    result = record_observation(
        db_path, status["when"], soc=status["soc"],
        grid_charging=status["grid_charging"],
        battery_current_amps=status["battery_current_amps"],
        smartshunt_full_at=status["smartshunt_full_at"], config=config,
    )
    return {**result, "training": training_status(db_path, config),
            "telemetry_at": status["when"].isoformat()}


def load_config(path):
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Missing or unexpected Pi configuration")
    return validate_config(json.loads(source.read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--status-file", help="Offline test input only; no network request")
    args = parser.parse_args()
    while True:
        delay = 60
        try:
            cfg = load_config(args.config)
            delay = cfg["charge_estimate"]["learning_poll_seconds"]
            payload = (json.loads(Path(args.status_file).read_text(encoding="utf-8"))
                       if args.status_file else fetch_local_status())
            result = observe_once(args.database, cfg, payload)
            print(json.dumps(result), flush=True)
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print("Charge learning observation failed: " + str(exc),
                  file=sys.stderr, flush=True)
            if not args.loop:
                raise SystemExit(1) from exc
        if not args.loop:
            return
        time.sleep(delay)


if __name__ == "__main__":
    main()
