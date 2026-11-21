#!/usr/bin/env python3
"""No-login Commander Boomer request-full-charge page (ADVISORY ONLY).

This is a staged private-LAN UI, not a Victron command endpoint. GET exposes
status and lets a trusted household user choose a post-full hold duration.
POST requires an explicit confirmation and same-page CSRF token. It cannot
change administrator settings or electrical protections.

Default bind is loopback; private-LAN exposure or embedding into the current
Pi dashboard requires an explicit reviewed deployment change.
"""
from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import urllib.parse

from commander_full_charge import (
    begin_full_charge, cancel_full_charge, describe_full_charge,
)
from operator_policy import validate_config
from pi_storm_supervisor import read_manual_override, set_manual_mode
from operator_portal import load_json
import datetime as dt

UTC = dt.timezone.utc
DEFAULT_CONFIG = "/data/solar-fortress/config/controller.json"
DEFAULT_STATE = "/data/solar-fortress/state/commander-full-charge.json"
DEFAULT_STORM_STATE = "/data/solar-fortress/state/commander-storm-override.json"
MAX_POST = 2048


def checked_bind_address(value):
    """Require an explicit loopback/private IPv4 listener, never wildcard."""
    try:
        host = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Specify a numeric private IPv4 listener") from exc
    if host.version != 4 or host.is_unspecified or host.is_multicast or not host.is_private:
        raise ValueError("Only an explicit private or loopback IPv4 bind is allowed")
    return str(host)


