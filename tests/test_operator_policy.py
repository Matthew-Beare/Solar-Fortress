import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from operator_policy import (
    DEFAULTS, estimated_charge_hours, maintenance_request_due,
    storm_decision, validate_config,
)


class OperatorPolicyTests(unittest.TestCase):
    def test_defaults_do_not_enable_storm(self):
        p = validate_config({})
        self.assertFalse(p["storm"]["enabled"])
        self.assertEqual(p["storm"]["lookahead_hours"], 12)
        self.assertEqual(p["maintenance"]["request_repeat_days"], 4)

    def test_preserve_existing_keys(self):
        p = validate_config({"custom_runtime": {"keep": "me"}, "qualification_seconds": {"grid_charge": 60}})
        self.assertEqual(p["custom_runtime"]["keep"], "me")
        self.assertEqual(p["qualification_seconds"]["grid_charge"], 60)

    def test_guard_invalid_threshold_order(self):
        with self.assertRaises(ValueError):
            validate_config({"soc_thresholds": {"grid_charge": 35, "grid_pass_through": 30}})
        with self.assertRaises(ValueError):
            validate_config({"storm": {"wind": {"enabled": True, "threshold_mph": None}}})

    def test_disabled_condition_does_not_trigger(self):
        now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        p = validate_config({"storm": {"enabled": True}})
        period = {"start": now, "end": now + dt.timedelta(hours=2), "wind_mph": 100, "snow_inches": 2, "ice_inches": 0}
        self.assertFalse(storm_decision(now, [period], p)["active"])
        self.assertNotIn("wind", storm_decision(now, [period], p)["unknown"])

    def test_storm_window_and_missing_data(self):
        now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        p = validate_config({"storm": {"enabled": True}})
        later = {"start": now + dt.timedelta(hours=14), "end": now + dt.timedelta(hours=16), "snow_inches": 12}
        inside = {"start": now + dt.timedelta(hours=2), "end": now + dt.timedelta(hours=4), "ice_inches": .3}
        self.assertFalse(storm_decision(now, [later], p)["active"])
        result = storm_decision(now, [later, inside], p)
        self.assertTrue(result["active"])
        self.assertEqual(result["reasons"], ["ice"])
        self.assertEqual(result["unknown"], ["snow"])

    def test_maintenance_uses_smartshunt_event_and_retry(self):
        now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        p = validate_config({})
        last_full = now - dt.timedelta(days=11)
        self.assertIsNone(maintenance_request_due(now, None, None, p))
        self.assertTrue(maintenance_request_due(now, last_full, None, p))
        self.assertFalse(maintenance_request_due(now, last_full, now - dt.timedelta(days=3), p))
        self.assertTrue(maintenance_request_due(now, last_full, now - dt.timedelta(days=4), p))

    def test_eta_is_optimistic_not_guarantee(self):
        self.assertAlmostEqual(estimated_charge_hours(40, 600, 50), 7.2)
        with self.assertRaises(ValueError):
            estimated_charge_hours(40, 600, 0)


if __name__ == "__main__":
    unittest.main()
