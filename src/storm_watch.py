"""Read-only 48-hour storm watch with 24-hour forecast-history confirmation.

Electrical controls are NEVER written by this module. The Pi remains
authoritative. If estimated charge time consumes the remaining warning,
recommend charging even without historical confirmation.
"""
from __future__ import annotations

import datetime as dt

from operator_policy import _aware_time, estimated_charge_hours, number, validate_config


def qualifying_events(now, periods, config):
    """Qualifying storm hazards and their forecast onset.

    Amounts belong to the NWS forecast validity interval. Partial intervals
    count in full as a conservative approximation, not an hourly forecast.
    """
    now = _aware_time(now)
    policy = validate_config(config)["storm"]
    if not policy["enabled"]:
        return []
    watch_end = now + dt.timedelta(hours=policy["watch_hours"])
    analysis_end = watch_end + dt.timedelta(hours=policy["accumulation_hours"])
    rows = {"snow": [], "ice": [], "wind": []}
    specs = (
        ("snow", "snow_inches", "threshold_inches"),
        ("ice", "ice_inches", "threshold_inches"),
        ("wind", "wind_mph", "threshold_mph"),
    )
    for raw in periods:
        start = _aware_time(raw["start"])
        end = _aware_time(raw["end"])
        if start >= end:
            raise ValueError("Invalid forecast interval")
        if start >= analysis_end or end <= now:
            continue
        for kind, field, threshold_name in specs:
            if not policy[kind]["enabled"] or raw.get(field) is None:
                continue
            rows[kind].append((start, end, number(raw[field], field, 0, 500)))

    events = []
    for kind, field, threshold_name in specs:
        if not policy[kind]["enabled"]:
            continue
        items = sorted(rows[kind], key=lambda value: value[0])
        if kind != "wind":
            for previous, next_item in zip(items, items[1:]):
                if previous[1] > next_item[0]:
                    raise ValueError("Overlapping " + kind + " intervals")
        for start, end, value in items:
            if end <= now or start >= watch_end or value <= 0:
                continue
            onset = max(start, now)
            if kind == "wind":
                severity = value
            else:
                cutoff = onset + dt.timedelta(hours=policy["accumulation_hours"])
                severity = sum(amount for a, b, amount in items
                               if a < cutoff and b > onset)
            if severity >= policy[kind][threshold_name]:
                events.append({
                    "kind": kind,
                    "onset": onset.isoformat(),
                    "forecast_amount": round(severity, 5),
                    "threshold": policy[kind][threshold_name],
                })
    return sorted(events, key=lambda value: (_aware_time(value["onset"]), value["kind"]))


def historical_matches(now, events, snapshots, policy, current_source_updated_at):
    """Match same hazard and near-identical onset in a forecast ~24h older.

    Cached 15-minute requests are NOT equivalent to 24h of confirmation.
    History must be persisted across restarts by the future server scheduler.
    """
    # An HTTP cache hit or repeated reading of the exact same official
    # NWS grid is not independent forecast confirmation.
    if current_source_updated_at is None:
        return []
    current_source = _aware_time(current_source_updated_at)
    if current_source > now or now - current_source > dt.timedelta(hours=6):
        return []
    delay = dt.timedelta(hours=policy["confirmation_hours"])
    limit = delay + dt.timedelta(hours=12)
    matches = []
    for old in snapshots:
        observed = _aware_time(old["checked_at"])
        age = now - observed
        if not delay <= age <= limit:
            continue
        old_source = old.get("source_update_time")
        if old_source is None:
            continue
        old_source = _aware_time(old_source)
        if old_source >= current_source or old_source > observed:
            continue
        old_events = qualifying_events(
            observed, old["periods"], {"storm": {**policy, "enabled": True}}
        )
        for event in events:
            for previous in old_events:
                if event["kind"] != previous["kind"]:
                    continue
                offset = abs((_aware_time(event["onset"]) -
                              _aware_time(previous["onset"])).total_seconds())
                if offset <= 6 * 3600:
                    matches.append({
                        "kind": event["kind"],
                        "prior_checked_at": observed.isoformat(),
                        "prior_event_onset": previous["onset"],
                    })
                    break
    return matches


def assess_storm_watch(now, forecast_periods, archived_snapshots, config, *,
                       soc, capacity_ah, actual_dc_charge_amps,
                       forecast_source_updated_at=None):
    """Assess, do not actuate, an impending forecast weather event."""
    now = _aware_time(now)
    policy = validate_config(config)["storm"]
    if not policy["enabled"]:
        return {"stage": "disabled", "charge_recommended": False,
                "reasons": [], "confirmed_by_history": False}
    if not isinstance(archived_snapshots, list):
        raise ValueError("Expected a list of prior forecast snapshots")
    events = qualifying_events(now, forecast_periods, config)
    if not events:
        return {
            "stage": "clear", "charge_recommended": False, "reasons": [],
            "confirmed_by_history": False,
            "note": "An empty risk forecast does not release a Pi storm latch.",
        }
    onset = _aware_time(events[0]["onset"])
    time_left = max(0.0, (onset - now).total_seconds() / 3600)
    matches = historical_matches(
        now, events, archived_snapshots, policy, forecast_source_updated_at
    )
    confirmed = bool(matches)
    reasons = sorted({event["kind"] for event in events})
    if soc is None or capacity_ah is None or actual_dc_charge_amps is None:
        return {
            "stage": "needs_fresh_telemetry", "charge_recommended": False,
            "reasons": reasons, "confirmed_by_history": confirmed,
            "hours_until_onset": round(time_left, 2),
            "note": "Cannot estimate battery readiness without fresh measurements.",
        }
    current_soc = number(soc, "soc", 0, 100)
    if current_soc >= policy["target_soc"]:
        return {
            "stage": "ready", "charge_recommended": False,
            "reasons": reasons, "confirmed_by_history": confirmed,
            "hours_until_onset": round(time_left, 2),
        }
    bulk_hours = estimated_charge_hours(
        current_soc, capacity_ah, actual_dc_charge_amps, policy["target_soc"]
    )
    budget_hours = bulk_hours + policy["charge_margin_hours"]
    deadline = time_left <= budget_hours
    charge = confirmed or deadline
    return {
        "stage": "charge_due" if charge else "watch",
        "charge_recommended": charge,
        "confirmed_by_history": confirmed,
        "deadline_override": bool(deadline and not confirmed),
        "reasons": reasons,
        "event_onset": onset.isoformat(),
        "hours_until_onset": round(time_left, 2),
        "estimated_bulk_charge_hours": round(bulk_hours, 2),
        "charge_planning_hours": round(budget_hours, 2),
        "history_matches": matches,
        "note": "Advisory only: no remote control or storm latch implemented.",
    }
