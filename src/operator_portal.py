#!/usr/bin/env python3
"""Loopback-only, password-protected settings editor. NOT an actuator.

The controller currently loads its policy on startup. This tool does not restart
the controller, enable storm charging or write to D-Bus.
"""
from __future__ import annotations

import argparse
import base64
import copy
import getpass
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import urllib.parse

from operator_policy import validate_config

DEFAULT_CONFIG = "/data/solar-fortress/config/controller.json"
DEFAULT_AUTH = "/data/solar-fortress/config/operator-auth.json"
LOCK = threading.RLock()
MAX_BODY = 8192


def atomic_json(path, obj, mode=0o600):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unexpected output file")
    if not path.parent.is_dir():
        raise ValueError("Destination directory does not exist")
    original = path.stat() if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=".solar-operator-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if original is not None:
                # Preserve controller/dashboard access, not force 0600 on a
                # pre-existing production config with a different owner.
                import stat
                original_mode = stat.S_IMODE(original.st_mode)
                if original_mode & 0o002:
                    raise ValueError("Refusing world-writable production configuration")
                os.fchmod(stream.fileno(), original_mode)
                if os.geteuid() == 0:
                    os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            else:
                os.fchmod(stream.fileno(), mode)
            json.dump(obj, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unexpected configuration file")
    return json.loads(path.read_text(encoding="utf-8"))


def password_record(password):
    if not isinstance(password, str) or not 14 <= len(password) <= 256:
        raise ValueError("Administrator password must contain 14 to 256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return {
        "salt": base64.b64encode(salt).decode(),
        "digest": base64.b64encode(digest).decode(),
        "csrf": secrets.token_urlsafe(32),
    }


def verify_password(password, record):
    try:
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["digest"], validate=True)
        if len(salt) != 16 or len(expected) != 64:
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


def parse_settings(form, original):
    """Constrain every editable field; retain unknown live-controller keys."""
    merged = copy.deepcopy(original)
    merged["soc_thresholds"] = {
        **merged.get("soc_thresholds", {}),
        "grid_charge": float(form["grid_charge"][0]),
        "grid_pass_through": float(form["grid_pass_through"][0]),
        "battery_return": float(form["battery_return"][0]),
    }
    merged["qualification_seconds"] = {
        **merged.get("qualification_seconds", {}),
        "pass_through": int(form["pass_through_qualification_seconds"][0]),
        "grid_charge": int(form["grid_charge_qualification_seconds"][0]),
        "stop_grid_charge": int(form["stop_grid_charge_qualification_seconds"][0]),
    }
    merged["maintenance"] = {
        **merged.get("maintenance", {}),
        "full_charge_interval_days": int(form["full_charge_interval_days"][0]),
        "request_repeat_days": int(form["request_repeat_days"][0]),
        "post_full_hold_minutes": int(form["post_full_hold_minutes"][0]),
    }
    current = validate_config(original)["storm"]
    storm = {**current}
    storm["enabled"] = "storm_enabled" in form
    storm["lookahead_hours"] = int(form["lookahead_hours"][0])
    storm["watch_hours"] = int(form["watch_hours"][0])
    storm["confirmation_hours"] = int(form["confirmation_hours"][0])
    storm["charge_margin_hours"] = int(form["charge_margin_hours"][0])
    storm["accumulation_hours"] = int(form["accumulation_hours"][0])
    storm["check_interval_minutes"] = int(form["check_interval_minutes"][0])
    storm["immediate_horizon_hours"] = int(form["immediate_horizon_hours"][0])
    storm["target_soc"] = float(form["target_soc"][0])
    storm["reserve_soc"] = float(form["reserve_soc"][0])
    for kind, field in (
        ("snow", "threshold_inches"),
        ("ice", "threshold_inches"),
        ("wind", "threshold_mph"),
    ):
        block = dict(storm[kind])
        block["enabled"] = kind + "_enabled" in form
        raw = form[kind + "_threshold"][0].strip()
        block[field] = float(raw) if raw else None
        severe_key = "immediate_threshold_mph" if kind == "wind" else "immediate_threshold_inches"
        severe_raw = form[kind + "_immediate_threshold"][0].strip()
        block[severe_key] = float(severe_raw) if severe_raw else None
        storm[kind] = block
    merged["storm"] = storm
    return validate_config(merged)


