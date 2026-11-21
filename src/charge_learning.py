"""Persistent, read-only charge-time learning from completed SmartShunt sessions.

Call record_observation on fresh Pi /api/status updates (e.g. every minute).
An observation never changes inverter settings. A session is learned ONLY
when a newly observed SmartShunt full-charge timestamp closes a continuous
grid-charging session. SOC at 100% alone NEVER completes a learning session.

The prediction incorporates the observed absorption/taper period up to the
actual SmartShunt full event, and is deliberately conservative: use the
slowest matching recent completed session with an operator-adjustable
multiplier; compare against the capacity/current floor when known.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import sqlite3

from operator_policy import _aware_time, estimated_charge_hours, number, validate_config

UTC = dt.timezone.utc


def _timestamp(raw):
    return _aware_time(raw).astimezone(UTC).timestamp()


def _open(path):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unexpected charge learning database path")
    if not path.parent.is_dir():
        raise ValueError("Charge learning database directory does not exist")
    fresh = not path.exists()
    connection = sqlite3.connect(path, timeout=10)
    try:
        if fresh:
            os.chmod(path, 0o600)
        elif path.stat().st_mode & 0o002:
            raise ValueError("Refusing world-writable history database")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS active_session ("
            "id INTEGER PRIMARY KEY CHECK(id=1), start_ts REAL NOT NULL,"
            "last_ts REAL NOT NULL, last_sample_ts REAL NOT NULL,"
            "start_soc REAL NOT NULL, last_soc REAL NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS active_samples ("
            "sample_ts REAL PRIMARY KEY, soc REAL NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS completed_sessions ("
            "id INTEGER PRIMARY KEY, full_ts REAL UNIQUE NOT NULL,"
            "start_ts REAL NOT NULL, first_soc REAL NOT NULL,"
            "samples_json TEXT NOT NULL)"
        )
        connection.commit()
        return connection
    except Exception:
        connection.close()
        raise


def _clear_active(db):
    db.execute("DELETE FROM active_session")
    db.execute("DELETE FROM active_samples")


def record_observation(db_path, when, *, soc, grid_charging,
                       battery_current_amps, smartshunt_full_at=None, config=None):
    """Persist a new observation, returning a status and completed-session count.

    Duplicate or older samples cannot create sessions. An existing full event
    is tolerated but is never counted twice. A long telemetry gap, charger
    interruption or clear battery discharge aborts incomplete training.
    """
    charge = validate_config(config or {})["charge_estimate"]
    if not charge["learning_enabled"]:
        return {"status": "learning_disabled", "completed": False}
    if not isinstance(grid_charging, bool):
        raise ValueError("grid_charging must be boolean")
    ts = _timestamp(when)
    soc = number(soc, "observed_soc", 0, 100)
    amps = number(battery_current_amps, "battery_current_amps", -10000, 10000)
    full_ts = None if smartshunt_full_at is None else _timestamp(smartshunt_full_at)
    if full_ts is not None and full_ts > ts:
        raise ValueError("SmartShunt full event timestamp is in the future")
    gap_seconds = charge["learning_max_gap_minutes"] * 60.0
    with _open(db_path) as db:
        with db:
            active = db.execute(
                "SELECT start_ts,last_ts,last_sample_ts,start_soc,last_soc "
                "FROM active_session WHERE id=1"
            ).fetchone()
            if active and full_ts is not None and full_ts >= active[0]:
                # The full event must be near the last live charging sample.
                # A stale historical SmartShunt value cannot close a new run.
                near = abs(full_ts - active[1]) <= gap_seconds
                samples = db.execute(
                    "SELECT sample_ts,soc FROM active_samples ORDER BY sample_ts"
                ).fetchall()
                near_full = samples and max(s[1] for s in samples) >= 95.0
                progress = soc - active[3]
                if (near and near_full and len(samples) >= 2 and
                        progress >= charge["learning_min_soc_gain_percent"]):
                    duplicate = db.execute(
                        "SELECT id FROM completed_sessions "
                        "WHERE ABS(full_ts-?) <= 90 LIMIT 1", (full_ts,)
                    ).fetchone()
                    if not duplicate:
                        db.execute(
                            "INSERT INTO completed_sessions "
                            "(full_ts,start_ts,first_soc,samples_json) VALUES(?,?,?,?)",
                            (full_ts, active[0], active[3],
                             json.dumps(samples, separators=(",", ":")))
                        )
                        # Limit permanent retention and prevent unbounded data.
                        db.execute(
                            "DELETE FROM completed_sessions WHERE id NOT IN "
                            "(SELECT id FROM completed_sessions ORDER BY full_ts DESC "
                            "LIMIT ?)", (charge["learning_history_sessions"],)
                        )
                    _clear_active(db)
                    return {
                        "status": "completed" if not duplicate else "duplicate_full_event",
                        "completed": not bool(duplicate),
                        "smartshunt_full_at": dt.datetime.fromtimestamp(
                            full_ts, UTC).isoformat(),
                    }
            # The SmartShunt's net charge current can approach zero as the
            # charger tapers near full. Preserve an existing session through
            # this phase rather than throwing away exactly the data needed
            # to learn its true finishing time.
            normal_current = amps >= charge["learning_min_charge_current_amps"]
            taper_current = (
                active is not None and
                soc >= charge["learning_taper_min_soc_percent"] and
                amps >= charge["learning_taper_min_current_amps"]
            )
            ongoing = grid_charging and (normal_current or taper_current)
            if not ongoing:
                _clear_active(db)
                return {"status": "not_grid_charging", "completed": False}
            if active and ts <= active[1]:
                return {"status": "duplicate_or_old_observation", "completed": False}
            if active and (ts - active[1] > gap_seconds or
                           ts - active[0] > 72 * 3600 or
                           soc < active[4] - 5):
                _clear_active(db)
                active = None
            if active is None:
                _clear_active(db)
                db.execute(
                    "INSERT INTO active_session "
                    "(id,start_ts,last_ts,last_sample_ts,start_soc,last_soc) "
                    "VALUES(1,?,?,?,?,?)", (ts, ts, ts, soc, soc)
                )
                db.execute("INSERT INTO active_samples VALUES(?,?)", (ts, soc))
                return {"status": "session_started", "completed": False}
            add_sample = ts - active[2] >= charge["learning_sample_interval_seconds"]
            db.execute(
                "UPDATE active_session SET last_ts=?, last_soc=?, "
                "last_sample_ts=? WHERE id=1",
                (ts, soc, ts if add_sample else active[2])
            )
            if add_sample:
                db.execute(
                    "INSERT OR REPLACE INTO active_samples VALUES(?,?)", (ts, soc)
                )
            return {"status": "charging_sample", "completed": False}


def estimate_remaining(db_path, current_soc, config, *,
                       capacity_ah=None, dc_charge_amps=None, target_soc=100):
    """Return learned or fallback time-to-full, including charge taper.

    A learned result requires independent completed SmartShunt sessions
    with SOC observations at/just below the current SOC. If history is
    inadequate, derive an explicitly labelled bulk-only fallback. A manual
    finishing allowance applies to fallback, not learned taper time.
    """
    settings = validate_config(config)["charge_estimate"]
    soc = number(current_soc, "current_soc", 0, 100)
    target = number(target_soc, "target_soc", 90, 100)
    if soc >= target:
        return {"hours": 0.0, "source": "already_at_target", "sessions": 0}
    if capacity_ah is None:
        capacity_ah = settings["battery_capacity_ah"]
    if dc_charge_amps is None:
        dc_charge_amps = settings["planning_dc_charge_amps"]
    elif settings["planning_dc_charge_amps"] is not None:
        dc_charge_amps = min(
            number(dc_charge_amps, "dc_charge_amps", .01, 10000),
            settings["planning_dc_charge_amps"]
        )
    fallback = None
    if capacity_ah is not None and dc_charge_amps is not None:
        fallback = (estimated_charge_hours(soc, capacity_ah, dc_charge_amps, target) +
                    settings["finishing_allowance_minutes"] / 60)
    samples_by_session = []
    if settings["learning_enabled"] and target == 100 and Path(db_path).is_file():
        with _open(db_path) as db:
            rows = db.execute(
                "SELECT full_ts,samples_json FROM completed_sessions "
                "ORDER BY full_ts DESC LIMIT ?",
                (settings["learning_history_sessions"],)
            ).fetchall()
        for completed_at, raw in rows:
            samples = json.loads(raw)
            # Use earliest matching (low SOC) point for a conservative
            # remaining-time estimate within the configured SOC tolerance.
            eligible = [
                completed_at - float(stamp) for stamp, prior_soc in samples
                if prior_soc <= soc and
                soc - prior_soc <= settings["learning_soc_match_percent"] and
                completed_at >= float(stamp)
            ]
            if eligible:
                samples_by_session.append(max(eligible) / 3600)
    if len(samples_by_session) >= settings["learning_min_sessions"]:
        learned = max(samples_by_session) * settings["learning_conservative_multiplier"]
        result = max(learned, fallback) if fallback is not None else learned
        return {
            "hours": round(result, 4),
            "source": "learned_smartshunt_full_sessions",
            "sessions": len(samples_by_session),
            "observed_worst_hours": round(max(samples_by_session), 4),
            "bulk_floor_hours": round(fallback, 4) if fallback is not None else None,
        }
    return {
        "hours": round(fallback, 4) if fallback is not None else None,
        "source": "untrained_capacity_current_fallback" if fallback is not None else "unknown",
        "sessions": len(samples_by_session),
    }


def training_status(db_path, config=None):
    settings = validate_config(config or {})["charge_estimate"]
    if not Path(db_path).is_file():
        return {"completed_sessions": 0, "session_active": False,
                "learning_enabled": settings["learning_enabled"]}
    with _open(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM completed_sessions").fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM active_session").fetchone()[0]
    return {"completed_sessions": count, "session_active": bool(active),
            "learning_enabled": settings["learning_enabled"]}
