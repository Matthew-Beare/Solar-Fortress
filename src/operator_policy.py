"""Validated, non-actuating operator policy for Solar Fortress.

This module makes NO Victron writes and is deliberately not wired into the
existing Pi controller. Never infer a full SmartShunt charge from elapsed time.
"""
from __future__ import annotations

import copy
import datetime as dt
import math

DEFAULTS = {
    "soc_thresholds": {
        "grid_pass_through": 30.0,
        "grid_charge": 20.0,
        "battery_return": 35.0,
    },
    "qualification_seconds": {
        "pass_through": 60,
        "grid_charge": 60,
        "stop_grid_charge": 60,
    },
    "charge_estimate": {
        "battery_capacity_ah": None,
        "planning_dc_charge_amps": None,
        "finishing_allowance_minutes": 0,
        "learning_enabled": True,
        "learning_min_sessions": 2,
        "learning_history_sessions": 12,
        "learning_soc_match_percent": 5.0,
        "learning_conservative_multiplier": 1.1,
        "learning_max_gap_minutes": 20,
        "learning_sample_interval_seconds": 60,
        "learning_poll_seconds": 60,
        "learning_min_charge_current_amps": 1.0,
        "learning_taper_min_soc_percent": 95.0,
        "learning_taper_min_current_amps": -2.0,
        "learning_min_soc_gain_percent": 5.0,
    },
    "commander_full_charge": {
        "enabled": True,
        "default_hold_hours": 12,
        "max_hold_hours": 168,
        "override_expiry_hours": 168,
    },
    "maintenance": {
        "enabled": True,
        "full_charge_interval_days": 10,
        "request_repeat_days": 4,
        "post_full_hold_minutes": 60,
    },
    "storm": {
        "enabled": False,
        "lookahead_hours": 12,
        "watch_hours": 48,
        "confirmation_hours": 24,
        "charge_margin_hours": 4,
        "accumulation_hours": 24,
        "immediate_horizon_hours": 24,
        "check_interval_minutes": 15,
        "server_grace_seconds": 300,
        "manual_override_default_hours": 24,
        "manual_override_max_hours": 168,
        "post_storm_hold_hours": 12,
        "storm_event_join_hours": 12,
        "max_forecast_age_hours": 6,
        "forecast_latitude": None,
        "forecast_longitude": None,
        "nws_user_agent": None,
        "require_alert_clear_for_release": True,
        "target_soc": 100.0,
        "reserve_soc": 90.0,
        "snow": {"enabled": True, "threshold_inches": 6.0, "immediate_threshold_inches": None},
        "ice": {"enabled": True, "threshold_inches": 0.25, "immediate_threshold_inches": 0.5},
        "wind": {"enabled": False, "threshold_mph": None, "immediate_threshold_mph": None},
    },
}


def number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be numeric")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(name + " is outside its permitted bounds")
    return float(value)


