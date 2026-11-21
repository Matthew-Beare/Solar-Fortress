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
        atomic_json(self.auth, password_record("a-very-long-operator-secret"))
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

    def test_csrf_rejection_and_valid_update(self):
        password = "a-very-long-operator-secret"
        form = {"csrf": "wrong", "grid_charge": 20, "grid_pass_through": 30,
                "battery_return": 35,
                "pass_through_qualification_seconds": 60,
                "grid_charge_qualification_seconds": 60,
                "stop_grid_charge_qualification_seconds": 60,
                "post_full_hold_minutes": 75, "full_charge_interval_days": 10,
                "battery_capacity_ah": 600, "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 0,
                "request_repeat_days": 4, "lookahead_hours": 12, "watch_hours": 8, "confirmation_hours": 24, "charge_margin_hours": 8, "accumulation_hours": 24, "check_interval_minutes": 15, "immediate_horizon_hours": 24, "target_soc": 100, "reserve_soc": 90,
                "snow_threshold": 6, "ice_threshold": .25, "wind_threshold": "",
                "snow_immediate_threshold": "", "ice_immediate_threshold": 0.5,
                "wind_immediate_threshold": "",
                "storm_enabled": "on", "snow_enabled": "on", "ice_enabled": "on"}
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 403)
        self.assertNotIn("storm", json.loads(self.config.read_text()))
        form["csrf"] = json.loads(self.auth.read_text())["csrf"]
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 200)
        saved = json.loads(self.config.read_text())
        self.assertTrue(saved["storm"]["enabled"])
        self.assertEqual(saved["storm"]["watch_hours"], 8)
        self.assertEqual(saved["qualification_seconds"]["pass_through"], 60)
        self.assertEqual(saved["maintenance"]["post_full_hold_minutes"], 75)
        self.assertEqual(saved["charge_estimate"]["battery_capacity_ah"], 600)
        self.assertEqual(saved["charge_estimate"]["planning_dc_charge_amps"], 30)
        self.assertEqual(saved["storm"]["charge_margin_hours"], 8)
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
                "battery_capacity_ah": 600, "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 0,
                "full_charge_interval_days": 10, "request_repeat_days": 4,
                "lookahead_hours": 12, "watch_hours": 8, "confirmation_hours": 24, "charge_margin_hours": 8, "accumulation_hours": 24, "check_interval_minutes": 15, "immediate_horizon_hours": 24, "target_soc": 100, "reserve_soc": 90, "snow_threshold": 6, "ice_threshold": .25,
                "wind_threshold": "", "snow_immediate_threshold": "",
                "ice_immediate_threshold": 0.5, "wind_immediate_threshold": ""}
        status, _ = self.request("POST", "/settings", form, password)
        self.assertEqual(status, 400)
        self.assertEqual(self.config.read_bytes(), original)

    def test_admin_password_rotation(self):
        old = "a-very-long-operator-secret"
        form = {"csrf": json.loads(self.auth.read_text())["csrf"],
                "current": old, "new": "another-long-administrator-secret"}
        status, _ = self.request("POST", "/password", form, old)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/", password=old)[0], 401)
        self.assertEqual(self.request("GET", "/", password=form["new"])[0], 200)


if __name__ == "__main__":
    unittest.main()
