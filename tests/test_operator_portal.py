import base64
import http.client
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from operator_portal import atomic_json, make_handler, password_record, verify_password


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.config = root / "controller.json"
        self.auth = root / "operator-auth.json"
        atomic_json(self.config, {"soc_thresholds": {
            "grid_charge": 20, "grid_pass_through": 30, "battery_return": 35
        }, "qualification_seconds": {"pass_through": 60, "grid_charge": 60,
                                      "stop_grid_charge": 60},
        "preserve": {"deployed_feature": True}}, mode=0o640)
        atomic_json(self.auth, password_record("a-very-long-operator-secret", "admin"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.config, self.auth))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, data=None, password=None):
        headers = {}
        if password is not None:
            data64 = base64.b64encode(("admin:" + password).encode()).decode()
            headers["Authorization"] = "Basic " + data64
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = None
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def test_not_public_and_password_hash(self):
        self.assertFalse(verify_password("wrong", json.loads(self.auth.read_text())))
        status, _ = self.request("GET", "/")
        self.assertEqual(status, 401)
        status, body = self.request("GET", "/", password="a-very-long-operator-secret")
        self.assertEqual(status, 200)
        self.assertIn("Maintenance request cadence", body)

    def test_custom_local_picard_login_and_short_accidental_edit_guard(self):
        auth = password_record("1A 2B 3C", username="Captain Jean-Luc Picard")
        self.assertTrue(verify_password("1A 2B 3C", auth))
        atomic_json(self.auth, auth)
        # This credential is only for the isolated operator UI.
        headers = {"Authorization": "Basic " + base64.b64encode(
            "Captain Jean-Luc Picard:1A 2B 3C".encode()).decode()}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/", headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            conn.close()
        with self.assertRaises(ValueError):
            password_record("1234567", "test-user")
        with self.assertRaises(ValueError):
            password_record("sufficient", username="invalid:login")

    def test_csrf_rejection_and_valid_update(self):
        password = "a-very-long-operator-secret"
        form = {"csrf": "wrong", "grid_charge": 20, "grid_pass_through": 30,
                "battery_return": 35,
                "pass_through_qualification_seconds": 60,
                "grid_charge_qualification_seconds": 60,
                "stop_grid_charge_qualification_seconds": 60,
                "post_full_hold_minutes": 75, "full_charge_interval_days": 10, "commander_full_charge_enabled": "on", "commander_default_hold_hours": 12, "commander_max_hold_hours": 168, "commander_charge_attempt_timeout_hours": 72,
                "battery_capacity_ah": 600, "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 0,
                "learning_min_sessions": 2, "learning_history_sessions": 12,
                "learning_soc_match_percent": 5.0,
                "learning_conservative_multiplier": 1.1,
                "learning_max_gap_minutes": 20,
                "learning_sample_interval_seconds": 60,
                "learning_poll_seconds": 60,
                "learning_min_charge_current_amps": 1.0,
                "learning_taper_min_soc_percent": 95.0,
                "learning_taper_min_current_amps": -2.0,
                "learning_min_soc_gain_percent": 5.0,
                "request_repeat_days": 4, "lookahead_hours": 12, "watch_hours": 8, "confirmation_hours": 24, "charge_margin_hours": 8, "accumulation_hours": 24, "check_interval_minutes": 15, "server_grace_seconds": 300, "manual_override_default_hours": 24, "manual_override_max_hours": 168, "immediate_horizon_hours": 24,
                "post_storm_hold_hours": 18, "storm_event_join_hours": 12,
                "max_forecast_age_hours": 6, "forecast_latitude": "", "forecast_longitude": "", "nws_user_agent": "Solar-Fortress/1.0 (test@example.invalid)", "target_soc": 100, "reserve_soc": 90,
                "snow_threshold": 6, "ice_threshold": .25, "wind_threshold": "",
                "snow_immediate_threshold": "", "ice_immediate_threshold": 0.5,
                "wind_immediate_threshold": "",
                "storm_enabled": "on", "snow_enabled": "on", "ice_enabled": "on",
                "maintenance_enabled": "on", "learning_enabled": "on",
                "require_alert_clear_for_release": "on"}
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 403)
        self.assertNotIn("storm", json.loads(self.config.read_text()))
        form["csrf"] = json.loads(self.auth.read_text())["csrf"]
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 200)
        saved = json.loads(self.config.read_text())
        self.assertTrue(saved["maintenance"]["enabled"])
        self.assertTrue(saved["storm"]["enabled"])
        self.assertEqual(saved["storm"]["watch_hours"], 8)
        self.assertEqual(saved["qualification_seconds"]["pass_through"], 60)
        self.assertEqual(saved["maintenance"]["post_full_hold_minutes"], 75)
        self.assertEqual(saved["charge_estimate"]["battery_capacity_ah"], 600)
        self.assertEqual(saved["charge_estimate"]["planning_dc_charge_amps"], 30)
        self.assertEqual(saved["storm"]["charge_margin_hours"], 8)
        self.assertEqual(saved["storm"]["server_grace_seconds"], 300)
        self.assertEqual(saved["storm"]["manual_override_default_hours"], 24)
        self.assertTrue(saved["commander_full_charge"]["enabled"])
        self.assertEqual(saved["commander_full_charge"]["default_hold_hours"], 12)
        self.assertEqual(saved["storm"]["post_storm_hold_hours"], 18)
        self.assertEqual(saved["storm"]["max_forecast_age_hours"], 6)
        self.assertIsNone(saved["storm"]["forecast_latitude"])
        self.assertIn("test@example.invalid", saved["storm"]["nws_user_agent"])
        self.assertTrue(saved["charge_estimate"]["learning_enabled"])
        self.assertEqual(saved["charge_estimate"]["learning_min_sessions"], 2)
        self.assertEqual(saved["charge_estimate"]["learning_poll_seconds"], 60)
        self.assertEqual(saved["charge_estimate"]["learning_taper_min_soc_percent"], 95.0)
        self.assertEqual(saved["storm"]["ice"]["immediate_threshold_inches"], 0.5)
        self.assertEqual(saved["storm"]["target_soc"], 100)
        self.assertEqual(saved["storm"]["reserve_soc"], 90)
        self.assertEqual(saved["maintenance"]["request_repeat_days"], 4)
        self.assertTrue(saved["preserve"]["deployed_feature"])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)

    def test_invalid_threshold_rejected_without_writes(self):
        password = "a-very-long-operator-secret"
        original = self.config.read_bytes()
        form = {"csrf": json.loads(self.auth.read_text())["csrf"],
                "grid_charge": 32, "grid_pass_through": 30, "battery_return": 35,
                "pass_through_qualification_seconds": 60,
                "grid_charge_qualification_seconds": 60,
                "stop_grid_charge_qualification_seconds": 60,
                "post_full_hold_minutes": 60,
                "commander_default_hold_hours": 12, "commander_max_hold_hours": 168,
                "commander_charge_attempt_timeout_hours": 72,
                "battery_capacity_ah": 600, "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 0,
                "learning_min_sessions": 2, "learning_history_sessions": 12,
                "learning_soc_match_percent": 5.0,
                "learning_conservative_multiplier": 1.1,
                "learning_max_gap_minutes": 20,
                "learning_sample_interval_seconds": 60,
                "learning_poll_seconds": 60,
                "learning_min_charge_current_amps": 1.0,
                "learning_taper_min_soc_percent": 95.0,
                "learning_taper_min_current_amps": -2.0,
                "learning_min_soc_gain_percent": 5.0,
                "full_charge_interval_days": 10, "request_repeat_days": 4,
                "lookahead_hours": 12, "watch_hours": 8, "confirmation_hours": 24, "charge_margin_hours": 8, "accumulation_hours": 24, "check_interval_minutes": 15, "server_grace_seconds": 300, "manual_override_default_hours": 24, "manual_override_max_hours": 168, "immediate_horizon_hours": 24,
                "post_storm_hold_hours": 18, "storm_event_join_hours": 12,
                "max_forecast_age_hours": 6, "forecast_latitude": "", "forecast_longitude": "", "nws_user_agent": "Solar-Fortress/1.0 (test@example.invalid)", "target_soc": 100, "reserve_soc": 90, "snow_threshold": 6, "ice_threshold": .25,
                "wind_threshold": "", "snow_immediate_threshold": "",
                "ice_immediate_threshold": 0.5, "wind_immediate_threshold": ""}
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 400)
        self.assertEqual(self.config.read_bytes(), original)

    def test_first_run_enrollment_requires_confirm_and_cannot_reopen(self):
        self.auth.unlink()
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("create first superuser", body)
        import re
        token = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        data = {"csrf": token, "username": "Mike Bravo Echo",
                "password": "my-long-administrator-password",
                "confirm": "not-the-same"}
        status, _ = self.request("POST", "/enroll", data)
        self.assertEqual(status, 400)
        self.assertFalse(self.auth.exists())
        data["confirm"] = data["password"]
        status, _ = self.request("POST", "/enroll", data)
        self.assertEqual(status, 200)
        saved = json.loads(self.auth.read_text())
        self.assertEqual(saved["username"], "Mike Bravo Echo")
        self.assertNotIn("my-long-administrator-password", self.auth.read_text())
        self.assertTrue(verify_password(data["password"], saved))
        self.assertEqual(self.request("GET", "/")[0], 401)
        self.assertEqual(self.request("POST", "/enroll", data)[0], 403)
        login = base64.b64encode(
            ("Mike Bravo Echo:" + data["password"]).encode()).decode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            conn.request("GET", "/", headers={"Authorization": "Basic " + login})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            conn.close()

    def test_password_rotation_must_retype_new_password(self):
        old = "a-very-long-operator-secret"
        token = json.loads(self.auth.read_text())["csrf"]
        bad = {"csrf": token, "current": old,
               "new": "fresh-secret-password-with-length",
               "new_confirm": "different-password"}
        status, _ = self.request("POST", "/password", bad, old)
        self.assertEqual(status, 400)
        self.assertTrue(verify_password(old, json.loads(self.auth.read_text())))

    def test_admin_password_rotation(self):
        old = "a-very-long-operator-secret"
        form = {"csrf": json.loads(self.auth.read_text())["csrf"],
                "current": old, "new": "another-long-administrator-secret",
                "new_confirm": "another-long-administrator-secret"}
        status, _ = self.request("POST", "/password", form, old)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/", password=old)[0], 401)
        self.assertEqual(self.request("GET", "/", password=form["new"])[0], 200)


if __name__ == "__main__":
    unittest.main()
