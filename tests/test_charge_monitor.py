import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from charge_monitor import decode_status, observe_once, parse_pi_time
from charge_learning import training_status
from operator_policy import validate_config


class ChargeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "learner.sqlite"
        self.now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        self.policy = validate_config({
            "charge_estimate": {"learning_max_gap_minutes": 120}
        })

    def status(self, now, soc=40, grid=True, age=100000, amps=25):
        return {
            "timestamp": now.isoformat(), "victron_connected": True,
            "soc": soc, "grid_charging": grid, "ac_present": True,
            "battery_current": amps, "last_full_charge_seconds": age,
        }

    def test_eastern_status_timestamp(self):
        self.assertEqual(parse_pi_time("2026-11-21 03:00:00 AM EST"),
                         dt.datetime(2026, 11, 21, 8, tzinfo=dt.timezone.utc))
        self.assertEqual(parse_pi_time("2026-07-21 03:00:00 AM EDT"),
                         dt.datetime(2026, 7, 21, 7, tzinfo=dt.timezone.utc))

    def test_rejects_stale_bad_sources(self):
        with self.assertRaises(ValueError):
            decode_status(self.status(self.now), now=self.now + dt.timedelta(minutes=5))
        bad = self.status(self.now)
        bad["victron_connected"] = False
        with self.assertRaises(ValueError):
            decode_status(bad, now=self.now)
        bad = self.status(self.now)
        bad["ac_present"] = False
        with self.assertRaises(ValueError):
            decode_status(bad, now=self.now)

    def test_full_event_age_derivation(self):
        snap = decode_status(self.status(self.now, age=3600), now=self.now)
        self.assertEqual(snap["smartshunt_full_at"],
                         self.now - dt.timedelta(hours=1))

    def test_real_status_samples_automatically_complete_learning(self):
        first = observe_once(
            self.path, self.policy,
            self.status(self.now, soc=40), now=self.now
        )
        self.assertEqual(first["status"], "session_started")
        mid = self.now + dt.timedelta(hours=1)
        observe_once(self.path, self.policy,
                     self.status(mid, soc=97), now=mid)
        full = self.now + dt.timedelta(hours=2)
        completed = observe_once(
            self.path, self.policy,
            self.status(full, soc=100, grid=False, age=0, amps=0),
            now=full
        )
        self.assertTrue(completed["completed"])
        self.assertEqual(training_status(self.path)["completed_sessions"], 1)


if __name__ == "__main__":
    unittest.main()