def whole(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(name + " must be an integer within its bounds")
    return value


def validate_config(raw):
    """Return validated configuration, retaining unrelated existing keys."""
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be an object")
    out = copy.deepcopy(raw)
    soc = out.get("soc_thresholds", {})
    if not isinstance(soc, dict):
        raise ValueError("soc_thresholds must be an object")
    soc = {**DEFAULTS["soc_thresholds"], **soc}
    charge = number(soc["grid_charge"], "grid_charge", 5, 80)
    passthrough = number(soc["grid_pass_through"], "grid_pass_through", 10, 90)
    battery = number(soc["battery_return"], "battery_return", 10, 95)
    if not (charge < passthrough < battery):
        raise ValueError("Require grid_charge < grid_pass_through < battery_return")
    out["soc_thresholds"] = {**soc, "grid_charge": charge, "grid_pass_through": passthrough, "battery_return": battery}

    qualification = out.get("qualification_seconds", {})
    if not isinstance(qualification, dict):
        raise ValueError("qualification_seconds must be an object")
    qualification = {**DEFAULTS["qualification_seconds"], **qualification}
    for timer in ("pass_through", "grid_charge", "stop_grid_charge"):
        qualification[timer] = whole(qualification[timer], timer + " qualification", 1, 3600)
    out["qualification_seconds"] = qualification

    commander = out.get("commander_full_charge", {})
    if not isinstance(commander, dict):
        raise ValueError("commander_full_charge must be an object")
    commander = {**DEFAULTS["commander_full_charge"], **commander}
    # An old, staged completion deadline is obsolete. Keep the independently
    # expiring manual override, not a target time for reaching full charge.
    commander.pop("charge_attempt_timeout_hours", None)
    if not isinstance(commander["enabled"], bool):
        raise ValueError("Commander full-charge control must be boolean")
    for key, low, high in (
        ("default_hold_hours", 0, 168),
        ("max_hold_hours", 1, 168),
        ("override_expiry_hours", 1, 720),
    ):
        commander[key] = whole(commander[key], key, low, high)
    if commander["default_hold_hours"] > commander["max_hold_hours"]:
        raise ValueError("Default manual hold exceeds the permitted maximum")
    out["commander_full_charge"] = commander

    maintenance = out.get("maintenance", {})
    if not isinstance(maintenance, dict):
        raise ValueError("maintenance must be an object")
    maintenance = {**DEFAULTS["maintenance"], **maintenance}
    if not isinstance(maintenance["enabled"], bool):
        raise ValueError("maintenance.enabled must be a boolean")
    maintenance["full_charge_interval_days"] = whole(
        maintenance["full_charge_interval_days"], "full_charge_interval_days", 1, 90
    )
    maintenance["request_repeat_days"] = whole(
        maintenance["request_repeat_days"], "request_repeat_days", 1, 30
    )
    maintenance["post_full_hold_minutes"] = whole(
        maintenance["post_full_hold_minutes"], "post_full_hold_minutes", 0, 1440
    )
    out["maintenance"] = maintenance

    charge = out.get("charge_estimate", {})
    if not isinstance(charge, dict):
        raise ValueError("charge_estimate must be an object")
    charge = {**DEFAULTS["charge_estimate"], **charge}
    if charge["battery_capacity_ah"] is not None:
        charge["battery_capacity_ah"] = number(
            charge["battery_capacity_ah"], "battery_capacity_ah", 1, 20000
        )
    if charge["planning_dc_charge_amps"] is not None:
        charge["planning_dc_charge_amps"] = number(
            charge["planning_dc_charge_amps"], "planning_dc_charge_amps", 0.1, 1000
        )
    charge["finishing_allowance_minutes"] = whole(
        charge["finishing_allowance_minutes"], "finishing_allowance_minutes", 0, 1440
    )
    if not isinstance(charge["learning_enabled"], bool):
        raise ValueError("learning_enabled must be a boolean")
    for field, low, high in (
        ("learning_min_sessions", 1, 20),
        ("learning_history_sessions", 1, 100),
        ("learning_max_gap_minutes", 1, 120),
        ("learning_sample_interval_seconds", 10, 3600),
        ("learning_poll_seconds", 15, 900),
    ):
        charge[field] = whole(charge[field], field, low, high)
    for field, low, high in (
        ("learning_soc_match_percent", 0.5, 20.0),
        ("learning_conservative_multiplier", 1.0, 3.0),
        ("learning_min_charge_current_amps", 0.1, 100.0),
        ("learning_min_soc_gain_percent", 0.1, 50.0),
        ("learning_taper_min_soc_percent", 80.0, 100.0),
        ("learning_taper_min_current_amps", -50.0, 10.0),
    ):
        charge[field] = number(charge[field], field, low, high)
    if charge["learning_taper_min_current_amps"] > charge["learning_min_charge_current_amps"]:
        raise ValueError("Taper minimum current cannot exceed normal charging minimum")
    if charge["learning_min_sessions"] > charge["learning_history_sessions"]:
        raise ValueError("Learning minimum cannot exceed history sessions")
    out["charge_estimate"] = charge

    storm = out.get("storm", {})
    if not isinstance(storm, dict):
        raise ValueError("storm must be an object")
    storm = {**DEFAULTS["storm"], **storm}
    if not isinstance(storm["enabled"], bool):
        raise ValueError("storm.enabled must be boolean")
    storm["lookahead_hours"] = whole(storm["lookahead_hours"], "lookahead_hours", 1, 72)
    storm["watch_hours"] = whole(storm["watch_hours"], "watch_hours", 1, 120)
    storm["confirmation_hours"] = whole(storm["confirmation_hours"], "confirmation_hours", 1, 48)
    storm["charge_margin_hours"] = whole(storm["charge_margin_hours"], "charge_margin_hours", 0, 48)
    storm["immediate_horizon_hours"] = whole(storm["immediate_horizon_hours"], "immediate_horizon_hours", 1, 72)
    # Immediate risk checks operate even when the optional watch is shorter.
    # Historical confirmation is informational, NEVER a prerequisite to charge.
    # A storm first forecast with short warning can immediately merit action.
    storm["accumulation_hours"] = whole(storm["accumulation_hours"], "accumulation_hours", 1, 72)
    storm["check_interval_minutes"] = whole(storm["check_interval_minutes"], "check_interval_minutes", 5, 120)
    storm["server_grace_seconds"] = whole(storm["server_grace_seconds"], "server_grace_seconds", 15, 3600)
    storm["manual_override_default_hours"] = whole(storm["manual_override_default_hours"], "manual_override_default_hours", 1, 168)
    storm["manual_override_max_hours"] = whole(storm["manual_override_max_hours"], "manual_override_max_hours", 1, 168)
    if storm["manual_override_default_hours"] > storm["manual_override_max_hours"]:
        raise ValueError("Manual storm override default exceeds maximum")
    storm["post_storm_hold_hours"] = whole(storm["post_storm_hold_hours"], "post_storm_hold_hours", 0, 168)
    storm["storm_event_join_hours"] = whole(storm["storm_event_join_hours"], "storm_event_join_hours", 0, 72)
    storm["max_forecast_age_hours"] = whole(storm["max_forecast_age_hours"], "max_forecast_age_hours", 1, 24)
    if (storm["forecast_latitude"] is None) != (storm["forecast_longitude"] is None):
        raise ValueError("Set both forecast latitude and longitude, or neither")
    if storm["forecast_latitude"] is not None:
        storm["forecast_latitude"] = number(storm["forecast_latitude"], "forecast_latitude", -90, 90)
        storm["forecast_longitude"] = number(storm["forecast_longitude"], "forecast_longitude", -180, 180)
    agent = storm["nws_user_agent"]
    if agent is not None:
        if (not isinstance(agent, str) or not 10 <= len(agent) <= 160
                or any(ord(char) < 32 or ord(char) > 126 for char in agent)
                or ("@" not in agent and "http" not in agent)):
            raise ValueError("NWS User-Agent must identify the app and contact")
    if not isinstance(storm["require_alert_clear_for_release"], bool):
        raise ValueError("require_alert_clear_for_release must be a boolean")
    storm["target_soc"] = number(storm["target_soc"], "target_soc", 90, 100)
    storm["reserve_soc"] = number(storm["reserve_soc"], "reserve_soc", 50, 100)
    if storm["reserve_soc"] > storm["target_soc"]:
        raise ValueError("Storm reserve cannot exceed target")
    for kind, metric, upper in (
        ("snow", "threshold_inches", 72),
        ("ice", "threshold_inches", 6),
        ("wind", "threshold_mph", 200),
    ):
        item = storm[kind]
        if not isinstance(item, dict):
            raise ValueError(kind + " must be an object")
        item = {**DEFAULTS["storm"][kind], **item}
        if not isinstance(item["enabled"], bool):
            raise ValueError(kind + ".enabled must be boolean")
        threshold = item[metric]
        if kind == "wind" and not item["enabled"] and threshold is None:
            pass
        else:
            item[metric] = number(threshold, kind + "." + metric, 0.01, upper)
        severe_field = "immediate_threshold_mph" if kind == "wind" else "immediate_threshold_inches"
        severe = item[severe_field]
        if severe is not None:
            severe = number(severe, kind + "." + severe_field, 0.01, upper)
            if item["enabled"] and severe < item[metric]:
                raise ValueError(kind + " immediate threshold cannot be below ordinary threshold")
        item[severe_field] = severe
        storm[kind] = item
    out["storm"] = storm
    return out


def maintenance_request_due(now, last_full_charge, last_request, raw):
    """Request only based on actual full-charge and prior request timestamps.

    Unknown last_full_charge means 'unknown', not permission to grid charge.
    No storm, sunset, or reboot event resets these timestamps.
    """
    config = validate_config(raw)["maintenance"]
    if not config["enabled"]:
        return False
    if last_full_charge is None:
        return None
    if now.tzinfo is None or last_full_charge.tzinfo is None:
        raise ValueError("Timezone-aware times required")
    if last_full_charge > now:
        raise ValueError("Future last_full_charge timestamp")
    interval = dt.timedelta(days=config["full_charge_interval_days"])
    if now - last_full_charge < interval:
        return False
    if last_request is None:
        return True
    if last_request.tzinfo is None or last_request > now:
        raise ValueError("Invalid last_request timestamp")
    return now - last_request >= dt.timedelta(days=config["request_repeat_days"])


def _aware_time(value):
    """Accept NWS ISO timestamps or aware datetimes; reject naive inputs."""
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid forecast timestamp") from exc
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware forecast time required")
    return value


def storm_decision(now, forecast_periods, raw):
    """Advisory trigger: 12h lead and rolling 24h snowfall/ice totals.

    Find a precipitation forecast beginning (or underway) during the next
    lookahead_hours, then consider precipitation inside the following
    accumulation_hours. Wind is maximum gust during the lead horizon.
    Count partial NWS intervals in full: an intentionally conservative
    estimate, not an assertion that an interval's amount is evenly spread.

    This does NOT latch Storm Mode or command charging. Missing quantities
    are unknown. Production must verify NWS accumulation period semantics.
    """
    policy = validate_config(raw)["storm"]
    if not policy["enabled"]:
        return {"active": False, "reasons": [], "unknown": [], "enabled": False}
    now = _aware_time(now)
    lead_end = now + dt.timedelta(hours=policy["lookahead_hours"])
    accumulate = dt.timedelta(hours=policy["accumulation_hours"])
    analysis_end = lead_end + accumulate
    quantities = {"snow": [], "ice": [], "wind": []}
    lookup = (
        ("snow", "snow_inches"),
        ("ice", "ice_inches"),
        ("wind", "wind_mph"),
    )
    for period in forecast_periods:
        start = _aware_time(period["start"])
        end = _aware_time(period["end"])
        if start >= end:
            raise ValueError("Invalid forecast period")
        if start >= analysis_end or end <= now:
            continue
        for kind, key in lookup:
            if not policy[kind]["enabled"]:
                continue
            value = period.get(key)
            if value is None:
                continue
            amount = number(value, key, 0, 500)
            quantities[kind].append((start, end, amount))

    reasons, unknown = [], []
    for kind, metric in (
        ("snow", "threshold_inches"),
        ("ice", "threshold_inches"),
        ("wind", "threshold_mph"),
    ):
        if not policy[kind]["enabled"]:
            continue
        intervals = sorted(quantities[kind], key=lambda item: item[0])
        if not intervals:
            unknown.append(kind)
            continue
        if kind == "wind":
            gusts = [value for start, end, value in intervals
                     if start < lead_end and end > now]
            if not gusts:
                unknown.append(kind)
                continue
            observed = max(gusts)
        else:
            # A single forecast variable must have disjoint validity
            # periods; duplicates must not inflate a risk threshold.
            for previous, following in zip(intervals, intervals[1:]):
                if previous[1] > following[0]:
                    raise ValueError("Overlapping " + kind + " intervals")
            # Only a storm that is underway or starts within the advance
            # warning horizon can cause a request. Forecast accumulation
            # may then continue over a longer 24-hour assessment period.
            visible = [(start, end, value) for start, end, value in intervals
                       if start < lead_end and end > now]
            if not visible:
                unknown.append(kind)
                continue
            anchors = [max(now, start) for start, end, value in visible if value > 0]
            # A zero-precipitation forecast inside the lead window is not
            # evidence that precipitation starting beyond it has arrived.
            observed = max((
                sum(value for start, end, value in intervals
                    if start < anchor + accumulate and end > anchor)
                for anchor in anchors
            ), default=0.0)
        if observed >= policy[kind][metric]:
            reasons.append(kind)
    return {
        "active": bool(reasons),
        "reasons": reasons,
        "unknown": unknown,
        "enabled": True,
        "evaluated_at": now.isoformat(),
        "lookahead_hours": policy["lookahead_hours"],
        "accumulation_hours": policy["accumulation_hours"],
        "check_interval_minutes": policy["check_interval_minutes"],
    }


def estimated_charge_hours(current_soc, capacity_ah, actual_dc_charge_amps, target_soc=100):
    """Best-case bulk estimate, NOT a guaranteed deadline or MPPT grid limit.

    Real charging takes longer due to taper, AC input limits and concurrent loads.
    """
    soc = number(current_soc, "current_soc", 0, 100)
    target = number(target_soc, "target_soc", soc, 100)
    capacity = number(capacity_ah, "capacity_ah", 1, 100000)
    amps = number(actual_dc_charge_amps, "actual_dc_charge_amps", 0.01, 10000)
    return (target - soc) * capacity / (100.0 * amps)
