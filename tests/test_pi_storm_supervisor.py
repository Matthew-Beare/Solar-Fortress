import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from pi_storm_supervisor import (
    evaluate_local_mode, read_manual_mode, read_manual_override, set_manual_mode,
)

UTC = dt.timezone.utc


class PiStormSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "manual-mode.json"
        self.now = dt.datetime(2026, 11, 21, tzinfo=UTC)

    def plan(self, mode="storm_pass_through", issued=None, expires=None):
        issued = issued or self.now
        expires = expires or self.now + dt.timedelta(minutes=5)
        return {
            "version": 1, "mode": mode,
            "issued_at": issued.isoformat(), "expires_at": expires.isoformat(),
        }

    def test_no_server_means_normal_without_touching_local_state(self):
        result = evaluate_local_mode(self.now)
        self.assertEqual(result["source"], "normal_fallback")
        self.assertIsNone(result["requested_mode"])
        self.assertFalse(self.path.exists())

    def test_fresh_server_plan_may_request_storm_mode(self):
        result = evaluate_local_mode(
            self.now, server_plan=self.plan(),
            server_plan_authenticated=True,
        )
        self.assertEqual(result["requested_mode"], "storm_pass_through")
        self.assertEqual(result["source"], "trusted_server_plan")
        self.assertFalse(result["actuation_permitted"])

    def test_server_goes_to_shit_falls_back_to_existing_soc_policy(self):
        later = self.now + dt.timedelta(minutes=6)
        result = evaluate_local_mode(
            later, server_plan=self.plan(),
            server_plan_authenticated=True,
        )
        self.assertEqual(result["source"], "normal_fallback")
        self.assertIsNone(result["requested_mode"])

    def test_untrusted_plan_does_not_gain_electrical_control(self):
        result = evaluate_local_mode(self.now, server_plan=self.plan())
        self.assertEqual(result["source"], "normal_fallback")

    def test_local_override_survives_server_loss_and_restart(self):
        self.assertTrue(set_manual_mode(self.path, "storm_pass_through", confirmed=True, now=self.now))
        self.assertFalse(set_manual_mode(self.path, "storm_pass_through", confirmed=True, now=self.now))
        stored = read_manual_override(self.path, self.now)
        result = evaluate_local_mode(
            self.now, manual_override=stored,
            server_plan=self.plan("normal"),
            server_plan_authenticated=True,
        )
        self.assertEqual(result["source"], "commander_local_override")
        self.assertEqual(result["requested_mode"], "storm_pass_through")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_commander_can_force_charge_or_restore_normal(self):
        for mode, expected in (("storm_charge", "storm_charge"),
                               ("normal", None), ("automatic", None)):
            set_manual_mode(self.path, mode, confirmed=True, now=self.now)
            result = evaluate_local_mode(self.now, manual_override=read_manual_override(self.path, self.now))
            self.assertEqual(result["requested_mode"], expected)

    def test_requires_local_confirmation(self):
        with self.assertRaises(ValueError):
            set_manual_mode(self.path, "storm_charge", confirmed=False, now=self.now)
        self.assertFalse(self.path.exists())

    def test_server_plan_has_strict_schema_and_expiry(self):
        broken = self.plan()
        broken["untrusted_command"] = "disable_all_safety"
        with self.assertRaises(ValueError):
            evaluate_local_mode(
                self.now, server_plan=broken, server_plan_authenticated=True
            )
        future = self.plan(expires=self.now + dt.timedelta(hours=8))
        with self.assertRaises(ValueError):
            evaluate_local_mode(self.now, server_plan=future,
                                server_plan_authenticated=True)

    def test_all_manual_storm_overrides_have_absolute_expiration(self):
        self.assertTrue(set_manual_mode(
            self.path, "storm_pass_through", confirmed=True,
            now=self.now, duration_hours=2
        ))
        self.assertEqual(read_manual_mode(self.path, self.now), "storm_pass_through")
        before = evaluate_local_mode(
            self.now + dt.timedelta(hours=1),
            manual_override=read_manual_override(
                self.path, self.now + dt.timedelta(hours=1)
            )
        )
        self.assertEqual(before["source"], "commander_local_override")
        after = evaluate_local_mode(
            self.now + dt.timedelta(hours=2),
            manual_override=read_manual_override(
                self.path, self.now + dt.timedelta(hours=2)
            )
        )
        self.assertEqual(after["source"], "normal_fallback")
        self.assertIsNone(after["requested_mode"])
        # Expiration is evaluated on read, not written continuously.
        stored = self.path.read_bytes()
        read_manual_override(self.path, self.now + dt.timedelta(days=1))
        self.assertEqual(self.path.read_bytes(), stored)

    def test_renew_requires_explicit_action_and_gets_new_expiry(self):
        set_manual_mode(self.path, "storm_charge", confirmed=True,
                        now=self.now, duration_hours=2)
        original = self.path.read_bytes()
        changed = set_manual_mode(
            self.path, "storm_charge", confirmed=True,
            now=self.now + dt.timedelta(minutes=30), duration_hours=2
        )
        self.assertFalse(changed)
        self.assertEqual(original, self.path.read_bytes())
        changed = set_manual_mode(
            self.path, "storm_charge", confirmed=True,
            now=self.now + dt.timedelta(minutes=30), duration_hours=2, renew=True
        )
        self.assertTrue(changed)
        self.assertNotEqual(original, self.path.read_bytes())
        expires_at = read_manual_override(self.path, self.now)["expires_at"]
        self.assertEqual(dt.datetime.fromisoformat(expires_at),
                         self.now + dt.timedelta(hours=2, minutes=30))

    def test_raw_manual_mode_without_expiry_cannot_stick(self):
        result = evaluate_local_mode(self.now, manual_mode="storm_charge")
        self.assertEqual(result["source"], "normal_fallback")

    def test_commander_full_charge_overrides_server_but_not_hardware(self):
        request = {"phase": "charging", "request_mode": "grid_charging"}
        result = evaluate_local_mode(
            self.now, server_plan=self.plan(), server_plan_authenticated=True,
            commander_charge=request
        )
        self.assertEqual(result["source"], "commander_full_charge")
        self.assertEqual(result["requested_mode"], "grid_charging")
        self.assertFalse(result["actuation_permitted"])
        done = evaluate_local_mode(
            self.now, commander_charge={"phase": "expired", "request_mode": None}
        )
        self.assertEqual(done["source"], "normal_fallback")

    def test_timeout_validation(self):
        with self.assertRaises(ValueError):
            evaluate_local_mode(self.now, grace_seconds=0)
        with self.assertRaises(ValueError):
            evaluate_local_mode(self.now, grace_seconds=3601)


if __name__ == "__main__":
    unittest.main()
