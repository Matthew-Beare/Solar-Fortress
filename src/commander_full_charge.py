"""Commander Boomer's local, no-login request-full-charge state machine.

Pure advisory. The Pi retains physical authority. Request is a one-shot
manual intervention that requests charging IMMEDIATELY, independent of
weather/Proxmox/internet. No artificial target time to reach full exists.
Every request carries a failsafe absolute expiry so stale manual commands
cannot persist forever. When SmartShunt confirms a NEW full-charge event,
the operator-selected hold begins and the expiry becomes full + hold.

The state file changes only on request, completion, expiry, or cancellation.
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


def begin_full_charge(path, now, hold_hours, config, *, confirmed, replace_existing=False):
    """Record an explicit request, replacing any prior Commander request."""
    settings = validate_config(config)["commander_full_charge"]
    if not settings["enabled"]:
        raise ValueError("Commander full-charge requests disabled by administrator")
    if confirmed is not True:
        raise ValueError("Full-charge request requires confirmation")
    hold = whole(hold_hours, "requested hold hours", 0, settings["max_hold_hours"])
    start = _time(now)
    existing = describe_full_charge(path, start)
    if (existing["phase"] in ("charging", "holding_full")
            and replace_existing is not True):
        raise ValueError("A full-charge request is already active. Cancel it or explicitly replace it.")
    override_expiry = start + dt.timedelta(hours=settings["override_expiry_hours"])
    state = {
        "version": 1, "phase": "charging",
        "requested_at": _iso(start),
        "override_expires_at": _iso(override_expiry),
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
    # Unknown/old staged requests have no valid lease and cannot resume.
    expires_raw = state.get("override_expires_at")
    if phase in ("charging", "holding_full"):
        if expires_raw is None or clock >= _time(expires_raw):
            phase = "completed" if phase == "holding_full" else "expired"
    if phase == "holding_full" and state.get("hold_expires_at") is not None:
        if clock >= _time(state["hold_expires_at"]):
            phase = "completed"
    mode = ("grid_charging" if phase == "charging" else
            "full_charge_hold" if phase == "holding_full" else None)
    return {
        "phase": phase,
        "request_mode": mode,
        "requested_at": state["requested_at"],
        "override_expires_at": state.get("override_expires_at"),
        "hold_hours": state["hold_hours"],
        "smartshunt_full_at": state["smartshunt_full_at"],
        "hold_expires_at": state["hold_expires_at"],
        "actuation_permitted": False,
    }


def update_from_smartshunt(path, now, smartshunt_full_at):
    """Persist actual full completion. Never start the timer from button press.

    A stale last-full timestamp from before the request does not qualify.
    An uncompleted request has no artificial target-to-full deadline; it
    remains a local charge request until its explicit failsafe lease expires.
    """
    state = _load(path)
    if state is None or state["phase"] not in ("charging", "holding_full"):
        return describe_full_charge(path, now)
    clock = _time(now)
    if state["phase"] == "charging":
        lease_expiry_raw = state.get("override_expires_at")
        if lease_expiry_raw is None or clock >= _time(lease_expiry_raw):
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
                state["override_expires_at"] = _iso(expires)
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
