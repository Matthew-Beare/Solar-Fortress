import json
import time
from dataclasses import dataclass
from enum import Enum


class OperatingMode(str, Enum):
    BATTERY_FIRST = "battery_first"
    PASS_THROUGH = "pass_through"
    CHARGING = "charging"


@dataclass(frozen=True)
class SystemSnapshot:
    soc: float
    ac_available: bool
    operating_mode: OperatingMode


@dataclass(frozen=True)
class Decision:
    requested_mode: OperatingMode
    reason: str


class SolarFortressController:
    def __init__(
        self,
        grid_pass_through_soc: float,
        grid_charge_soc: float,
        battery_return_soc: float,
        grid_charge_qualification: float,
        stop_grid_charge_qualification: float,
    ):
        if not (
            0 <= grid_charge_soc
            < grid_pass_through_soc
            < battery_return_soc
            <= 100
        ):
            raise ValueError(
                "SOC thresholds must satisfy "
                "0 <= grid_charge < grid_pass_through < battery_return <= 100"
            )

        self.grid_pass_through_soc = grid_pass_through_soc
        self.grid_charge_soc = grid_charge_soc
        self.battery_return_soc = battery_return_soc
        self.grid_charge_qualification = grid_charge_qualification
        self.stop_grid_charge_qualification = stop_grid_charge_qualification

        self._low_soc_since = None
        self._charge_stop_since = None

    @classmethod
    def from_config_file(cls, path: str):
        with open(path, "r") as config_file:
            config = json.load(config_file)

        thresholds = config["soc_thresholds"]
        qualification = config["qualification_seconds"]

        return cls(
            grid_pass_through_soc=thresholds["grid_pass_through"],
            grid_charge_soc=thresholds["grid_charge"],
            battery_return_soc=thresholds["battery_return"],
            grid_charge_qualification=qualification["grid_charge"],
            stop_grid_charge_qualification=qualification["stop_grid_charge"],
        )

    def evaluate(
        self,
        snapshot: SystemSnapshot,
        now: float | None = None,
    ) -> Decision:
        if now is None:
            now = time.monotonic()

        if not 0.0 <= snapshot.soc <= 100.0:
            raise ValueError(f"Invalid SOC: {snapshot.soc}")

        if not snapshot.ac_available:
            self._reset_timers()

            return Decision(
                OperatingMode.BATTERY_FIRST,
                "AC unavailable",
            )

        if snapshot.operating_mode == OperatingMode.CHARGING:
            self._low_soc_since = None

            if snapshot.soc >= self.grid_pass_through_soc:
                if self._charge_stop_since is None:
                    self._charge_stop_since = now

                elapsed = now - self._charge_stop_since

                if elapsed >= self.stop_grid_charge_qualification:
                    self._charge_stop_since = None

                    return Decision(
                        OperatingMode.PASS_THROUGH,
                        "SOC >= pass-through threshold for stop-charge qualification",
                    )

                return Decision(
                    OperatingMode.CHARGING,
                    "Waiting for stop-charge qualification",
                )

            self._charge_stop_since = None

            return Decision(
                OperatingMode.CHARGING,
                "Grid charging active below stop threshold",
            )

        if snapshot.operating_mode == OperatingMode.BATTERY_FIRST:
            self._reset_timers()

            if snapshot.soc <= self.grid_pass_through_soc:
                return Decision(
                    OperatingMode.PASS_THROUGH,
                    "SOC reached grid pass-through threshold",
                )

            return Decision(
                OperatingMode.BATTERY_FIRST,
                "Battery-first operation continues",
            )

        if snapshot.operating_mode == OperatingMode.PASS_THROUGH:
            self._charge_stop_since = None

            if snapshot.soc <= self.grid_charge_soc:
                if self._low_soc_since is None:
                    self._low_soc_since = now

                elapsed = now - self._low_soc_since

                if elapsed >= self.grid_charge_qualification:
                    self._low_soc_since = None

                    return Decision(
                        OperatingMode.CHARGING,
                        "SOC <= grid-charge threshold for charge qualification",
                    )
            else:
                self._low_soc_since = None

            if snapshot.soc >= self.battery_return_soc:
                self._low_soc_since = None

                return Decision(
                    OperatingMode.BATTERY_FIRST,
                    "SOC reached battery-return threshold",
                )

            if self._low_soc_since is not None:
                return Decision(
                    OperatingMode.PASS_THROUGH,
                    "Waiting for grid-charge qualification",
                )

            return Decision(
                OperatingMode.PASS_THROUGH,
                "Grid pass-through continues",
            )

        raise ValueError(
            f"Unsupported operating mode: {snapshot.operating_mode}"
        )

    def _reset_timers(self):
        self._low_soc_since = None
        self._charge_stop_since = None
