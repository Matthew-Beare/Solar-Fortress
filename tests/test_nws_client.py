import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from nws_client import fetch_weather, normalize_grid, active_winter_alerts, write_atomic_json


class NWSLocalClientTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        self.grid_url = "https://api.weather.gov/gridpoints/MRX/45,56"
        self.point_url = "https://api.weather.gov/points/36.0,-82.5"
        self.alert_url = "https://api.weather.gov/alerts/active?point=36.0,-82.5"
        self.grid = {
            "properties": {
                "updateTime": self.now.isoformat(),
                "iceAccumulation": {"uom": "wmoUnit:mm", "values": [
                    {"validTime": "2026-11-21T12:00:00Z/PT6H", "value": 12.7}
                ]},
                "snowfallAmount": {"uom": "wmoUnit:mm", "values": [
                    {"validTime": "2026-11-21T12:00:00Z/PT6H", "value": 0}
                ]},
                "windGust": {"uom": "wmoUnit:km_h-1", "values": []},
            }
        }

    def test_fetch_and_normalize_ice_with_live_alert_gate(self):
        fixtures = {
            self.point_url: {"properties": {"forecastGridData": self.grid_url}},
            self.grid_url: self.grid,
            self.alert_url: {"features": [
                {"properties": {"event": "Ice Storm Warning"}},
                {"properties": {"event": "Special Weather Statement"}},
            ]},
        }
        forecast, alerts = fetch_weather(
            36.0, -82.5, self.now, fetch=lambda url: fixtures[url]
        )
        ice = next(p["ice_inches"] for p in forecast["periods"]
                   if "ice_inches" in p)
        self.assertAlmostEqual(ice, .5, places=3)
        self.assertFalse(alerts["alerts_clear"])
        self.assertEqual(alerts["active_winter_events"], ["Ice Storm Warning"])

    def test_clear_alerts_are_explicit(self):
        clear = active_winter_alerts({"features": []}, self.now)
        self.assertTrue(clear["alerts_clear"])
        with self.assertRaises(ValueError):
            active_winter_alerts({}, self.now)

    def test_reject_foreign_forecast_grid_and_invalid_units(self):
        with self.assertRaises(ValueError):
            normalize_grid({"properties": {"updateTime": "2026-11-21T00:00:00Z",
                                          "iceAccumulation": {
                                              "uom": "cubic-metres",
                                              "values": [{
                                                  "validTime": "2026-11-21T12:00:00Z/PT1H",
                                                  "value": 1,
                                              }]
                                          }}})
        with self.assertRaises(ValueError):
            fetch_weather(
                36.0, -82.5, self.now,
                fetch=lambda url: {"properties": {
                    "forecastGridData": "https://example.com/evil"
                }}
            )

    def test_atomic_file_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "weather.json"
            write_atomic_json(path, {"periods": []})
            self.assertEqual(json.loads(path.read_text()), {"periods": []})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
