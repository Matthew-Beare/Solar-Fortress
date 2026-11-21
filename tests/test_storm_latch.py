import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from operator_policy import validate_config
from storm_latch import advance_storm_latch, get_latch_state


class StormLatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = pathlib.Path(self.folder.name) / "latch.sqlite"
        self.zero = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        self.config = validate_config({"storm": {"enabled": True}})
        self.onset = self.zero + dt.timedelta(hours=18)
        self.periods = [{
            "start": self.onset,
            "end": self.onset + dt.timedelta(hours=5),
            "ice_inches": .5,
        }]
        self.trigger = {
            "stage": "charge_due", "charge_recommended": True,
            "severe_immediate": True,
            "event_onset": self.onset.isoformat(),
            "hours_until_onset": 18,
        }
        self.clear = {"stage": "clear", "charge_recommended": False}

    def step(self, t=None, assessment=None, periods=None,
             source_time=None, alerts_clear=None, full=None,
             config=None, soc=50, ac=True):
        clock = t or self.zero
        return advance_storm_latch(
            self.path, clock,
            self.trigger if assessment is None else assessment,
            self.periods if periods is None else periods,
            self.config if config is None else config,
            forecast_source_updated_at=clock if source_time is None else source_time,
            smartshunt_full_at=full, soc=soc,
            ac_available=ac, alerts_clear=alerts_clear,
        )

    def test_activation_and_pass_through_until_12h_after_last_ice(self):
        first = self.step()
        self.assertTrue(first["active"])
        self.assertEqual(first["phase"], "prestorm_charge")
        self.assertEqual(first["requested_mode"], "grid_charging")
        self.assertFalse(first["actuation_permitted_by_this_module"])
        self.assertEqual(first["release_after"],
                         (self.zero + dt.timedelta(hours=35)).isoformat())
        storm = self.step(t=self.zero + dt.timedelta(hours=19),
                          assessment=self.clear, periods=[])
        self.assertEqual(storm["requested_mode"], "pass_through")
        self.assertEqual(storm["phase"], "storm_pass_through")
        not_yet = self.step(t=self.zero + dt.timedelta(hours=34, minutes=59),
                            assessment=self.clear, periods=[], alerts_clear=True)
        self.assertTrue(not_yet["active"])
        release = self.step(t=self.zero + dt.timedelta(hours=35),
                            assessment=self.clear, periods=[], alerts_clear=True)
        self.assertFalse(release["active"])
        self.assertEqual(release["phase"], "released")

    def test_latch_survives_restart_and_stale_forecast(self):
        self.step()
        reopened = get_latch_state(self.path)
        self.assertTrue(reopened["active"])
        late = self.zero + dt.timedelta(hours=40)
        no_weather = advance_storm_latch(
            self.path, late, self.clear, [], self.config,
            forecast_source_updated_at=None, alerts_clear=None, soc=20,
        )
        self.assertTrue(no_weather["active"])
        self.assertEqual(no_weather["requested_mode"], "pass_through")
        self.assertFalse(no_weather["source_fresh"])

    def test_latest_fresh_precipitation_extends_end_not_shortens(self):
        self.step()
        late = self.zero + dt.timedelta(hours=22)
        extensions = [{
            "start": self.zero + dt.timedelta(hours=29),
            "end": self.zero + dt.timedelta(hours=34),
            "ice_inches": .05,
        }]
        extended = self.step(t=late, assessment=self.clear, periods=extensions)
        self.assertTrue(extended["active"])
        self.assertEqual(extended["last_accumulation_end"],
                         (self.zero + dt.timedelta(hours=34)).isoformat())
        self.assertEqual(extended["release_after"],
                         (self.zero + dt.timedelta(hours=46)).isoformat())
        # Later forecasts may downgrade; an old storm latch cannot shrink.
        downgraded = self.step(t=self.zero + dt.timedelta(hours=23),
                               assessment=self.clear, periods=[])
        self.assertEqual(downgraded["release_after"], extended["release_after"])

    def test_release_needs_explicit_alert_clearance_by_default(self):
        self.step()
        late = self.zero + dt.timedelta(hours=40)
        held = self.step(t=late, assessment=self.clear, periods=[],
                         alerts_clear=None)
        self.assertTrue(held["active"])
        held = self.step(t=late, assessment=self.clear, periods=[],
                         alerts_clear=False)
        self.assertTrue(held["active"])
        released = self.step(t=late, assessment=self.clear, periods=[],
                             alerts_clear=True)
        self.assertFalse(released["active"])

    def test_recovery_hours_configurable(self):
        config = validate_config({"storm": {
            "enabled": True, "post_storm_hold_hours": 6,
            "require_alert_clear_for_release": False,
        }})
        result = self.step(config=config)
        self.assertEqual(result["release_after"],
                         (self.zero + dt.timedelta(hours=29)).isoformat())
        release = self.step(t=self.zero + dt.timedelta(hours=29),
                            config=config, assessment=self.clear, periods=[])
        self.assertEqual(release["phase"], "released")

    def test_smartshunt_full_event_starts_configured_balancing_timer(self):
        self.step()
        full_time = self.zero + dt.timedelta(hours=10)
        hold = self.step(t=full_time + dt.timedelta(minutes=15),
                         assessment=self.clear, periods=[],
                         full=full_time, soc=100)
        self.assertEqual(hold["phase"], "post_full_balancing_hold")
        self.assertEqual(hold["requested_mode"], "grid_charging")
        complete = self.step(t=full_time + dt.timedelta(hours=1),
                             assessment=self.clear, periods=[], soc=100)
        self.assertEqual(complete["phase"], "prepared_pass_through")
        self.assertEqual(complete["requested_mode"], "pass_through")

    def test_master_disable_is_explicit_override(self):
        self.step()
        off = validate_config({"storm": {"enabled": False}})
        result = self.step(config=off)
        self.assertFalse(result["active"])
        self.assertEqual(result["phase"], "disabled")
        self.assertFalse(get_latch_state(self.path)["active"])

    def test_no_latch_without_fresh_valid_trigger(self):
        result = advance_storm_latch(
            self.path, self.zero, self.trigger, self.periods,
            self.config, forecast_source_updated_at=None, soc=30
        )
        self.assertFalse(result["active"])
        self.assertEqual(result["phase"], "unlatched")

    def test_already_full_battery_arms_latch_for_pass_through(self):
        assessment = {
            "stage": "ready", "severe_immediate": True,
            "event_onset": self.onset.isoformat(),
            "hours_until_onset": 18,
        }
        result = self.step(assessment=assessment, soc=100)
        self.assertTrue(result["active"])
        self.assertEqual(result["requested_mode"], "pass_through")

    def test_unknown_ac_cannot_authorize_hardware(self):
        result = self.step(ac=False)
        self.assertTrue(result["active"])
        self.assertFalse(result["actuation_permitted_by_this_module"])
        self.assertFalse(result["ac_available"])


if __name__ == "__main__":
    unittest.main()
