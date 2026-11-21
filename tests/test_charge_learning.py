import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from charge_learning import estimate_remaining, record_observation, training_status
from operator_policy import validate_config


class ChargeLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "charge.sqlite"
        self.t0 = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        self.policy = validate_config({
            "charge_estimate": {
                "learning_max_gap_minutes": 120,
                "learning_sample_interval_seconds": 60,
                "learning_min_sessions": 2,
                "learning_conservative_multiplier": 1.1,
                "battery_capacity_ah": 600,
                "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 60,
            }
        })

    def sample(self, when, soc, grid=True, amps=25, full=None):
        return record_observation(
            self.path, when, soc=soc, grid_charging=grid,
            battery_current_amps=amps, smartshunt_full_at=full,
            config=self.policy,
        )

    def complete_run(self, offset_days, total_hours, starting_soc=40):
        start = self.t0 + dt.timedelta(days=offset_days)
        for i in range(total_hours):
            # High SOC near the tail, with a deliberately slow taper.
            pct = starting_soc + min(55, 55 * i / max(total_hours-4, 1))
            self.sample(start + dt.timedelta(hours=i), pct)
        event = start + dt.timedelta(hours=total_hours)
        return self.sample(event, 100, grid=False, amps=0, full=event)

    def test_never_learns_from_soc_100_without_smartshunt_event(self):
        self.sample(self.t0, 40)
        self.sample(self.t0 + dt.timedelta(hours=1), 95)
        self.sample(self.t0 + dt.timedelta(hours=2), 100, grid=False, amps=0)
        self.assertEqual(training_status(self.path)["completed_sessions"], 0)
        result = estimate_remaining(self.path, 40, self.policy)
        self.assertEqual(result["source"], "untrained_capacity_current_fallback")
        self.assertEqual(result["hours"], 13)  # 12 bulk + 1 explicit finish.

    def test_learns_full_sessions_including_taper(self):
        self.assertTrue(self.complete_run(0, 12)["completed"])
        self.assertTrue(self.complete_run(2, 13)["completed"])
        self.assertEqual(training_status(self.path)["completed_sessions"], 2)
        result = estimate_remaining(self.path, 40, self.policy)
        self.assertEqual(result["source"], "learned_smartshunt_full_sessions")
        self.assertEqual(result["sessions"], 2)
        self.assertGreaterEqual(result["hours"], 14.3)  # 13h * 1.1.
        self.assertEqual(result["bulk_floor_hours"], 13)

    def test_restart_between_samples_preserves_session(self):
        self.sample(self.t0, 40)
        self.sample(self.t0 + dt.timedelta(hours=1), 95)
        self.assertTrue(training_status(self.path)["session_active"])
        # Every call opens a new DB connection, simulating process restart.
        self.assertTrue(self.sample(
            self.t0 + dt.timedelta(hours=2), 100, grid=False, amps=0,
            full=self.t0 + dt.timedelta(hours=2)
        )["completed"])
        self.assertEqual(training_status(self.path)["completed_sessions"], 1)

    def test_old_full_event_cannot_close_new_charge(self):
        yesterday = self.t0 - dt.timedelta(days=1)
        self.sample(self.t0, 40, full=yesterday)
        self.sample(self.t0 + dt.timedelta(hours=1), 95, full=yesterday)
        final = self.sample(self.t0 + dt.timedelta(hours=2), 100, full=yesterday)
        self.assertFalse(final["completed"])
        self.assertEqual(training_status(self.path)["completed_sessions"], 0)

    def test_grid_break_discards_incomplete_session(self):
        self.sample(self.t0, 40)
        self.sample(self.t0 + dt.timedelta(hours=1), 65, grid=False, amps=0)
        event = self.t0 + dt.timedelta(hours=2)
        self.sample(event, 100, grid=False, amps=0, full=event)
        self.assertEqual(training_status(self.path)["completed_sessions"], 0)

    def test_gap_discards_incomplete_session(self):
        self.sample(self.t0, 40)
        self.sample(self.t0 + dt.timedelta(hours=3), 95)
        event = self.t0 + dt.timedelta(hours=4)
        self.sample(event, 100, grid=False, amps=0, full=event)
        self.assertEqual(training_status(self.path)["completed_sessions"], 0)

    def test_untrained_and_unknown_model_is_explicit(self):
        settings = validate_config({"charge_estimate": {
            "battery_capacity_ah": None, "planning_dc_charge_amps": None
        }})
        result = estimate_remaining(self.path, 55, settings)
        self.assertEqual(result["source"], "unknown")
        self.assertIsNone(result["hours"])

    def test_conservative_cap_and_configurable_learning_disable(self):
        self.complete_run(0, 12)
        self.complete_run(2, 12)
        cap = estimate_remaining(self.path, 40, self.policy, dc_charge_amps=50)
        self.assertEqual(cap["bulk_floor_hours"], 13)
        off = validate_config({"charge_estimate": {
            "learning_enabled": False, "battery_capacity_ah": 600,
            "planning_dc_charge_amps": 30,
        }})
        result = estimate_remaining(self.path, 40, off)
        self.assertEqual(result["source"], "untrained_capacity_current_fallback")

    def test_learning_settings_reject_wrong_types_and_ranges(self):
        with self.assertRaises(ValueError):
            validate_config({"charge_estimate": {"learning_min_sessions": 0}})
        with self.assertRaises(ValueError):
            validate_config({"charge_estimate": {"learning_conservative_multiplier": .8}})
        with self.assertRaises(ValueError):
            validate_config({"charge_estimate": {"learning_min_sessions": 10,
                                                  "learning_history_sessions": 5}})


if __name__ == "__main__":
    unittest.main()
