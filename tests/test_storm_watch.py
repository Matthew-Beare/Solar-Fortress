import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from operator_policy import validate_config
from storm_watch import assess_storm_watch, qualifying_events, severe_now_events


class StormWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 21, 0, tzinfo=dt.timezone.utc)
        self.policy = validate_config({"storm": {"enabled": True}})
        self.ice = [{
            "start": self.now + dt.timedelta(hours=40),
            "end": self.now + dt.timedelta(hours=42),
            "ice_inches": 0.35,
        }]

    def assess(self, now=None, periods=None, history=None, soc=30, amps=30,
               config=None, forecast_updated=None):
        checked = now or self.now
        return assess_storm_watch(
            checked,
            self.ice if periods is None else periods,
            [] if history is None else history,
            self.policy if config is None else config,
            soc=soc,
            capacity_ah=600,
            actual_dc_charge_amps=amps,
            forecast_source_updated_at=checked if forecast_updated is None else forecast_updated,
        )

    def test_configurable_watch_is_not_required_to_trigger_emergency(self):
        urgent = [{
            "start": self.now + dt.timedelta(hours=18),
            "end": self.now + dt.timedelta(hours=20),
            "ice_inches": 0.5,
        }]
        policy = validate_config({
            "storm": {"enabled": True, "watch_hours": 1, "lookahead_hours": 12}
        })
        result = self.assess(periods=urgent, history=[], config=policy)
        self.assertEqual(result["stage"], "charge_due")
        self.assertEqual(result["trigger"], "severe_forecast")
        self.assertTrue(result["severe_immediate"])
        self.assertFalse(result["confirmed_by_history"])
        self.assertEqual(result["hours_until_onset"], 18)

    def test_severe_threshold_and_horizon_are_editable(self):
        imminent = [{
            "start": self.now + dt.timedelta(hours=21),
            "end": self.now + dt.timedelta(hours=23),
            "ice_inches": 0.6,
        }]
        policy = validate_config({"storm": {
            "enabled": True, "watch_hours": 1,
            "ice": {"immediate_threshold_inches": 0.8}
        }})
        self.assertFalse(severe_now_events(self.now, imminent, policy))
        policy = validate_config({"storm": {
            "enabled": True, "watch_hours": 1, "immediate_horizon_hours": 12
        }})
        self.assertFalse(severe_now_events(self.now, imminent, policy))
        self.assertEqual(self.assess(periods=imminent, config=policy)["stage"], "clear")

    def test_twelve_hour_charge_four_hour_buffer_starts_at_sixteen(self):
        storm = [{
            "start": self.now + dt.timedelta(hours=16),
            "end": self.now + dt.timedelta(hours=18),
            "ice_inches": 0.3,
        }]
        policy = validate_config({"storm": {"enabled": True, "charge_margin_hours": 4}})
        result = self.assess(periods=storm, soc=40, amps=30, config=policy)
        self.assertEqual(result["estimated_bulk_charge_hours"], 12)
        self.assertEqual(result["estimated_charge_hours"], 12)
        self.assertEqual(result["charge_planning_hours"], 16)
        self.assertEqual(result["trigger"], "charging_deadline")

    def test_configured_model_inputs_are_not_live_measurements(self):
        storm = [{
            "start": self.now + dt.timedelta(hours=18),
            "end": self.now + dt.timedelta(hours=20),
            "ice_inches": 0.3,
        }]
        policy = validate_config({
            "storm": {"enabled": True, "charge_margin_hours": 4},
            "charge_estimate": {
                "battery_capacity_ah": 600,
                "planning_dc_charge_amps": 30,
                "finishing_allowance_minutes": 60,
            }
        })
        result = self.assess(periods=storm, soc=40, amps=None, config=policy)
        self.assertEqual(result["estimated_bulk_charge_hours"], 12)
        self.assertEqual(result["estimated_charge_hours"], 13)
        self.assertEqual(result["charge_planning_hours"], 17)
        self.assertEqual(result["charging_estimate_source"], "includes_operator_estimate")
        # Limiting the plan to a conservative 20A matters even if a caller
        # supplies a faster hypothetical charging rate.
        slower = validate_config({
            "storm": {"enabled": True, "charge_margin_hours": 4},
            "charge_estimate": {
                "battery_capacity_ah": 600,
                "planning_dc_charge_amps": 20,
                "finishing_allowance_minutes": 0,
            }
        })
        cap = self.assess(periods=storm, soc=40, amps=30, config=slower)
        self.assertEqual(cap["estimated_bulk_charge_hours"], 18)
        self.assertTrue(cap["charge_recommended"])

    def test_four_hours_or_eight_hours_safety_margin_changes_deadline(self):
        imminent = [{
            "start": self.now + dt.timedelta(hours=22),
            "end": self.now + dt.timedelta(hours=23),
            "ice_inches": 0.3,
        }]
        base = validate_config({"storm": {"enabled": True, "charge_margin_hours": 4}})
        more = validate_config({"storm": {"enabled": True, "charge_margin_hours": 8}})
        result4 = self.assess(periods=imminent, config=base)
        result8 = self.assess(periods=imminent, config=more)
        self.assertEqual(result4["estimated_bulk_charge_hours"], 14)
        self.assertEqual(result4["charge_planning_hours"], 18)
        self.assertFalse(result4["charge_recommended"])
        self.assertEqual(result8["charge_planning_hours"], 22)
        self.assertTrue(result8["charge_recommended"])
        self.assertEqual(result8["trigger"], "charging_deadline")

    def test_new_battery_soc_recomputes_time_at_every_evaluation(self):
        event = [{
            "start": self.now + dt.timedelta(hours=16),
            "end": self.now + dt.timedelta(hours=18),
            "ice_inches": 0.3,
        }]
        low = self.assess(periods=event, soc=30)
        high = self.assess(periods=event, soc=70)
        self.assertEqual(low["charge_planning_hours"], 18)
        self.assertEqual(high["charge_planning_hours"], 10)
        self.assertTrue(low["charge_recommended"])
        self.assertFalse(high["charge_recommended"])

    def test_latest_forecast_overrides_older_alarm(self):
        old = {"checked_at": self.now - dt.timedelta(hours=24),
               "source_update_time": (self.now - dt.timedelta(hours=24)).isoformat(),
               "periods": self.ice}
        downgraded = [{
            "start": self.now + dt.timedelta(hours=15),
            "end": self.now + dt.timedelta(hours=18),
            "ice_inches": 0.05,
        }]
        result = self.assess(periods=downgraded, history=[old])
        self.assertEqual(result["stage"], "clear")
        self.assertFalse(result["charge_recommended"])

    def test_48h_watch_is_information_not_an_activation_gate(self):
        initial = self.assess()
        self.assertEqual(initial["stage"], "watch")
        self.assertEqual(initial["hours_until_onset"], 40)
        self.assertFalse(initial["charge_recommended"])
        self.assertEqual(initial["charge_planning_hours"], 18)

    def test_corroborating_24h_history_can_recommend_earlier_charging(self):
        old = {"checked_at": self.now, "periods": self.ice,
               "source_update_time": self.now.isoformat()}
        checked = self.now + dt.timedelta(hours=24)
        result = self.assess(now=checked, history=[old])
        self.assertEqual(result["stage"], "charge_due")
        self.assertTrue(result["confirmed_by_history"])

    def test_replaying_old_cached_forecast_is_stale_not_confirmation(self):
        checked = self.now + dt.timedelta(hours=24)
        old = {"checked_at": self.now, "periods": self.ice,
               "source_update_time": self.now.isoformat()}
        result = self.assess(now=checked, history=[old], forecast_updated=self.now)
        self.assertEqual(result["stage"], "stale_forecast")
        self.assertFalse(result["charge_recommended"])

    def test_deadline_ignores_missing_historical_confirmation(self):
        checked = self.now + dt.timedelta(hours=22)
        result = self.assess(now=checked)
        self.assertTrue(result["charge_recommended"])
        self.assertEqual(result["trigger"], "charging_deadline")
        self.assertTrue(result["deadline_override"])

    def test_no_forecast_or_missing_battery_does_not_unlatch_controller(self):
        absent = self.assess(periods=[])
        self.assertEqual(absent["stage"], "clear")
        self.assertIn("must not be cleared", absent["note"])
        missing = self.assess(soc=None)
        self.assertEqual(missing["stage"], "needs_fresh_telemetry")
        self.assertFalse(missing["charge_recommended"])

    def test_full_battery_needs_no_further_charging(self):
        result = self.assess(soc=100)
        self.assertEqual(result["stage"], "ready")
        self.assertFalse(result["charge_recommended"])

    def test_very_distant_storm_ignored(self):
        later = [{
            "start": self.now + dt.timedelta(hours=50),
            "end": self.now + dt.timedelta(hours=52),
            "ice_inches": 0.6,
        }]
        self.assertEqual(self.assess(periods=later)["stage"], "clear")

    def test_accumulations_across_forecast_intervals(self):
        intervals = [
            {"start": self.now + dt.timedelta(hours=35),
             "end": self.now + dt.timedelta(hours=37), "ice_inches": 0.15},
            {"start": self.now + dt.timedelta(hours=40),
             "end": self.now + dt.timedelta(hours=42), "ice_inches": 0.12},
        ]
        result = self.assess(periods=intervals)
        self.assertEqual(result["stage"], "watch")
        self.assertIn("ice", result["reasons"])

    def test_overlapping_ice_periods_do_not_double_count(self):
        intervals = [
            {"start": self.now + dt.timedelta(hours=20),
             "end": self.now + dt.timedelta(hours=26), "ice_inches": .15},
            {"start": self.now + dt.timedelta(hours=23),
             "end": self.now + dt.timedelta(hours=28), "ice_inches": .15},
        ]
        with self.assertRaises(ValueError):
            qualifying_events(self.now, intervals, self.policy)
        with self.assertRaises(ValueError):
            severe_now_events(self.now, intervals, self.policy)

    def test_user_can_set_eight_hour_watch_and_margin(self):
        policy = validate_config({"storm": {
            "enabled": True, "watch_hours": 8, "charge_margin_hours": 8,
            "confirmation_hours": 24,
        }})
        self.assertEqual(policy["storm"]["watch_hours"], 8)
        self.assertEqual(policy["storm"]["charge_margin_hours"], 8)

    def test_explicit_master_disable_prevents_charge_recommendation(self):
        off = validate_config({"storm": {"enabled": False}})
        imminent = [{
            "start": self.now + dt.timedelta(hours=1),
            "end": self.now + dt.timedelta(hours=3),
            "ice_inches": 0.7,
        }]
        self.assertEqual(self.assess(periods=imminent, config=off)["stage"], "disabled")


if __name__ == "__main__":
    unittest.main()
