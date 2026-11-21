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

    def test_manual_request_keeps_charging_after_old_72h_cutoff(self):
        issued = self.begin(24)
        self.assertEqual(issued["request_mode"], "grid_charging")
        # Commander is requesting charge NOW, not a promise to reach full by 72h.
        after_old_deadline = self.now + dt.timedelta(hours=100)
        still_charging = describe_full_charge(self.path, after_old_deadline)
        self.assertEqual(still_charging["phase"], "charging")
        self.assertEqual(still_charging["request_mode"], "grid_charging")
        # Every override still expires so a lost Pi/SmartShunt event cannot stick.
        failsafe = self.now + dt.timedelta(hours=168)
        expired = describe_full_charge(self.path, failsafe)
        self.assertEqual(expired["phase"], "expired")
        self.assertIsNone(expired["request_mode"])
        self.assertEqual(update_from_smartshunt(self.path, failsafe, None)["phase"], "expired")

    def test_full_event_near_failsafe_starts_complete_selected_hold(self):
        self.begin(24)
        full = self.now + dt.timedelta(hours=160)
        result = update_from_smartshunt(self.path, full, full)
        self.assertEqual(result["phase"], "holding_full")
        self.assertEqual(result["override_expires_at"],
                         (full + dt.timedelta(hours=24)).isoformat())
        self.assertEqual(describe_full_charge(self.path, self.now +
                         dt.timedelta(hours=170))["phase"], "holding_full")
        self.assertEqual(describe_full_charge(self.path, full +
                         dt.timedelta(hours=24))["phase"], "completed")

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

    def test_failsafe_lifetime_is_independent_of_post_full_hold(self):
        custom = validate_config({"commander_full_charge": {
            "override_expiry_hours": 336,
            "default_hold_hours": 8,
            "max_hold_hours": 96,
        }})
        request = begin_full_charge(
            self.path, self.now, 8, custom, confirmed=True
        )
        self.assertEqual(request["override_expires_at"],
                         (self.now + dt.timedelta(hours=336)).isoformat())
        full = self.now + dt.timedelta(hours=6)
        result = update_from_smartshunt(self.path, full, full)
        self.assertEqual(result["override_expires_at"],
                         (full + dt.timedelta(hours=8)).isoformat())
        self.assertEqual(describe_full_charge(
            self.path, full + dt.timedelta(hours=8)
        )["phase"], "completed")
        with self.assertRaises(ValueError):
            validate_config({"commander_full_charge": {
                "override_expiry_hours": 721
            }})

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
            "override_expiry_hours": 240
        }})
        result = begin_full_charge(self.path, self.now, 72, p, confirmed=True)
        self.assertEqual(result["hold_hours"], 72)
        self.assertEqual(result["override_expires_at"],
                         (self.now + dt.timedelta(hours=240)).isoformat())
        with self.assertRaises(ValueError):
            validate_config({"commander_full_charge": {
                "default_hold_hours": 80, "max_hold_hours": 72
            }})


if __name__ == "__main__":
    unittest.main()
