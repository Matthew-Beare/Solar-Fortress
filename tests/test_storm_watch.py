import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from operator_policy import validate_config
from storm_watch import assess_storm_watch, qualifying_events


class StormWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        self.policy = validate_config({"storm": {"enabled": True}})
        self.ice = [{"start": self.now + dt.timedelta(hours=40),
                     "end": self.now + dt.timedelta(hours=42),
                     "ice_inches": 0.35}]

    def assess(self, now=None, periods=None, history=None, soc=30, amps=30, config=None):
        return assess_storm_watch(
            now or self.now, self.ice if periods is None else periods,
            [] if history is None else history, config or self.policy,
            soc=soc, capacity_ah=600, actual_dc_charge_amps=amps,
            forecast_source_updated_at=now or self.now
        )

    def test_watch_is_48_hours_and_does_not_charge_immediately(self):
        result = self.assess()
        self.assertEqual(result["stage"], "watch")
        self.assertFalse(result["charge_recommended"])
        self.assertEqual(result["hours_until_onset"], 40)
        self.assertEqual(result["charge_planning_hours"], 18)

    def test_24h_old_forecast_same_storm_promotes_to_charge(self):
        snapshot = {"checked_at": self.now, "periods": self.ice,
                    "source_update_time": self.now.isoformat()}
        later = self.now + dt.timedelta(hours=24)
        result = self.assess(now=later, history=[snapshot])
        self.assertEqual(result["stage"], "charge_due")
        self.assertTrue(result["confirmed_by_history"])
        self.assertFalse(result["deadline_override"])

    def test_replaying_same_cached_forecast_is_not_confirmation(self):
        snapshot = {"checked_at": self.now, "periods": self.ice,
                    "source_update_time": self.now.isoformat()}
        later = self.now + dt.timedelta(hours=24)
        result = assess_storm_watch(
            later, self.ice, [snapshot], self.policy,
            soc=30, capacity_ah=600, actual_dc_charge_amps=30,
            forecast_source_updated_at=self.now.isoformat()
        )
        self.assertFalse(result["confirmed_by_history"])
        self.assertTrue(result["deadline_override"])

    def test_deadline_overrides_incomplete_confirmation(self):
        result = self.assess(now=self.now + dt.timedelta(hours=22))
        self.assertTrue(result["charge_recommended"])
        self.assertTrue(result["deadline_override"])
        self.assertFalse(result["confirmed_by_history"])

    def test_no_storm_and_no_latch_reset(self):
        result = self.assess(periods=[])
        self.assertEqual(result["stage"], "clear")
        self.assertFalse(result["charge_recommended"])
        self.assertIn("does not release", result["note"])

    def test_missing_battery_state(self):
        result = self.assess(soc=None)
        self.assertEqual(result["stage"], "needs_fresh_telemetry")
        self.assertFalse(result["charge_recommended"])

    def test_old_forecast_too_recent_is_not_confirmation(self):
        snapshot = {"checked_at": self.now - dt.timedelta(hours=1), "periods": self.ice,
                    "source_update_time": (self.now - dt.timedelta(hours=1)).isoformat()}
        result = self.assess(history=[snapshot])
        self.assertFalse(result["confirmed_by_history"])

    def test_earlier_forecast_different_onset_not_same_event(self):
        old = [{"start": self.now - dt.timedelta(hours=15),
                "end": self.now - dt.timedelta(hours=13),
                "ice_inches": 0.4}]
        snapshot = {"checked_at": self.now - dt.timedelta(hours=24), "periods": old,
                    "source_update_time": (self.now - dt.timedelta(hours=24)).isoformat()}
        result = self.assess(history=[snapshot])
        self.assertFalse(result["confirmed_by_history"])

    def test_full_battery_has_no_charge_request(self):
        result = self.assess(soc=100)
        self.assertEqual(result["stage"], "ready")
        self.assertFalse(result["charge_recommended"])

    def test_storm_beyond_watch_does_not_trigger(self):
        event = [{"start": self.now + dt.timedelta(hours=50),
                  "end": self.now + dt.timedelta(hours=52),
                  "ice_inches": 0.5}]
        self.assertEqual(self.assess(periods=event)["stage"], "clear")

    def test_ice_accumulates_over_24h(self):
        periods = [
            {"start": self.now + dt.timedelta(hours=35),
             "end": self.now + dt.timedelta(hours=37), "ice_inches": 0.15},
            {"start": self.now + dt.timedelta(hours=40),
             "end": self.now + dt.timedelta(hours=42), "ice_inches": 0.12},
        ]
        result = self.assess(periods=periods)
        self.assertEqual(result["stage"], "watch")
        self.assertIn("ice", result["reasons"])
        self.assertEqual(result["hours_until_onset"], 35)

    def test_no_overlap_double_count(self):
        event = [
            {"start": self.now + dt.timedelta(hours=20),
             "end": self.now + dt.timedelta(hours=26), "ice_inches": .15},
            {"start": self.now + dt.timedelta(hours=25),
             "end": self.now + dt.timedelta(hours=28), "ice_inches": .15},
        ]
        with self.assertRaises(ValueError):
            qualifying_events(self.now, event, self.policy)

    def test_config_margins(self):
        with self.assertRaises(ValueError):
            validate_config({"storm": {"watch_hours": 10}})
        with self.assertRaises(ValueError):
            validate_config({"storm": {"watch_hours": 24, "confirmation_hours": 24}})


if __name__ == "__main__":
    unittest.main()
