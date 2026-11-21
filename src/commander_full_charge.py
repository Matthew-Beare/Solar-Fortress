"""Commander Boomer's local, no-login request-full-charge state machine.

Pure advisory. The Pi retains physical authority. Request is a one-shot
manual intervention with (a) an absolute charging-attempt deadline, (b) a
hold duration beginning only at a NEW SmartShunt full-charge event, and
(c) a final expiration. No weather/Proxmox/internet is required.

The state file changes only on request, completion, timeout, or cancellation.
Reads are safe after reboot. Repeated status polls do not write the card.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import tempfile

from operator_policy import _aware_time, validate_config, whole

UTC = dt.timezone.utc


def _time(value):
    return _aware_time(value).astimezone(UTC)


def _iso(value):
    return _time(value).isoformat()


def _write(path, state):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unexpected Commander request state path")
    if not path.parent.is_dir():
        raise ValueError("Commander state directory does not exist")
    fd, tmp = tempfile.mkstemp(prefix=".commander-charge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(state, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load(path):
    file = Path(path)
    if not file.exists():
        return None
    if file.is_symlink() or not file.is_file():
        raise ValueError("Invalid Commander request state path")
    state = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("phase") not in (
        "charging", "holding_full", "completed", "expired", "cancelled"
    ):
        raise ValueError("Corrupt Commander request state")
    return state


def begin_full_charge(path, now, hold_hours, config, *, confirmed):
    """Record an explicit request, replacing any prior Commander request."""
    settings = validate_config(config)["commander_full_charge"]
    if not settings["enabled"]:
        raise ValueError("Commander full-charge requests disabled by administrator")
    if confirmed is not True:
        raise ValueError("Full-charge request requires confirmation")
    hold = whole(hold_hours, "requested hold hours", 0, settings["max_hold_hours"])
    start = _time(now)
    deadline = start + dt.timedelta(hours=settings["charge_attempt_timeout_hours"])
    state = {
        "version": 1, "phase": "charging",
        "requested_at": _iso(start),
        "charge_attempt_expires_at": _iso(deadline),
        "hold_hours": hold,
        "smartshunt_full_at": None,
        "hold_expires_at": None,
        "ended_at": None,
    }
    _write(path, state)
    return describe_full_charge(path, start)


def cancel_full_charge(path, now, *, confirmed):
    if confirmed is not True:
        raise ValueError("Cancelling requires confirmation")
    state = _load(path)
    if state is None or state["phase"] in ("completed", "expired", "cancelled"):
        return {"phase": "idle", "request_mode": None}
    state["phase"] = "cancelled"
    state["ended_at"] = _iso(now)
    _write(path, state)
    return {"phase": "cancelled", "request_mode": None}


def describe_full_charge(path, now):
    """Read status without writes. Expired requests never actuate anything."""
    state = _load(path)
    if state is None:
        return {"phase": "idle", "request_mode": None}
    phase = state["phase"]
    clock = _time(now)
    if phase == "charging" and clock >= _time(state["charge_attempt_expires_at"]):
        phase = "expired"
    if phase == "holding_full" and clock >= _time(state["hold_expires_at"]):
        phase = "completed"
    mode = ("grid_charging" if phase == "charging" else
            "full_charge_hold" if phase == "holding_full" else None)
    return {
        "phase": phase,
        "request_mode": mode,
        "requested_at": state["requested_at"],
        "charge_attempt_expires_at": state["charge_attempt_expires_at"],
        "hold_hours": state["hold_hours"],
        "smartshunt_full_at": state["smartshunt_full_at"],
        "hold_expires_at": state["hold_expires_at"],
        "actuation_permitted": False,
    }


def update_from_smartshunt(path, now, smartshunt_full_at):
    """Persist actual full completion. Never start the timer from button press.

    A stale last-full timestamp from before the request does not qualify.
    An absent full event will expire the attempt at its fixed deadline.
    """
    state = _load(path)
    if state is None or state["phase"] not in ("charging", "holding_full"):
        return describe_full_charge(path, now)
    clock = _time(now)
    if state["phase"] == "charging":
        deadline = _time(state["charge_attempt_expires_at"])
        if clock >= deadline:
            state["phase"] = "expired"
            state["ended_at"] = _iso(clock)
            _write(path, state)
            return describe_full_charge(path, clock)
        if smartshunt_full_at is not None:
            full = _time(smartshunt_full_at)
            if _time(state["requested_at"]) < full <= clock:
                expires = full + dt.timedelta(hours=state["hold_hours"])
                state["smartshunt_full_at"] = _iso(full)
                state["hold_expires_at"] = _iso(expires)
                if clock >= expires:
                    state["phase"] = "completed"
                    state["ended_at"] = _iso(clock)
                else:
                    state["phase"] = "holding_full"
                _write(path, state)
    if state["phase"] == "holding_full" and clock >= _time(state["hold_expires_at"]):
        state["phase"] = "completed"
        state["ended_at"] = _iso(clock)
        _write(path, state)
    return describe_full_charge(path, clock)
