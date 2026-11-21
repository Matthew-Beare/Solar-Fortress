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
        self.assertEqual(p["storm"]["watch_hours"], 48)
        self.assertEqual(p["storm"]["confirmation_hours"], 24)
        self.assertEqual(p["storm"]["charge_margin_hours"], 4)
        self.assertEqual(p["storm"]["immediate_horizon_hours"], 24)
        self.assertEqual(p["storm"]["ice"]["immediate_threshold_inches"], 0.5)
        self.assertEqual(p["storm"]["accumulation_hours"], 24)
        self.assertEqual(p["storm"]["check_interval_minutes"], 15)
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

    def test_rolling_twelve_hours_enters_as_storm_approaches(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        policy = validate_config({"storm": {"enabled": True, "lookahead_hours": 12}})
        period = {
            "start": (now + dt.timedelta(hours=13)).isoformat(),
            "end": (now + dt.timedelta(hours=15)).isoformat(),
            "ice_inches": 0.35,
        }
        self.assertFalse(storm_decision(now, [period], policy)["active"])
        # Two hours later the same forecast is now inside the rolling window.
        result = storm_decision(now + dt.timedelta(hours=2), [period], policy)
        self.assertTrue(result["active"])
        self.assertEqual(result["reasons"], ["ice"])

    def test_ice_accumulates_across_hours(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        policy = validate_config({"storm": {"enabled": True}})
        periods = [
            {"start": now + dt.timedelta(hours=3), "end": now + dt.timedelta(hours=5), "ice_inches": .15},
            {"start": now + dt.timedelta(hours=5), "end": now + dt.timedelta(hours=7), "ice_inches": .12},
        ]
        self.assertEqual(storm_decision(now, periods, policy)["reasons"], ["ice"])
        # No additional storm event may be counted outside the lookahead.
        self.assertFalse(storm_decision(now - dt.timedelta(hours=15), periods, policy)["active"])

    def test_overlapping_accumulation_periods_rejected(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        policy = validate_config({"storm": {"enabled": True}})
        periods = [
            {"start": now, "end": now + dt.timedelta(hours=3), "snow_inches": 4},
            {"start": now + dt.timedelta(hours=2), "end": now + dt.timedelta(hours=5), "snow_inches": 4},
        ]
        with self.assertRaises(ValueError):
            storm_decision(now, periods, policy)

    def test_conservative_partial_interval_and_wind_max(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        policy = validate_config({"storm": {
            "enabled": True, "wind": {"enabled": True, "threshold_mph": 55}
        }})
        periods = [
            {"start": now + dt.timedelta(hours=11), "end": now + dt.timedelta(hours=17),
             "ice_inches": .27, "wind_mph": 35},
            {"start": now + dt.timedelta(hours=3), "end": now + dt.timedelta(hours=4),
             "wind_mph": 60},
        ]
        self.assertEqual(storm_decision(now, periods, policy)["reasons"], ["ice", "wind"])

    def test_two_windows_and_configurable_poll(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        # Ice starts 8h away. Two successive readings total 0.27 inches
        # inside 24h despite the second one starting beyond the 12h lead.
        intervals = [
            {"start": now + dt.timedelta(hours=8), "end": now + dt.timedelta(hours=10),
             "ice_inches": 0.15},
            {"start": now + dt.timedelta(hours=14), "end": now + dt.timedelta(hours=16),
             "ice_inches": 0.12},
        ]
        conf = validate_config({"storm": {"enabled": True}})
        result = storm_decision(now, intervals, conf)
        self.assertEqual(result["reasons"], ["ice"])
        self.assertEqual(result["lookahead_hours"], 12)
        self.assertEqual(result["accumulation_hours"], 24)
        self.assertEqual(result["check_interval_minutes"], 15)
        # If 12h is mistakenly used as the total accumulation interval,
        # the second piece would be missed and this should NOT trigger.
        shorter = validate_config({"storm": {"enabled": True, "accumulation_hours": 4,
                                               "check_interval_minutes": 30}})
        result = storm_decision(now, intervals, shorter)
        self.assertFalse(result["active"])
        self.assertEqual(result["check_interval_minutes"], 30)

    def test_no_early_trigger_for_storm_beginning_after_lead(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        period = {"start": now + dt.timedelta(hours=13),
                  "end": now + dt.timedelta(hours=15), "ice_inches": 0.7}
        policy = validate_config({"storm": {"enabled": True}})
        self.assertFalse(storm_decision(now, [period], policy)["active"])
        self.assertTrue(storm_decision(now + dt.timedelta(hours=2), [period], policy)["active"])

    def test_no_false_trigger_from_24h_ice_within_next_12h_dry(self):
        now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        conf = validate_config({"storm": {"enabled": True}})
        periods = [
            {"start": now, "end": now + dt.timedelta(hours=12), "ice_inches": 0},
            {"start": now + dt.timedelta(hours=14),
             "end": now + dt.timedelta(hours=16), "ice_inches": 0.6},
        ]
        # Future accumulation is high, but all precipitation begins >12h.
        self.assertFalse(storm_decision(now, periods, conf)["active"])
        self.assertTrue(storm_decision(now + dt.timedelta(hours=3), periods, conf)["active"])

    def test_reject_out_of_range_polling_cadence(self):
        for interval in (0, 4, 121, 15.5, True):
            with self.assertRaises(ValueError):
                validate_config({"storm": {"check_interval_minutes": interval}})

    def test_independent_watch_and_timer_settings(self):
        p = validate_config({"storm": {"watch_hours": 8, "lookahead_hours": 12,
                                       "confirmation_hours": 24, "charge_margin_hours": 8}})
        self.assertEqual(p["storm"]["watch_hours"], 8)
        self.assertEqual(p["storm"]["charge_margin_hours"], 8)
        with self.assertRaises(ValueError):
            validate_config({"storm": {"watch_hours": 0}})
        with self.assertRaises(ValueError):
            validate_config({"storm": {"ice": {"immediate_threshold_inches": 0.1}}})
        with self.assertRaises(ValueError):
            validate_config({"storm": {"reserve_soc": 100, "target_soc": 90}})

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
