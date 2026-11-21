"""Pi-local Storm Mode supervisor for the SERVER-OWNED planner architecture.

The Pi has NO internet requirement. VM102 owns forecast, charge learning and
high-rate history. The Pi accepts only a trusted, fresh server plan; a missing
or expired plan returns to the EXISTING normal SOC policy after a configurable
grace period. Commander Boomer can explicitly override it locally.

This module is non-actuating. The deployed Pi controller must still apply
local inverter, BMS, AC-loss and SOC safety constraints before changing modes.

No periodic SD-card writes: only an explicit manual-mode change is persisted.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import tempfile

from operator_policy import _aware_time, whole

UTC = dt.timezone.utc
MANUAL_MODES = frozenset({
    "automatic", "normal", "storm_charge", "storm_pass_through",
})
SERVER_MODES = frozenset({"normal", "storm_charge", "storm_pass_through"})
DEFAULT_SERVER_GRACE_SECONDS = 300


def validate_timeout(value):
    return whole(value, "server grace seconds", 15, 3600)


def _seconds(raw):
    return _aware_time(raw).astimezone(UTC).timestamp()


def read_manual_mode(path):
    """Missing local override is automatic. Corrupt existing state fails safe."""
    file = Path(path)
    if not file.exists():
        return "automatic"
    if file.is_symlink() or not file.is_file():
        raise ValueError("Invalid manual storm override path")
    payload = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("mode") not in MANUAL_MODES:
        raise ValueError("Invalid local manual storm override")
    return payload["mode"]


def set_manual_mode(path, mode, *, confirmed):
    """Only a confirmed local user interaction may change this setting."""
    if mode not in MANUAL_MODES or not isinstance(mode, str):
        raise ValueError("Invalid requested manual mode")
    if confirmed is not True:
        raise ValueError("Manual storm change requires explicit confirmation")
    target = Path(path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("Invalid local manual override path")
    if not target.parent.is_dir():
        raise ValueError("Local override state directory not found")
    if target.exists() and read_manual_mode(target) == mode:
        return False  # Avoid needless SD/flash writes on unchanged choices.
    descriptor, temp = tempfile.mkstemp(prefix=".storm-manual-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump({"mode": mode}, file, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def evaluate_local_mode(now, *, server_plan=None, server_plan_authenticated=False,
                        grace_seconds=DEFAULT_SERVER_GRACE_SECONDS,
                        manual_mode="automatic"):
    """Return the Pi's requested mode ADVISORY only.

    The plan is valid only if a separate transport has authenticated VM102,
    its schema is exact, and the plan is freshly issued with an expiry.
    No internet requests, SQLite history, or background weather collection.
    """
    clock = _seconds(now)
    validate_timeout(grace_seconds)
    if manual_mode not in MANUAL_MODES:
        raise ValueError("Unknown manual mode")
    if manual_mode != "automatic":
        return {
            "requested_mode": None if manual_mode == "normal" else manual_mode,
            "source": "commander_local_override",
            "server_available": None,
            "reason": "Locally confirmed manual override; normal means default SOC policy.",
            "actuation_permitted": False,
        }
    if server_plan_authenticated is not True or not isinstance(server_plan, dict):
        return {
            "requested_mode": None, "source": "normal_fallback",
            "server_available": False,
            "reason": "No authenticated server plan; use existing Pi SOC policy.",
            "actuation_permitted": False,
        }
    if set(server_plan) != {"version", "issued_at", "expires_at", "mode"}:
        raise ValueError("Unexpected server plan fields")
    if server_plan["version"] != 1 or server_plan["mode"] not in SERVER_MODES:
        raise ValueError("Unsupported server plan version or requested mode")
    issued, expires = _seconds(server_plan["issued_at"]), _seconds(server_plan["expires_at"])
    if issued > clock + 30 or expires <= issued or expires - issued > grace_seconds:
        raise ValueError("Invalid server plan timing")
    if clock > expires:
        return {
            "requested_mode": None, "source": "normal_fallback",
            "server_available": False,
            "reason": "Server heartbeat expired; restore existing Pi SOC policy.",
            "actuation_permitted": False,
        }
    return {
        "requested_mode": None if server_plan["mode"] == "normal"
        else server_plan["mode"],
        "source": "trusted_server_plan",
        "server_available": True,
        "reason": "Fresh, authenticated server recommendation (still subject to Pi safeguards).",
        "actuation_permitted": False,
    }
