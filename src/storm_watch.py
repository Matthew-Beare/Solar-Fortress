"""Read-only weather-trigger and charging-deadline advisor.

Every evaluation uses the newest *fresh* NWS issue, the latest battery SOC,
and a vetted attainable DC charging rate. None of the historical snapshots,
the optional advance watch horizon, or the confirmation interval is required
for a severe-weather trigger. No Victron write or storm latch occurs here.
"""
from __future__ import annotations

import datetime as dt

from operator_policy import _aware_time, estimated_charge_hours, number, validate_config

SOURCE_MAX_AGE = dt.timedelta(hours=6)


def _period_rows(now, periods, search_end, policy):
    """Normalize one forecast grid variable per hazard without double-counting."""
    rows = {"snow": [], "ice": [], "wind": []}
    columns = (("snow", "snow_inches"), ("ice", "ice_inches"), ("wind", "wind_mph"))
    for item in periods:
        start, end = _aware_time(item["start"]), _aware_time(item["end"])
        if end <= start:
            raise ValueError("Invalid forecast interval")
        if end <= now or start >= search_end:
            continue
        for kind, field in columns:
            if not policy[kind]["enabled"] or item.get(field) is None:
                continue
            rows[kind].append((start, end, number(item[field], field, 0, 500)))
    for kind in ("snow", "ice"):
        intervals = sorted(rows[kind], key=lambda i: (i[0], i[1]))
        for left, right in zip(intervals, intervals[1:]):
            if left[1] > right[0]:
                raise ValueError("Overlapping " + kind + " forecast intervals")
        rows[kind] = intervals
    return rows


def qualifying_events(now, periods, config):
    """Find ordinary threshold events in a configurable watch horizon.

    A storm starting within watch_hours can accumulate over the following
    accumulation_hours. A partial NWS validity interval is counted in full
    conservatively. The forecast is not proof the weather will occur.
    """
    now = _aware_time(now)
    policy = validate_config(config)["storm"]
    if not policy["enabled"]:
        return []
    watch_end = now + dt.timedelta(hours=policy["watch_hours"])
    analysis_end = watch_end + dt.timedelta(hours=policy["accumulation_hours"])
    rows = _period_rows(now, periods, analysis_end, policy)
    events = []
    for kind, key in (("snow", "threshold_inches"),
                      ("ice", "threshold_inches"),
                      ("wind", "threshold_mph")):
        if not policy[kind]["enabled"]:
            continue
        items = rows[kind]
        for start, end, value in items:
            if value <= 0 or end <= now or start >= watch_end:
                continue
            onset = max(start, now)
            if kind == "wind":
                severity = value
            else:
                cutoff = onset + dt.timedelta(hours=policy["accumulation_hours"])
                severity = sum(amount for a, b, amount in items
                               if a < cutoff and b > onset)
            if severity >= policy[kind][key]:
                events.append({
                    "kind": kind,
                    "onset": onset.isoformat(),
                    "forecast_amount": round(severity, 5),
                    "threshold": policy[kind][key],
                })
    return sorted(events, key=lambda e: (_aware_time(e["onset"]), e["kind"]))


def severe_now_events(now, periods, config):
    """Override for genuinely severe conditions inside the next N hours.

    This does NOT depend on watch_hours or a 24-hour historical confirmation.
    Ice defaults to >=0.5 inch forecast in the upcoming 24-hour period.
    Optional severe snow/wind triggers can be individually configured.
    """
    now = _aware_time(now)
    policy = validate_config(config)["storm"]
    if not policy["enabled"]:
        return []
    horizon = now + dt.timedelta(hours=policy["immediate_horizon_hours"])
    rows = _period_rows(now, periods, horizon, policy)
    severe = []
    for kind, metric in (("snow", "immediate_threshold_inches"),
                         ("ice", "immediate_threshold_inches"),
                         ("wind", "immediate_threshold_mph")):
        threshold = policy[kind][metric]
        if not policy[kind]["enabled"] or threshold is None:
            continue
        items = [(start, end, value) for start, end, value in rows[kind]
                 if start < horizon and end > now]
        if not items:
            continue
        amount = (max(value for start, end, value in items)
                  if kind == "wind" else sum(value for start, end, value in items))
        if amount >= threshold:
            first_positive = min((max(start, now) for start, end, value in items
                                  if value > 0), default=None)
            if first_positive is not None:
                severe.append({
                    "kind": kind, "onset": first_positive.isoformat(),
                    "forecast_amount": round(amount, 5), "threshold": threshold,
                    "horizon_hours": policy["immediate_horizon_hours"],
                })
    return sorted(severe, key=lambda e: (_aware_time(e["onset"]), e["kind"]))


