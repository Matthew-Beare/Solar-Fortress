import json
import math
import time
from dataclasses import dataclass
from enum import Enum


class GridMode(str, Enum):
    BATTERY_FIRST = "battery_first"
    PASS_THROUGH = "pass_through"
    GRID_CHARGING = "grid_charging"

    # Temporary compatibility alias for existing hardware code.
    CHARGING = "grid_charging"


# Temporary compatibility alias.
OperatingMode = GridMode


@dataclass(frozen=True)
class SystemSnapshot:
    soc: float
    ac_available: bool
    operating_mode: GridMode


@dataclass(frozen=True)
class Decision:
    requested_mode: GridMode
    reason: str


class SolarFortressController:
    def __init__(
        self,
        grid_pass_through_soc: float,
        grid_charge_soc: float,
        battery_return_soc: float,
        pass_through_qualification: float,
        grid_charge_qualification: float,
        stop_grid_charge_qualification: float,
    ):
        thresholds = (grid_charge_soc, grid_pass_through_soc, battery_return_soc)
        qualifications = (
            pass_through_qualification,
            grid_charge_qualification,
            stop_grid_charge_qualification,
        )
        for value in thresholds + qualifications:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("SOC thresholds and qualifications must be finite numbers")
        if any(value < 0 for value in qualifications):
            raise ValueError("Qualification durations must be non-negative")

        if not (
            0 <= grid_charge_soc
            < grid_pass_through_soc
            < battery_return_soc
            <= 100
        ):
            raise ValueError(
                "SOC thresholds must satisfy "
                "0 <= grid_charge < grid_pass_through "
                "< battery_return <= 100"
            )

        self.grid_pass_through_soc = grid_pass_through_soc
        self.grid_charge_soc = grid_charge_soc
        self.battery_return_soc = battery_return_soc

        self.pass_through_qualification = (
            pass_through_qualification
        )
        self.grid_charge_qualification = (
            grid_charge_qualification
        )
        self.stop_grid_charge_qualification = (
            stop_grid_charge_qualification
        )

        self._pass_through_since = None
        self._low_soc_since = None
        self._charge_stop_since = None

    @classmethod
    def from_config_file(cls, path: str):
        with open(path, "r") as config_file:
            config = json.load(config_file)

        thresholds = config["soc_thresholds"]
        qualification = config["qualification_seconds"]

        return cls(
            grid_pass_through_soc=
                thresholds["grid_pass_through"],

            grid_charge_soc=
                thresholds["grid_charge"],

            battery_return_soc=
                thresholds["battery_return"],

            pass_through_qualification=
                qualification["pass_through"],

            grid_charge_qualification=
                qualification["grid_charge"],

            stop_grid_charge_qualification=
                qualification["stop_grid_charge"],
        )

    def evaluate(
        self,
        snapshot: SystemSnapshot,
        now: float | None = None,
    ) -> Decision:
        if now is None:
            now = time.monotonic()

        if not 0.0 <= snapshot.soc <= 100.0:
            raise ValueError(
                f"Invalid SOC: {snapshot.soc}"
            )

        mode = snapshot.operating_mode

        #
        # BATTERY FIRST
        #
        if mode == GridMode.BATTERY_FIRST:
            self._low_soc_since = None
            self._charge_stop_since = None

            if snapshot.soc <= self.grid_pass_through_soc:
                if self._pass_through_since is None:
                    self._pass_through_since = now

                elapsed = now - self._pass_through_since

                if (
                    elapsed
                    >= self.pass_through_qualification
                ):
                    self._pass_through_since = None

                    return Decision(
                        GridMode.PASS_THROUGH,
                        "SOC <= pass-through threshold "
                        "for 60-second qualification",
                    )

                return Decision(
                    GridMode.BATTERY_FIRST,
                    "Waiting for pass-through qualification",
                )

            self._pass_through_since = None

            return Decision(
                GridMode.BATTERY_FIRST,
                "Battery-first operation continues",
            )

        #
        # PASS THROUGH
        #
        if mode == GridMode.PASS_THROUGH:
            self._pass_through_since = None
            self._charge_stop_since = None

            if snapshot.soc >= self.battery_return_soc:
                self._low_soc_since = None

                return Decision(
                    GridMode.BATTERY_FIRST,
                    "SOC reached battery-return threshold",
                )

            if snapshot.soc <= self.grid_charge_soc:
                if self._low_soc_since is None:
                    self._low_soc_since = now

                elapsed = now - self._low_soc_since

                if (
                    elapsed
                    >= self.grid_charge_qualification
                ):
                    self._low_soc_since = None

                    return Decision(
                        GridMode.GRID_CHARGING,
                        "SOC <= grid-charge threshold "
                        "for 60-second qualification",
                    )

                return Decision(
                    GridMode.PASS_THROUGH,
                    "Waiting for grid-charge qualification",
                )

            self._low_soc_since = None

            return Decision(
                GridMode.PASS_THROUGH,
                "Grid pass-through continues",
            )

        #
        # GRID CHARGING
        #
        if mode == GridMode.GRID_CHARGING:
            self._pass_through_since = None
            self._low_soc_since = None

            if snapshot.soc >= self.grid_pass_through_soc:
                if self._charge_stop_since is None:
                    self._charge_stop_since = now

                elapsed = now - self._charge_stop_since

                if (
                    elapsed
                    >= self.stop_grid_charge_qualification
                ):
                    self._charge_stop_since = None

                    return Decision(
                        GridMode.PASS_THROUGH,
                        "SOC >= pass-through threshold "
                        "for 60-second stop-charge "
                        "qualification",
                    )

                return Decision(
                    GridMode.GRID_CHARGING,
                    "Waiting for stop-charge qualification",
                )

            self._charge_stop_since = None

            return Decision(
                GridMode.GRID_CHARGING,
                "Grid charging continues",
            )

        raise ValueError(
            f"Unsupported grid mode: {mode}"
        )

    def _reset_timers(self):
        self._pass_through_since = None
        self._low_soc_since = None
        self._charge_stop_since = None
