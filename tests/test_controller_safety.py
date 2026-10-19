"""Boundary and invalid-configuration regressions. No Victron hardware."""
import unittest

from controller import GridMode, SolarFortressController, SystemSnapshot


def build(**overrides):
    opts = {
        "grid_pass_through_soc": 30.0,
        "grid_charge_soc": 20.0,
        "battery_return_soc": 35.0,
        "pass_through_qualification": 60.0,
        "grid_charge_qualification": 60.0,
        "stop_grid_charge_qualification": 60.0,
    }
    opts.update(overrides)
    return SolarFortressController(**opts)


class ControllerBoundaryTests(unittest.TestCase):
    def test_bad_thresholds_rejected(self):
        for bad in (-0.1, float("nan"), float("inf"), "30", True):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                build(grid_pass_through_soc=bad)

    def test_bad_qualifications_rejected(self):
        for bad in (-1, float("nan"), float("inf"), "60", True):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                build(grid_charge_qualification=bad)

    def test_threshold_order_is_enforced(self):
        with self.assertRaises(ValueError):
            build(grid_charge_soc=30.0)
        with self.assertRaises(ValueError):
            build(battery_return_soc=30.0)

    def test_30_percent_change_qualifies_only_after_60_seconds(self):
        ctl = build()
        state = SystemSnapshot(soc=30.0, ac_available=True, operating_mode=GridMode.BATTERY_FIRST)
        self.assertEqual(GridMode.BATTERY_FIRST, ctl.evaluate(state, now=0.0).requested_mode)
        self.assertEqual(GridMode.BATTERY_FIRST, ctl.evaluate(state, now=59.999).requested_mode)
        self.assertEqual(GridMode.PASS_THROUGH, ctl.evaluate(state, now=60.0).requested_mode)

    def test_zero_qualification_is_configurable(self):
        ctl = build(pass_through_qualification=0)
        state = SystemSnapshot(soc=30.0, ac_available=True, operating_mode=GridMode.BATTERY_FIRST)
        self.assertEqual(GridMode.PASS_THROUGH, ctl.evaluate(state, now=0.0).requested_mode)

    def test_bad_soc_fails_safely(self):
        ctl = build()
        for bad in (float("nan"), float("inf"), -0.01, 100.01):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                ctl.evaluate(SystemSnapshot(soc=bad, ac_available=True, operating_mode=GridMode.BATTERY_FIRST), now=0)


if __name__ == "__main__":
    unittest.main()