def historical_matches(now, events, snapshots, policy, current_source_updated_at):
    """Historical trend only. It NEVER vetoes a fresh severe or deadline trigger."""
    if current_source_updated_at is None:
        return []
    current_issue = _aware_time(current_source_updated_at)
    if current_issue > now or now - current_issue > SOURCE_MAX_AGE:
        return []
    minimum_age = dt.timedelta(hours=policy["confirmation_hours"])
    maximum_age = minimum_age + dt.timedelta(hours=12)
    matches = []
    for snapshot in snapshots:
        checked = _aware_time(snapshot["checked_at"])
        age = now - checked
        if not minimum_age <= age <= maximum_age:
            continue
        old_source = snapshot.get("source_update_time")
        if old_source is None:
            continue
        old_issue = _aware_time(old_source)
        if old_issue >= current_issue or old_issue > checked:
            continue
        earlier = qualifying_events(
            checked, snapshot["periods"], {"storm": {**policy, "enabled": True}}
        )
        for new_event in events:
            for old_event in earlier:
                delta = abs((_aware_time(new_event["onset"]) -
                             _aware_time(old_event["onset"])).total_seconds())
                if new_event["kind"] == old_event["kind"] and delta <= 6 * 3600:
                    matches.append({
                        "kind": new_event["kind"],
                        "prior_checked_at": checked.isoformat(),
                        "prior_event_onset": old_event["onset"],
                    })
                    break
    return matches


def assess_storm_watch(now, forecast_periods, archived_snapshots, config, *,
                       soc, capacity_ah, actual_dc_charge_amps,
                       forecast_source_updated_at=None):
    """Recalculate an advisory charge decision on every fresh forecast poll.

    actual_dc_charge_amps is a VERIFIED attainable positive charging rate, not
    the instant battery current (which may be zero before the charger starts).
    Capacity, effective current, availability of AC and absorption taper
    require calibration before automatic electrical operation.
    """
    now = _aware_time(now)
    policy = validate_config(config)["storm"]
    if not policy["enabled"]:
        return {"stage": "disabled", "charge_recommended": False,
                "reasons": [], "confirmed_by_history": False}
    if not isinstance(archived_snapshots, list):
        raise ValueError("Expected a list of historic forecast snapshots")
    if forecast_source_updated_at is None:
        return {"stage": "stale_forecast", "charge_recommended": False,
                "reasons": [], "confirmed_by_history": False,
                "note": "Official NWS update time unavailable; do not trust a cached forecast."}
    source_time = _aware_time(forecast_source_updated_at)
    if source_time > now or now - source_time > SOURCE_MAX_AGE:
        return {"stage": "stale_forecast", "charge_recommended": False,
                "reasons": [], "confirmed_by_history": False,
                "note": "NWS data stale or timestamp from the future."}

    ordinary = qualifying_events(now, forecast_periods, config)
    severe = severe_now_events(now, forecast_periods, config)
    if not ordinary and not severe:
        return {
            "stage": "clear", "charge_recommended": False, "reasons": [],
            "confirmed_by_history": False, "severe_immediate": False,
            "note": "No current qualifying hazard. A Pi storm latch must not be cleared by a forecast alone.",
        }
    # Severe can bypass an absent/short ordinary watch completely.
    events = ordinary + severe
    onset = min(_aware_time(item["onset"]) for item in events)
    time_left = max(0.0, (onset - now).total_seconds() / 3600)
    matches = historical_matches(now, ordinary, archived_snapshots,
                                 policy, source_time)
    confirmed = bool(matches)
    reasons = sorted({item["kind"] for item in events})

    if soc is None or capacity_ah is None or actual_dc_charge_amps is None:
        return {
            "stage": "needs_fresh_telemetry", "charge_recommended": False,
            "reasons": reasons, "confirmed_by_history": confirmed,
            "severe_immediate": bool(severe),
            "hours_until_onset": round(time_left, 2),
            "note": "Severe conditions still require verified battery and charger inputs.",
        }
    current_soc = number(soc, "soc", 0, 100)
    if current_soc >= policy["target_soc"]:
        return {
            "stage": "ready", "charge_recommended": False,
            "reasons": reasons, "confirmed_by_history": confirmed,
            "severe_immediate": bool(severe),
            "hours_until_onset": round(time_left, 2),
        }
    bulk_hours = estimated_charge_hours(
        current_soc, capacity_ah, actual_dc_charge_amps, policy["target_soc"]
    )
    budget_hours = bulk_hours + policy["charge_margin_hours"]
    deadline = time_left <= budget_hours

    # No history prerequisite. If the latest forecast indicates severe
    # conditions, the charge recommendation is immediate.
    charge = bool(severe) or confirmed or deadline
    if severe:
        reason = "severe_forecast"
    elif deadline:
        reason = "charging_deadline"
    elif confirmed:
        reason = "history_corroborated"
    else:
        reason = "watching"
    return {
        "stage": "charge_due" if charge else "watch",
        "charge_recommended": charge,
        "trigger": reason,
        "confirmed_by_history": confirmed,
        "deadline_override": bool(deadline and not confirmed),
        "severe_immediate": bool(severe),
        "severe_events": severe,
        "reasons": reasons,
        "event_onset": onset.isoformat(),
        "hours_until_onset": round(time_left, 2),
        "estimated_bulk_charge_hours": round(bulk_hours, 2),
        "charge_planning_hours": round(budget_hours, 2),
        "history_matches": matches,
        "note": "Read-only advisory: no Pi command or storm latch implemented.",
    }