def make_handler(config_path, state_path, storm_state_path=None):
    storm_state_path = (Path(storm_state_path) if storm_state_path is not None
                        else Path(state_path).parent / "commander-storm-override.json")
    csrf = secrets.token_urlsafe(32)
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, message):
            data = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/":
                return self.reply(404, "Not found")
            try:
                config = validate_config(load_json(config_path))
                commander = config["commander_full_charge"]
                status = describe_full_charge(state_path, dt.datetime.now(UTC))
                override = read_manual_override(storm_state_path, dt.datetime.now(UTC))
                storm_policy = config["storm"]
                enabled = commander["enabled"]
                token = html.escape(csrf, quote=True)
                message = (
                    '<!doctype html><meta charset="utf-8">'
                    '<title>Solar Fortress | Full Charge</title>'
                    '<style>body{font:1.1rem system-ui;max-width:32rem;'
                    'margin:2rem auto;padding:1rem}button,input{font:inherit;'
                    'padding:.6rem;margin:.3rem}label{display:block}</style>'
                    '<h1>Solar Fortress</h1><h2>Commander Boomer: Request Full Charge</h2>'
                    '<p>No login required. This is a one-time request, not a settings change.</p>'
                    '<p>Current request: <strong>' +
                    html.escape(status["phase"].replace("_", " ")) +
                    '</strong></p>'
                )
                if status["phase"] == "charging":
                    message += ('<p>Charging requested now. Failsafe manual override expires: ' +
                                html.escape(status["override_expires_at"]) +
                                '</p>')
                if status["phase"] == "holding_full":
                    message += ('<p>Full charge confirmed by SmartShunt. '
                                'Hold ends at: ' +
                                html.escape(status["hold_expires_at"]) + '</p>')
                if enabled:
                    message += (
                        '<form method="post" action="/request">'
                        '<input type="hidden" name="csrf" value="' + token + '">'
                        '<label>After the SmartShunt confirms full charge, '
                        'how many hours should it hold full?</label>'
                        '<input type="number" name="hold_hours" min="0" max="' +
                        str(commander["max_hold_hours"]) +
                        '" step="1" value="' + str(commander["default_hold_hours"]) +
                        '" required>'
                        '<p>Charging is requested immediately. The selected hold begins '
                        'only after SmartShunt confirms full. 0 hours means stop at full. '
                        'An independent, configurable failsafe eventually expires the manual '
                        'request if SmartShunt never reports full.</p>'
                        '<label><input type="checkbox" name="confirm" value="yes" required>'
                        'I confirm this full-charge request</label>'
                        '<label><input type="checkbox" name="replace" value="yes">'
                        'Replace the existing full-charge request, if one is active</label>'
                        '<button type="submit">Charge Now: Request Full Charge</button></form>'
                        '<form method="post" action="/cancel">'
                        '<input type="hidden" name="csrf" value="' + token + '">'
                        '<label><input type="checkbox" name="confirm" value="yes" required>'
                        'Confirm cancellation</label>'
                        '<button type="submit">Cancel Full Charge Request</button></form>'
                    )
                else:
                    message += '<p>Full-charge requests are disabled by the administrator.</p>'
                message += (
                    '<hr><h2>Manual Storm Mode</h2>'
                    '<p>This overrides the server temporarily. Every override expires '
                    'automatically unless explicitly renewed.</p>'
                    '<p>Current manual mode: <strong>' +
                    html.escape(override["mode"].replace("_", " ")) +
                    '</strong></p>'
                )
                if override.get("expires_at"):
                    message += (
                        '<p>Override expires: ' +
                        html.escape(override["expires_at"]) + '</p>'
                    )
                message += (
                    '<form method="post" action="/storm-mode">'
                    '<input type="hidden" name="csrf" value="' + token + '">'
                    '<label>Choose mode <select name="mode">'
                    '<option value="storm_charge">Storm: Grid Charge</option>'
                    '<option value="storm_pass_through">Storm: Pass-Through</option>'
                    '<option value="normal">Normal SOC Policy</option>'
                    '<option value="automatic">Cancel Manual Override / Use Server</option>'
                    '</select></label>'
                    '<label>Override duration (hours) '
                    '<input type="number" name="duration_hours" min="1" max="' +
                    str(storm_policy["manual_override_max_hours"]) +
                    '" value="' + str(storm_policy["manual_override_default_hours"]) +
                    '" step="1" required></label>'
                    '<label><input type="checkbox" name="renew" value="yes">'
                    'Explicitly renew the current override</label>'
                    '<label><input type="checkbox" name="confirm" value="yes" required>'
                    'I confirm this time-limited storm override</label>'
                    '<button type="submit">Apply Temporary Storm Mode</button></form>'
                )
                message += '<p>Electrical safety protections always take precedence.</p>'
                self.reply(200, message)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self.reply(503, "Status unavailable: " + html.escape(str(exc)))

        def do_POST(self):
            if self.path not in ("/request", "/cancel", "/storm-mode"):
                return self.reply(404, "Not found")
            if (self.headers.get("Content-Type", "").split(";")[0].strip() !=
                    "application/x-www-form-urlencoded"):
                return self.reply(415, "Form data required")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_POST:
                    return self.reply(413, "Request too large")
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"),
                                             keep_blank_values=True, max_num_fields=6)
                if len(form.get("csrf", [])) != 1 or not secrets.compare_digest(
                        form["csrf"][0], csrf):
                    return self.reply(403, "Invalid request token")
                if form.get("confirm") != ["yes"]:
                    return self.reply(400, "Confirm this action before submitting")
                settings = validate_config(load_json(config_path))
                if self.path in ("/request", "/cancel") and (
                        not settings["commander_full_charge"]["enabled"]):
                    return self.reply(403, "Request control disabled by administrator")
                with lock:
                    now = dt.datetime.now(UTC)
                    if self.path == "/request":
                        hold = int(form["hold_hours"][0])
                        result = begin_full_charge(
                            state_path, now, hold, settings, confirmed=True,
                            replace_existing=(form.get("replace") == ["yes"]),
                        )
                    elif self.path == "/cancel":
                        result = cancel_full_charge(state_path, now, confirmed=True)
                    else:
                        mode = form["mode"][0]
                        hours = int(form["duration_hours"][0])
                        changed = set_manual_mode(
                            storm_state_path, mode, confirmed=True, now=now,
                            duration_hours=hours,
                            max_hours=settings["storm"]["manual_override_max_hours"],
                            renew=(form.get("renew") == ["yes"]),
                        )
                        active = read_manual_override(storm_state_path, now)
                        result = {
                            "phase": ("changed" if changed else "already_active"),
                            "override_expires_at": active.get("expires_at"),
                        }
                detail = (" Expires: " + html.escape(result["override_expires_at"])
                          if result.get("override_expires_at") else "")
                self.reply(
                    200, "<p>Request recorded: " + html.escape(result["phase"]) +
                    "." + detail + "</p><p>Awaiting controller integration before "
                    "physical commands are enabled.</p><a href='/'>Return to status</a>"
                )
            except (ValueError, TypeError, IndexError, KeyError, OSError) as exc:
                self.reply(400, "Request rejected: " + html.escape(str(exc)))

        def log_message(self, fmt, *args):
            return

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--storm-state", default=DEFAULT_STORM_STATE)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8083)
    args = parser.parse_args()
    address = checked_bind_address(args.bind)
    ThreadingHTTPServer((address, args.port),
                        make_handler(Path(args.config), Path(args.state),
                                     Path(args.storm_state))).serve_forever()


if __name__ == "__main__":
    main()
