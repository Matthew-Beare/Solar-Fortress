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
    "maintenance": {
        "full_charge_interval_days": 10,
        "request_repeat_days": 4,
    },
    "storm": {
        "enabled": False,
        "lookahead_hours": 12,
        "accumulation_hours": 24,
        "check_interval_minutes": 15,
        "target_soc": 100.0,
        "reserve_soc": 90.0,
        "snow": {"enabled": True, "threshold_inches": 6.0},
        "ice": {"enabled": True, "threshold_inches": 0.25},
        "wind": {"enabled": False, "threshold_mph": None},
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

    maintenance = out.get("maintenance", {})
    if not isinstance(maintenance, dict):
        raise ValueError("maintenance must be an object")
    maintenance = {**DEFAULTS["maintenance"], **maintenance}
    maintenance["full_charge_interval_days"] = whole(
        maintenance["full_charge_interval_days"], "full_charge_interval_days", 1, 90
    )
    maintenance["request_repeat_days"] = whole(
        maintenance["request_repeat_days"], "request_repeat_days", 1, 30
    )
    out["maintenance"] = maintenance

    storm = out.get("storm", {})
    if not isinstance(storm, dict):
        raise ValueError("storm must be an object")
    storm = {**DEFAULTS["storm"], **storm}
    if not isinstance(storm["enabled"], bool):
        raise ValueError("storm.enabled must be boolean")
    storm["lookahead_hours"] = whole(storm["lookahead_hours"], "lookahead_hours", 1, 72)
    storm["accumulation_hours"] = whole(storm["accumulation_hours"], "accumulation_hours", 1, 72)
    storm["check_interval_minutes"] = whole(storm["check_interval_minutes"], "check_interval_minutes", 5, 120)
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
        storm[kind] = item
    out["storm"] = storm
    return out


def maintenance_request_due(now, last_full_charge, last_request, raw):
    """Request only based on actual full-charge and prior request timestamps.

    Unknown last_full_charge means 'unknown', not permission to grid charge.
    No storm, sunset, or reboot event resets these timestamps.
    """
    config = validate_config(raw)["maintenance"]
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