def make_handler(config_path, auth_path):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, message, mime="text/html; charset=utf-8", extra=None):
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic ") or len(header) > 4096:
                return False
            try:
                candidate = base64.b64decode(header[6:], validate=True).decode("utf-8")
                username, colon, password = candidate.partition(":")
                return colon == ":" and username == "admin" and verify_password(password, load_json(auth_path))
            except (ValueError, UnicodeError, OSError):
                return False

        def require_login(self):
            if self.authorized():
                return True
            self.reply(401, "<h1>Solar Fortress administration requires a password</h1>",
                       extra={"WWW-Authenticate": 'Basic realm="Solar Fortress settings", charset="UTF-8"'})
            return False

        def do_GET(self):
            if self.path != "/":
                return self.reply(404, "Not found", "text/plain")
            if not self.require_login():
                return
            try:
                settings = validate_config(load_json(config_path))
                token = load_json(auth_path)["csrf"]
            except (ValueError, OSError, KeyError) as exc:
                return self.reply(500, "<h1>Settings unavailable</h1><p>" + html.escape(str(exc)) + "</p>")
            def field(name, value, typ="number", step="1"):
                return ('<label>' + html.escape(name.replace("_", " ").title()) +
                        ' <input name="' + html.escape(name) + '" type="' + typ +
                        '" step="' + step + '" value="' + html.escape(str(value)) +
                        '" required></label><p></p>')
            def optional_field(name, value, step="0.01"):
                text_value = "" if value is None else str(value)
                return ('<label>' + html.escape(name.replace("_", " ").title()) +
                        ' <input name="' + html.escape(name) + '" type="number"' +
                        ' step="' + step + '" value="' + html.escape(text_value) +
                        '"></label><p></p>')
            def check(name, enabled):
                return ('<label><input type="checkbox" name="' + name + '"' +
                        (' checked' if enabled else '') + '> ' + name.replace("_", " ").title() +
                        '</label><p></p>')
            soc, qual, maint, storm = (settings["soc_thresholds"], settings["qualification_seconds"],
                                      settings["maintenance"], settings["storm"])
            page = ['<!doctype html><meta charset="utf-8"><title>Solar Fortress settings</title>',
                    '<style>body{font:1rem system-ui;max-width:40rem;margin:2rem auto;padding:1rem}input{padding:.35rem}button{padding:.6rem 1rem}fieldset{margin:1rem 0}</style>',
                    '<h1>Solar Fortress operator settings</h1>',
                    '<p>Administrator only. Saving does not restart the controller or activate unintegrated storm/maintenance features.</p>',
                    '<form method="post" action="/settings"><input type="hidden" name="csrf" value="' + html.escape(token) + '">',
                    '<fieldset><legend>Battery thresholds, SOC %</legend>',
                    field("grid_charge", soc["grid_charge"], step="0.1"),
                    field("grid_pass_through", soc["grid_pass_through"], step="0.1"),
                    field("battery_return", soc["battery_return"], step="0.1"), '</fieldset>',
                    '<fieldset><legend>SOC change qualifications (seconds)</legend>',
                    field("pass_through_qualification_seconds", qual["pass_through"]),
                    field("grid_charge_qualification_seconds", qual["grid_charge"]),
                    field("stop_grid_charge_qualification_seconds", qual["stop_grid_charge"]), '</fieldset>',
                    '<fieldset><legend>Maintenance request cadence</legend>',
                    field("full_charge_interval_days", maint["full_charge_interval_days"]),
                    field("request_repeat_days", maint["request_repeat_days"]),
                    field("post_full_hold_minutes", maint["post_full_hold_minutes"]), '</fieldset>',
                    '<fieldset><legend>Storm Mode (staged, not actuating)</legend>',
                    check("storm_enabled", storm["enabled"]),
                    field("lookahead_hours", storm["lookahead_hours"]),
                    field("watch_hours", storm["watch_hours"]),
                    field("confirmation_hours", storm["confirmation_hours"]),
                    field("charge_margin_hours", storm["charge_margin_hours"]),
                    field("accumulation_hours", storm["accumulation_hours"]),
                    field("check_interval_minutes", storm["check_interval_minutes"]),
                    field("immediate_horizon_hours", storm["immediate_horizon_hours"]),
                    field("target_soc", storm["target_soc"], step="0.1"),
                    field("reserve_soc", storm["reserve_soc"], step="0.1"),
                    '<p>Urgent weather override can recommend charging without an active watch or old forecast confirmation.</p>',
                    check("snow_enabled", storm["snow"]["enabled"]),
                    field("snow_threshold", storm["snow"]["threshold_inches"], step="0.01"),
                    optional_field("snow_immediate_threshold", storm["snow"]["immediate_threshold_inches"]),
                    check("ice_enabled", storm["ice"]["enabled"]),
                    field("ice_threshold", storm["ice"]["threshold_inches"], step="0.01"),
                    optional_field("ice_immediate_threshold", storm["ice"]["immediate_threshold_inches"]),
                    check("wind_enabled", storm["wind"]["enabled"]),
                    '<label>Wind threshold MPH <input name="wind_threshold" type="number" min="0.01" max="200" step="0.1" value="' +
                    html.escape(str(storm["wind"]["threshold_mph"] or "")) + '"></label>',
                    optional_field("wind_immediate_threshold", storm["wind"]["immediate_threshold_mph"]),
                    '</fieldset><button type="submit">Save settings</button></form>',
                    '<hr><h2>Change administrator password</h2><form method="post" action="/password">',
                    '<input type="hidden" name="csrf" value="' + html.escape(token) + '">',
                    '<label>Current password <input type="password" name="current" required></label><p></p>',
                    '<label>New password, 14+ characters <input type="password" name="new" minlength="14" required></label><p></p>',
                    '<button type="submit">Change password</button></form>']
            self.reply(200, "\n".join(page))

        def do_POST(self):
            if self.path not in ("/settings", "/password"):
                return self.reply(404, "Not found", "text/plain")
            if not self.require_login():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
                return self.reply(415, "Expected form data", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_BODY:
                    return self.reply(413, "Invalid form size", "text/plain")
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True, max_num_fields=32)
                with LOCK:
                    auth = load_json(auth_path)
                    token = form.get("csrf", [""])[0]
                    if not isinstance(token, str) or not hmac.compare_digest(token, auth["csrf"]):
                        return self.reply(403, "Invalid form token", "text/plain")
                    if self.path == "/password":
                        if not verify_password(form["current"][0], auth):
                            return self.reply(403, "Current password incorrect", "text/plain")
                        new_auth = password_record(form["new"][0])
                        atomic_json(auth_path, new_auth)
                        return self.reply(200, "<p>Password changed. Reload and authenticate with the new password.</p>")
                    config = load_json(config_path)
                    updated = parse_settings(form, config)
                    atomic_json(config_path, updated, mode=0o600)
                return self.reply(200, "<p>Saved. Controller restart/integration required before new behaviour takes effect.</p><a href='/'>Back to settings</a>")
            except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
                return self.reply(400, "<p>Settings rejected: " + html.escape(str(exc)) + "</p><a href='/'>Back</a>")

        def log_message(self, format, *args):
            return

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init-password", "serve"])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--auth", default=DEFAULT_AUTH)
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()
    if args.action == "init-password":
        if Path(args.auth).exists():
            raise SystemExit("Password already initialised. Change it through the admin page.")
        password = getpass.getpass("Set administrator password (14+ chars): ")
        confirm = getpass.getpass("Repeat password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match")
        atomic_json(args.auth, password_record(password))
        print("Operator password initialised. Never expose port 8082 to the LAN.")
        return
    if not Path(args.auth).is_file():
        raise SystemExit("Run init-password locally before starting the operator portal")
    validate_config(load_json(args.config))
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.config, args.auth)).serve_forever()


if __name__ == "__main__":
    main()
