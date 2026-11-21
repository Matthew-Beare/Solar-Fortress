#!/usr/bin/env python3
"""Automatic read-only charge learning + storm-latch orchestration sidecar.

Polls the existing Pi status endpoint and reads latest normalized NWS files
produced by a trusted, separate forecaster. Re-evaluates battery readiness
each poll, persists learned sessions and storm latch, and emits a JSON mode
ADVISORY. It NEVER writes Victron D-Bus, HTTP control, or inverter settings.

No production service is installed by adding this file to Git. Deployment
requires inspection of the newer running Pi controller and hardware tests.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import time

from charge_learning import estimate_remaining
from charge_monitor import decode_status, fetch_local_status, load_config, observe_once
from operator_policy import _aware_time, validate_config
from nws_client import fetch_weather, write_atomic_json
from storm_latch import advance_storm_latch
from storm_watch import assess_storm_watch

DEFAULT_CONFIG = "/data/solar-fortress/config/controller.json"
DEFAULT_CHARGE_DB = "/data/solar-fortress/state/charge-learning.sqlite"
DEFAULT_LATCH_DB = "/data/solar-fortress/state/storm-latch.sqlite"
DEFAULT_FORECAST = "/data/solar-fortress/state/nws-forecast.json"
DEFAULT_ALERTS = "/data/solar-fortress/state/nws-alerts.json"
MAX_FILE_BYTES = 1024 * 1024
UTC = dt.timezone.utc


def _read_json(path, default):
    if path is None:
        return default
    source = Path(path)
    if source.is_symlink():
        raise ValueError("Refusing symbolic-link input")
    if not source.is_file():
        return default
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Oversized operator input JSON")
    return json.loads(source.read_text(encoding="utf-8"))


def _current_alerts_clear(alerts, now, max_age_hours):
    if not isinstance(alerts, dict) or "alerts_clear" not in alerts:
        return None
    issued = alerts.get("checked_at")
    if issued is None:
        return None
    age = (_aware_time(now) - _aware_time(issued)).total_seconds()
    if not 0 <= age <= max_age_hours * 3600:
        return None
    value = alerts["alerts_clear"]
    if not isinstance(value, bool):
        raise ValueError("alerts_clear must be a verified boolean")
    return value


def evaluate_cycle(when, pi_status, weather, archive, alerts, config, *,
                   learning_db, latch_db):
    """Make one automatic, durable state transition from current input data."""
    now = _aware_time(when)
    policy = validate_config(config)
    snapshot = decode_status(pi_status, now=now)
    observation = observe_once(learning_db, policy, pi_status, now=now)
    charge_settings = policy["charge_estimate"]
    eta = estimate_remaining(
        learning_db, snapshot["soc"], policy,
        target_soc=policy["storm"]["target_soc"],
        capacity_ah=charge_settings["battery_capacity_ah"],
        dc_charge_amps=charge_settings["planning_dc_charge_amps"],
    )
    if not isinstance(weather, dict) or not isinstance(weather.get("periods"), list):
        raise ValueError("Weather must be a normalized NWS forecast object")
    if not isinstance(archive, list):
        raise ValueError("NWS forecast archive must be a list")
    learned = (eta["hours"] if
               eta["source"] == "learned_smartshunt_full_sessions" else None)
    assessment = assess_storm_watch(
        now, weather["periods"], archive, policy,
        soc=snapshot["soc"],
        capacity_ah=charge_settings["battery_capacity_ah"],
        actual_dc_charge_amps=charge_settings["planning_dc_charge_amps"],
        forecast_source_updated_at=weather.get("source_update_time"),
        learned_charge_hours=learned, learning_source=eta["source"],
        learning_completed_sessions=eta["sessions"],
    )
    alert_clear = _current_alerts_clear(
        alerts, now, policy["storm"]["max_forecast_age_hours"]
    )
    latch = advance_storm_latch(
        latch_db, now, assessment, weather["periods"], policy,
        forecast_source_updated_at=weather.get("source_update_time"),
        smartshunt_full_at=snapshot["smartshunt_full_at"],
        soc=snapshot["soc"], ac_available=snapshot["ac_available"],
        alerts_clear=alert_clear,
    )
    return {
        "evaluated_at": now.isoformat(),
        "telemetry_at": snapshot["when"].isoformat(),
        "full_charge_estimate": eta,
        "learning_observation": observation,
        "weather_assessment": assessment,
        "persistent_storm_latch": latch,
        "actuation_permitted": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--learning-db", default=DEFAULT_CHARGE_DB)
    parser.add_argument("--latch-db", default=DEFAULT_LATCH_DB)
    parser.add_argument("--forecast-file", default=DEFAULT_FORECAST)
    parser.add_argument("--fetch-nws", action="store_true", help="Fetch NWS using authenticated GUI location")
    parser.add_argument("--archive-file")
    parser.add_argument("--alerts-file", default=DEFAULT_ALERTS)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    last_nws_attempt = None
    while True:
        delay = 60
        try:
            config = load_config(args.config)
            delay = config["charge_estimate"]["learning_poll_seconds"]
            now = dt.datetime.now(UTC)
            # Weather is refreshed separately (15-minute default) while the
            # learner samples the battery every minute. An NWS outage must
            # never prevent the persistent latch from being reevaluated.
            refresh = config["storm"]["check_interval_minutes"] * 60
            if args.fetch_nws and (last_nws_attempt is None or
                                   now.timestamp() - last_nws_attempt >= refresh):
                last_nws_attempt = now.timestamp()
                try:
                    latitude = config["storm"]["forecast_latitude"]
                    longitude = config["storm"]["forecast_longitude"]
                    latest_forecast, latest_alerts = fetch_weather(latitude, longitude, now)
                    write_atomic_json(args.forecast_file, latest_forecast)
                    write_atomic_json(args.alerts_file, latest_alerts)
                except (ValueError, TypeError, OSError, KeyError) as weather_error:
                    print("NWS refresh unavailable: " + str(weather_error),
                          file=sys.stderr, flush=True)
                    # Reuse any previously saved inputs. Staleness rules on
                    # the planner and latch prevent unsafe activation/release.
            result = evaluate_cycle(
                now, fetch_local_status(),
                _read_json(args.forecast_file, {"periods": [],
                                                "source_update_time": None}),
                _read_json(args.archive_file, []),
                _read_json(args.alerts_file, {}),
                config, learning_db=args.learning_db, latch_db=args.latch_db
            )
            print(json.dumps(result), flush=True)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            print("Storm operator cycle failed: " + str(exc),
                  file=sys.stderr, flush=True)
            if not args.loop:
                raise SystemExit(1) from exc
        if not args.loop:
            return
        time.sleep(delay)


if __name__ == "__main__":
    main()
