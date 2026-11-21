import datetime as dt
import datetime as dt
import http.client
import json
import pathlib
import re
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from commander_portal import checked_bind_address, make_handler
from operator_portal import atomic_json
from operator_policy import validate_config
from commander_full_charge import describe_full_charge


class CommanderPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.config = root / "controller.json"
        self.state = root / "commander-full.json"
        atomic_json(self.config, validate_config({
            "commander_full_charge": {
                "default_hold_hours": 8,
                "max_hold_hours": 48,
                "override_expiry_hours": 240,
            }
        }))
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.config, self.state)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, data=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def token(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Commander Boomer", html)
        self.assertIn("After the SmartShunt confirms full charge", html)
        self.assertIn("Charge Now: Request Full Charge", html)
        token = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        return token

    def test_loginless_request_hold_and_cancel(self):
        token = self.token()
        self.assertEqual(self.request("POST", "/request", {
            "csrf": "invalid", "hold_hours": "8", "confirm": "yes"
        })[0], 403)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.request("POST", "/request", {
            "csrf": token, "hold_hours": "8"
        })[0], 400)
        self.assertFalse(self.state.exists())
        status, body = self.request("POST", "/request", {
            "csrf": token, "hold_hours": "12", "confirm": "yes"
        })
        self.assertEqual(status, 200)
        self.assertIn("charging", body)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["hold_hours"], 12)
        self.assertEqual(saved["phase"], "charging")
        self.assertIn("override_expires_at", saved)
        self.assertNotIn("charge_attempt_expires_at", saved)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request("POST", "/cancel", {
            "csrf": token, "confirm": "yes"
        })[0], 200)
        self.assertEqual(json.loads(self.state.read_text())["phase"], "cancelled")

    def test_invalid_manual_hold_rejected(self):
        token = self.token()
        for hold in ("49", "-1", "not-a-number"):
            status, _ = self.request("POST", "/request", {
                "csrf": token, "hold_hours": hold, "confirm": "yes"
            })
            self.assertEqual(status, 400)

    def test_commander_disabled_by_admin(self):
        atomic_json(self.config, validate_config({
            "commander_full_charge": {"enabled": False}
        }))
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("disabled by the administrator", body)
        status, _ = self.request("POST", "/request", {
            "csrf": "no-need", "hold_hours": "8", "confirm": "yes"
        })
        self.assertNotEqual(status, 200)

    def test_local_manual_storm_override_expires_and_requires_renewal(self):
        token = self.token()
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Manual Storm Mode", body)
        self.assertIn("Override duration", body)
        self.assertEqual(self.request("POST", "/storm-mode", {
            "csrf": token, "mode": "storm_pass_through", "duration_hours": "2"
        })[0], 400)
        status, body = self.request("POST", "/storm-mode", {
            "csrf": token, "mode": "storm_pass_through",
            "duration_hours": "2", "confirm": "yes",
        })
        self.assertEqual(status, 200)
        self.assertIn("Expires:", body)
        manual = self.state.parent / "commander-storm-override.json"
        stored = json.loads(manual.read_text())
        self.assertEqual(stored["mode"], "storm_pass_through")
        self.assertIn("expires_at", stored)
        self.assertEqual(manual.stat().st_mode & 0o777, 0o600)
        original = manual.read_bytes()
        status, _ = self.request("POST", "/storm-mode", {
            "csrf": token, "mode": "storm_pass_through",
            "duration_hours": "2", "confirm": "yes",
        })
        self.assertEqual(status, 200)
        self.assertEqual(original, manual.read_bytes())
        status, _ = self.request("POST", "/storm-mode", {
            "csrf": token, "mode": "storm_pass_through",
            "duration_hours": "3", "confirm": "yes", "renew": "yes",
        })
        self.assertEqual(status, 200)
        self.assertNotEqual(original, manual.read_bytes())
        expiry = dt.datetime.fromisoformat(json.loads(manual.read_text())["expires_at"])
        from pi_storm_supervisor import read_manual_override
        self.assertEqual(read_manual_override(manual, expiry)["mode"], "automatic")
        status, _ = self.request("POST", "/storm-mode", {
            "csrf": token, "mode": "automatic", "duration_hours": "1",
            "confirm": "yes",
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(manual.read_text())["mode"], "automatic")

    def test_must_explicitly_bind_to_private_ipv4(self):
        for address in ("0.0.0.0", "8.8.8.8", "::", "example.com"):
            with self.assertRaises(ValueError):
                checked_bind_address(address)
        self.assertEqual(checked_bind_address("127.0.0.1"), "127.0.0.1")
        self.assertEqual(checked_bind_address("192.168.1.21"), "192.168.1.21")


if __name__ == "__main__":
    unittest.main()
