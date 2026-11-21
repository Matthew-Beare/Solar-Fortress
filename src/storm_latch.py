"""Persistent Storm Mode latch with charge-then-pass-through recommendations.

ADVISORY only: NEVER accesses D-Bus or changes an inverter.
The Pi must enforce local manual override, AC, BMS and electrical safety.
Forecasts can extend the saved last-accumulation time but cannot shorten it.
No weather or alert response NEVER means safe to release.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3

from operator_policy import _aware_time, number, validate_config

UTC = dt.timezone.utc


def _epoch(value):
    return _aware_time(value).astimezone(UTC).timestamp()


@contextmanager
def _db(path):
    target = Path(path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("Invalid Storm Mode state path")
    if not target.parent.is_dir():
        raise ValueError("Storm Mode state directory is missing")
    fresh = not target.exists()
    db = sqlite3.connect(target, timeout=10)
    try:
        if fresh:
            os.chmod(target, 0o600)
        elif target.stat().st_mode & 0o002:
            raise ValueError("Refusing world-writable Storm Mode state")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS storm_latch ("
            "id INTEGER PRIMARY KEY CHECK(id=1), active INTEGER NOT NULL,"
            "activated_at REAL NOT NULL, onset REAL NOT NULL,"
            "last_accumulation_end REAL NOT NULL, last_full_event REAL)"
        )
        db.commit()
        yield db
    finally:
        db.close()


def _fmt(ts):
    return dt.datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None


def _positive_weather(periods, policy):
    result, winds = [], []
    for period in periods:
        start, end = _epoch(period["start"]), _epoch(period["end"])
        if end <= start:
            raise ValueError("Invalid weather period")
        for kind, field in (("ice", "ice_inches"), ("snow", "snow_inches")):
            if not policy[kind]["enabled"] or period.get(field) is None:
                continue
            if number(period[field], field, 0, 500) > 0:
                result.append((start, end))
        if policy["wind"]["enabled"] and period.get("wind_mph") is not None:
            if number(period["wind_mph"], "wind_mph", 0, 500) > 0:
                winds.append((start, end))
    return sorted(result if result else winds)


def _storm_end(periods, policy, onset, previous_end=None):
    """Find last forecast precipitation linked to the currently latched event.

    Start with intervals near the detected storm. Then extend across
    additional precipitation periods separated by at most the configurable
    event-join gap; never shorten the previous ending timestamp.
    """
    join = policy["storm_event_join_hours"] * 3600
    intervals = _positive_weather(periods, policy)
    if previous_end is None:
        relevant = [
            (a, b) for a, b in intervals
            if b >= onset and a <= onset + policy["accumulation_hours"] * 3600
        ]
        end = min((b for a, b in relevant), default=onset)
    else:
        end = previous_end
    extending = True
    while extending:
        extending = False
        for start, finish in intervals:
            if (start <= end + join and finish > end and
                    (previous_end is not None or start >= onset - join)):
                end = finish
                extending = True
    return end


def _fresh(now, issued, policy):
    if issued is None:
        return False
    age = now - _epoch(issued)
    return 0 <= age <= policy["max_forecast_age_hours"] * 3600


def get_latch_state(db_path):
    """Read state after restart without modifying it."""
    if not Path(db_path).is_file():
        return {"active": False}
    with _db(db_path) as db:
        row = db.execute(
            "SELECT active,activated_at,onset,last_accumulation_end,last_full_event "
            "FROM storm_latch WHERE id=1"
        ).fetchone()
    if not row or not row[0]:
        return {"active": False}
    return {
        "active": True, "activated_at": _fmt(row[1]),
        "event_onset": _fmt(row[2]),
        "last_accumulation_end": _fmt(row[3]),
        "last_full_event": _fmt(row[4]),
    }


def advance_storm_latch(
    db_path, now, assessment, forecast_periods, config, *,
    forecast_source_updated_at=None, smartshunt_full_at=None,
    soc=None, ac_available=None, alerts_clear=None
):
    """Persist one decision; return a mode recommendation, not a command.

    alerts_clear is a verified external bool, NOT inferred from an empty
    precipitation forecast. Unknown alerts prevent release by default.
    """
    validated = validate_config(config)
    policy = validated["storm"]
    now_ts = _epoch(now)
    if not isinstance(assessment, dict):
        raise ValueError("Assessment must be an object")
    if soc is not None:
        soc = number(soc, "soc", 0, 100)
    if ac_available is not None and not isinstance(ac_available, bool):
        raise ValueError("ac_available must be boolean or unknown")
    if alerts_clear is not None and not isinstance(alerts_clear, bool):
        raise ValueError("alerts_clear must be boolean or unknown")
    full_ts = None if smartshunt_full_at is None else _epoch(smartshunt_full_at)
    if full_ts is not None and full_ts > now_ts:
        raise ValueError("Future SmartShunt full event")
    fresh = _fresh(now_ts, forecast_source_updated_at, policy)
    with _db(db_path) as db:
        with db:
            row = db.execute(
                "SELECT active,activated_at,onset,last_accumulation_end,last_full_event "
                "FROM storm_latch WHERE id=1"
            ).fetchone()
            active = bool(row and row[0])
            if not policy["enabled"]:
                if active:
                    db.execute("UPDATE storm_latch SET active=0 WHERE id=1")
                return {
                    "active": False, "phase": "disabled", "requested_mode": None,
                    "note": "Explicit master disable; local Pi remains authoritative.",
                }
            if not active:
                activate = (
                    fresh and (
                        assessment.get("stage") == "charge_due"
                        or assessment.get("severe_immediate") is True
                        or (assessment.get("stage") == "ready" and
                            assessment.get("hours_until_onset", 10000) <=
                            policy["lookahead_hours"])
                    )
                )
                if not activate:
                    return {"active": False, "phase": "unlatched",
                            "requested_mode": None,
                            "note": "Watch only; no latched storm."}
                onset = _epoch(assessment["event_onset"])
                end = _storm_end(forecast_periods, policy, onset)
                row = (1, now_ts, onset, end, None)
                db.execute(
                    "INSERT OR REPLACE INTO storm_latch "
                    "(id,active,activated_at,onset,last_accumulation_end,last_full_event)"
                    "VALUES(1,?,?,?,?,?)", row
                )
                active = True

            _, activated, onset, previous_end, stored_full = row
            end = previous_end
            if fresh and forecast_periods:
                end = _storm_end(forecast_periods, policy, onset, previous_end)
            if full_ts is not None and full_ts >= activated:
                if stored_full is None or full_ts > stored_full:
                    stored_full = full_ts
            db.execute(
                "UPDATE storm_latch SET last_accumulation_end=?,last_full_event=? "
                "WHERE id=1", (end, stored_full)
            )
            release_after = end + policy["post_storm_hold_hours"] * 3600
            eligible_for_release = (
                fresh and now_ts >= release_after and
                (alerts_clear is True or
                 (not policy["require_alert_clear_for_release"] and
                  alerts_clear is not False))
            )
            if eligible_for_release:
                db.execute("UPDATE storm_latch SET active=0 WHERE id=1")
                return {
                    "active": False, "phase": "released",
                    "requested_mode": None, "release_after": _fmt(release_after),
                    "note": "Storm end, configurable hold and verified weather clearance satisfied.",
                }

            # Once onset arrives, persist pass-through through the storm and
            # recovery period. Before onset, allow grid charging to the
            # SmartShunt full event and optional maintenance balancing hold.
            if now_ts >= onset:
                phase, mode = "storm_pass_through", "pass_through"
            elif soc is not None and soc >= policy["target_soc"] and stored_full is None:
                phase, mode = "at_target_full_event_unknown", "pass_through"
            elif stored_full is not None:
                hold = (validated["maintenance"]["post_full_hold_minutes"]
                        if validated["maintenance"]["enabled"] else 0)
                if now_ts < stored_full + 60 * hold:
                    phase, mode = "post_full_balancing_hold", "grid_charging"
                else:
                    phase, mode = "prepared_pass_through", "pass_through"
            else:
                phase, mode = "prestorm_charge", "grid_charging"
            return {
                "active": True,
                "phase": phase,
                "requested_mode": mode,
                "actuation_permitted_by_this_module": False,
                "ac_available": ac_available,
                "source_fresh": fresh,
                "activated_at": _fmt(activated),
                "event_onset": _fmt(onset),
                "last_accumulation_end": _fmt(end),
                "release_after": _fmt(release_after),
                "last_full_event": _fmt(stored_full),
                "note": ("Storm latch held during stale NWS data."
                         if not fresh else "Storm latch active."),
            }
