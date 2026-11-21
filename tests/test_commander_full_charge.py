import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from commander_full_charge import (
    begin_full_charge, cancel_full_charge, describe_full_charge,
    update_from_smartshunt
)
from operator_policy import validate_config


class CommanderFullChargeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path = pathlib.Path(self.root.name) / "commander.json"
        self.now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        self.policy = validate_config({})

    def begin(self, hours=8):
        return begin_full_charge(self.path, self.now, hours,
                                 self.policy, confirmed=True)

    def test_no_login_request_uses_hold_after_real_smartshunt_full(self):
        state = self.begin(8)
        self.assertEqual(state["phase"], "charging")
        self.assertEqual(state["hold_hours"], 8)
        self.assertEqual(state["request_mode"], "grid_charging")
        self.assertFalse(state["actuation_permitted"])
        before = self.now - dt.timedelta(days=1)
        unchanged = update_from_smartshunt(self.path, self.now + dt.timedelta(hours=3), before)
        self.assertEqual(unchanged["phase"], "charging")
        completed_at = self.now + dt.timedelta(hours=6)
        result = update_from_smartshunt(self.path, completed_at, completed_at)
        self.assertEqual(result["phase"], "holding_full")
        self.assertEqual(result["request_mode"], "full_charge_hold")
        self.assertEqual(result["hold_expires_at"],
                         (completed_at + dt.timedelta(hours=8)).isoformat())
        self.assertEqual(describe_full_charge(self.path, completed_at +
                         dt.timedelta(hours=7))["phase"], "holding_full")
        self.assertEqual(describe_full_charge(self.path, completed_at +
                         dt.timedelta(hours=8))["phase"], "completed")
        self.assertIsNone(describe_full_charge(self.path, completed_at +
                          dt.timedelta(hours=9))["request_mode"])

    def test_missing_full_event_expires_request(self):
        self.begin(24)
        deadline = self.now + dt.timedelta(hours=72)
        result = describe_full_charge(self.path, deadline)
        self.assertEqual(result["phase"], "expired")
        self.assertIsNone(result["request_mode"])
        self.assertEqual(update_from_smartshunt(self.path, deadline, None)["phase"], "expired")

    def test_cancel_stops_while_waiting_or_holding(self):
        self.begin(12)
        done = cancel_full_charge(self.path, self.now, confirmed=True)
        self.assertEqual(done["phase"], "cancelled")
        self.assertIsNone(describe_full_charge(self.path, self.now)["request_mode"])
        self.begin(12)
        full = self.now + dt.timedelta(hours=1)
        update_from_smartshunt(self.path, full, full)
        cancel_full_charge(self.path, full + dt.timedelta(hours=2), confirmed=True)
        self.assertEqual(describe_full_charge(self.path, full +
                         dt.timedelta(hours=3))["phase"], "cancelled")

    def test_repeat_request_does_not_renew_without_explicit_replacement(self):
        self.begin(8)
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.begin(8)
        self.assertEqual(self.path.read_bytes(), original)
        updated = begin_full_charge(
            self.path, self.now + dt.timedelta(minutes=15), 12,
            self.policy, confirmed=True, replace_existing=True
        )
        self.assertEqual(updated["hold_hours"], 12)
        self.assertNotEqual(original, self.path.read_bytes())

    def test_cancel_and_request_need_confirmation(self):
        with self.assertRaises(ValueError):
            begin_full_charge(self.path, self.now, 5, self.policy, confirmed=False)
        with self.assertRaises(ValueError):
            cancel_full_charge(self.path, self.now, confirmed=False)

    def test_bounds_and_disabled_control(self):
        with self.assertRaises(ValueError):
            self.begin(169)
        with self.assertRaises(ValueError):
            self.begin(-1)
        with self.assertRaises(ValueError):
            self.begin(2.5)
        off = validate_config({"commander_full_charge": {"enabled": False}})
        with self.assertRaises(ValueError):
            begin_full_charge(self.path, self.now, 8, off, confirmed=True)

    def test_zero_hold_expires_immediately_after_full(self):
        self.begin(0)
        full = self.now + dt.timedelta(hours=4)
        result = update_from_smartshunt(self.path, full, full)
        self.assertEqual(result["phase"], "completed")
        self.assertIsNone(result["request_mode"])

    def test_reboot_read_does_not_modify_card(self):
        self.begin(3)
        content = self.path.read_bytes()
        for hours in range(1, 10):
            describe_full_charge(self.path, self.now + dt.timedelta(hours=hours))
        self.assertEqual(content, self.path.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_max_and_default_hold_are_admin_editable(self):
        p = validate_config({"commander_full_charge": {
            "default_hold_hours": 48, "max_hold_hours": 72,
            "charge_attempt_timeout_hours": 24
        }})
        result = begin_full_charge(self.path, self.now, 72, p, confirmed=True)
        self.assertEqual(result["hold_hours"], 72)
        self.assertEqual(result["charge_attempt_expires_at"],
                         (self.now + dt.timedelta(hours=24)).isoformat())
        with self.assertRaises(ValueError):
            validate_config({"commander_full_charge": {
                "default_hold_hours": 80, "max_hold_hours": 72
            }})


if __name__ == "__main__":
    unittest.main()
