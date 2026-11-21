import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from charge_learning import record_observation
from operator_cycle import evaluate_cycle
from operator_policy import validate_config
from storm_latch import get_latch_state


class OperatorCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = pathlib.Path(self.tmp.name)
        self.training = folder / "learning.sqlite"
        self.latch = folder / "latch.sqlite"
        self.now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        self.config = validate_config({
            "storm": {"enabled": True, "charge_margin_hours": 4},
            "charge_estimate": {
                "battery_capacity_ah": None,
                "planning_dc_charge_amps": None,
                "learning_min_sessions": 2,
                "learning_max_gap_minutes": 120,
            }
        })
        self.event = {
            "start": (self.now + dt.timedelta(hours=16)).isoformat(),
            "end": (self.now + dt.timedelta(hours=18)).isoformat(),
            "ice_inches": 0.3,
        }

    def status(self, clock, soc=40):
        return {
            "timestamp": clock.isoformat(), "victron_connected": True,
            "soc": soc, "battery_current": 0,
            "grid_charging": False, "ac_present": True,
            "last_full_charge_seconds": 86400,
        }

    def train(self):
        for day in (5, 3):
            begin = self.now - dt.timedelta(days=day)
            for hour in range(12):
                pct = min(95, 40 + hour * 6)
                record_observation(
                    self.training, begin + dt.timedelta(hours=hour),
                    soc=pct, grid_charging=True, battery_current_amps=25,
                    config=self.config
                )
            end = begin + dt.timedelta(hours=12)
            r = record_observation(
                self.training, end, soc=100, grid_charging=False,
                battery_current_amps=0, smartshunt_full_at=end,
                config=self.config
            )
            self.assertTrue(r["completed"])

    def cycle(self, clock=None, forecast=None, alerts=None, soc=40):
        now = clock or self.now
        weather = {"periods": [self.event] if forecast is None else forecast,
                   "source_update_time": now.isoformat()}
        return evaluate_cycle(
            now, self.status(now, soc=soc), weather, [],
            {"checked_at": now.isoformat(), "alerts_clear": False}
            if alerts is None else alerts,
            self.config, learning_db=self.training, latch_db=self.latch
        )

    def test_real_learned_history_arms_deadline_without_manual_amps(self):
        self.train()
        result = self.cycle()
        eta = result["full_charge_estimate"]
        self.assertEqual(eta["source"], "learned_smartshunt_full_sessions")
        self.assertEqual(eta["sessions"], 2)
        self.assertGreater(eta["hours"], 12)
        self.assertEqual(result["weather_assessment"]["trigger"],
                         "charging_deadline")
        self.assertTrue(result["persistent_storm_latch"]["active"])
        self.assertEqual(result["persistent_storm_latch"]["requested_mode"],
                         "grid_charging")
        self.assertFalse(result["actuation_permitted"])

    def test_latch_pass_through_and_configurable_end_hold_in_full_cycle(self):
        self.train()
        self.cycle()
        midway = self.cycle(clock=self.now + dt.timedelta(hours=20),
                            forecast=[], soc=100)
        self.assertEqual(midway["persistent_storm_latch"]["phase"],
                         "storm_pass_through")
        self.assertTrue(get_latch_state(self.latch)["active"])
        # Storm accumulation ends at +18h, hold defaults to +12h.
        complete_at = self.now + dt.timedelta(hours=30)
        done = self.cycle(clock=complete_at, forecast=[],
                          alerts={"checked_at": complete_at.isoformat(),
                                  "alerts_clear": True}, soc=100)
        self.assertEqual(done["persistent_storm_latch"]["phase"], "released")
        self.assertFalse(get_latch_state(self.latch)["active"])

    def test_no_learned_model_is_explicitly_unknown(self):
        result = self.cycle()
        self.assertEqual(result["full_charge_estimate"]["source"], "unknown")
        self.assertEqual(result["weather_assessment"]["stage"],
                         "needs_fresh_telemetry")
        self.assertFalse(result["persistent_storm_latch"]["active"])


if __name__ == "__main__":
    unittest.main()
