import asyncio
import dbus
import glob
import os
import subprocess
import sys
import time

from controller import OperatingMode, SystemSnapshot


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

VENDOR_PATH = os.path.join(PROJECT_ROOT, "vendor")

if VENDOR_PATH not in sys.path:
    sys.path.insert(0, VENDOR_PATH)

from victron_vebus_mk3_protocol import VictronMK3, Handler


FLAGS0_SETTING_ID = 0
DISABLE_CHARGE_BIT = 6


class QuietHandler(Handler):
    def on_response(self, response):
        pass

    def on_idle(self):
        pass

    def on_fault(self, fault):
        print(f"MK3 fault: {fault}")


class VictronHardware:
    def __init__(self):
        self.bus = self._get_bus()

        self.vebus_service = self._find_service(
            prefix="com.victronenergy.vebus.",
            product_contains="MultiPlus-II",
        )

        self.battery_service = self._find_service(
            prefix="com.victronenergy.battery.",
            product_contains="SmartShunt",
        )

        self.mk3_path = self._find_mk3_path()

        port_name = self.vebus_service.rsplit(".", 1)[-1]

        self.mk2_service_path = (
            f"/service/mk2-dbus.{port_name}"
        )

    def _get_bus(self):
        if "DBUS_SESSION_BUS_ADDRESS" in os.environ:
            return dbus.SessionBus()

        return dbus.SystemBus(private=True)

    def _read(self, service, path):
        obj = self.bus.get_object(
            service,
            path,
            introspect=False,
        )

        item = dbus.Interface(
            obj,
            "com.victronenergy.BusItem",
        )

        return item.GetValue()

    def _write(self, service, path, value):
        obj = self.bus.get_object(
            service,
            path,
            introspect=False,
        )

        item = dbus.Interface(
            obj,
            "com.victronenergy.BusItem",
        )

        if isinstance(value, float):
            dbus_value = dbus.Double(
                value,
                variant_level=1,
            )
        else:
            dbus_value = dbus.Int32(
                int(value),
                variant_level=1,
            )

        result = item.SetValue(dbus_value)

        if int(result) != 0:
            raise RuntimeError(
                f"SetValue failed for {path}: {result}"
            )

    def _write_verified(
        self,
        service,
        path,
        value,
        delay=0.25,
    ):
        self._write(service, path, value)

        time.sleep(delay)

        actual = self._read(service, path)

        if isinstance(value, float):
            if abs(float(actual) - value) > 0.01:
                raise RuntimeError(
                    f"Write verification failed for {path}: "
                    f"wanted {value}, got {actual}"
                )

        elif bool(actual) != bool(value):
            raise RuntimeError(
                f"Write verification failed for {path}: "
                f"wanted {value}, got {actual}"
            )

        return actual

    def _find_service(
        self,
        prefix,
        product_contains,
    ):
        matches = []

        for service in self.bus.list_names():
            if not service.startswith(prefix):
                continue

            try:
                product_name = str(
                    self._read(
                        service,
                        "/ProductName",
                    )
                )
            except Exception:
                continue

            if (
                product_contains.lower()
                in product_name.lower()
            ):
                matches.append(service)

        if len(matches) == 0:
            raise RuntimeError(
                f"No Victron service found for "
                f"{product_contains}"
            )

        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple Victron services found for "
                f"{product_contains}: {matches}"
            )

        return matches[0]

    def _find_mk3_path(self):
        matches = glob.glob(
            "/dev/serial/by-id/"
            "*VictronEnergy_MK3-USB_Interface*"
        )

        if len(matches) == 0:
            raise RuntimeError(
                "No Victron MK3 USB interface found"
            )

        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple MK3 interfaces found: {matches}"
            )

        return matches[0]

    def _wait_for_vebus(
        self,
        present,
        timeout=20.0,
    ):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            names = self.bus.list_names()
            exists = self.vebus_service in names

            if exists == present:
                if not present:
                    return

                try:
                    str(
                        self._read(
                            self.vebus_service,
                            "/ProductName",
                        )
                    )

                    int(
                        self._read(
                            self.vebus_service,
                            "/State",
                        )
                    )

                    int(
                        self._read(
                            self.vebus_service,
                            "/Ac/State/IgnoreAcIn1",
                        )
                    )

                    int(
                        self._read(
                            self.vebus_service,
                            "/Devices/0/ExtendStatus/"
                            "ChargeIsDisabled",
                        )
                    )

                    return

                except Exception:
                    pass

            time.sleep(0.25)

        state = "appear" if present else "disappear"

        raise RuntimeError(
            f"Timed out waiting for "
            f"{self.vebus_service} to {state}"
        )

    async def _mk3_request(
        self,
        driver,
        payload,
    ):
        done = asyncio.Event()
        result = {}

        def completion(handler, msg):
            result["msg"] = bytes(msg)
            done.set()

        driver._send_w_request(
            payload,
            completion,
        )

        await asyncio.wait_for(
            done.wait(),
            timeout=2,
        )

        return result["msg"]

    async def _mk3_read_flags0(
        self,
        driver,
    ):
        msg = await self._mk3_request(
            driver,
            [
                0x31,
                FLAGS0_SETTING_ID & 0xFF,
                FLAGS0_SETTING_ID >> 8,
            ],
        )

        if len(msg) < 5 or msg[2] != 0x86:
            raise RuntimeError(
                f"Unexpected Flags0 read response: "
                f"{msg.hex()}"
            )

        return msg[3] | (msg[4] << 8)

    async def _mk3_write_flags0(
        self,
        driver,
        raw_value,
    ):
        done = asyncio.Event()
        result = {}

        def completion(handler, msg):
            result["msg"] = bytes(msg)
            done.set()

        driver._send_frame(
            "X",
            [
                0x33,
                FLAGS0_SETTING_ID & 0xFF,
                FLAGS0_SETTING_ID >> 8,
            ],
        )

        driver._send_w_request(
            [
                0x34,
                raw_value & 0xFF,
                raw_value >> 8,
            ],
            completion,
        )

        await asyncio.wait_for(
            done.wait(),
            timeout=2,
        )

        msg = result["msg"]

        if len(msg) < 3 or msg[2] != 0x88:
            raise RuntimeError(
                f"Unexpected Flags0 write response: "
                f"{msg.hex()}"
            )

    async def _change_disable_charge_direct(
        self,
        enabled,
    ):
        mk3 = VictronMK3(self.mk3_path)
        await mk3.start(QuietHandler())

        try:
            before = await self._mk3_read_flags0(
                mk3._driver
            )

            mask = 1 << DISABLE_CHARGE_BIT

            if enabled:
                target = before | mask
            else:
                target = before & ~mask

            if target != before:
                await self._mk3_write_flags0(
                    mk3._driver,
                    target,
                )

            after = await self._mk3_read_flags0(
                mk3._driver
            )

            if after != target:
                raise RuntimeError(
                    f"Flags0 verification failed: "
                    f"wanted 0x{target:04X}, "
                    f"got 0x{after:04X}"
                )

            return before, after

        finally:
            await mk3.stop()

    def _set_disable_charge(
        self,
        enabled,
    ):
        current = bool(
            self._read(
                self.vebus_service,
                "/Devices/0/ExtendStatus/"
                "ChargeIsDisabled",
            )
        )

        if current == enabled:
            return

        subprocess.run(
            [
                "svc",
                "-d",
                self.mk2_service_path,
            ],
            check=True,
        )

        try:
            self._wait_for_vebus(
                present=False,
            )

            before, after = asyncio.run(
                self._change_disable_charge_direct(
                    enabled
                )
            )

            print(
                f"Flags0: "
                f"0x{before:04X} -> "
                f"0x{after:04X}"
            )

        finally:
            subprocess.run(
                [
                    "svc",
                    "-u",
                    self.mk2_service_path,
                ],
                check=True,
            )

            self._wait_for_vebus(
                present=True,
            )

        deadline = time.monotonic() + 20.0

        while True:
            try:
                verified = bool(
                    self._read(
                        self.vebus_service,
                        "/Devices/0/ExtendStatus/"
                        "ChargeIsDisabled",
                    )
                )
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Timed out waiting for ChargeIsDisabled "
                        "after mk2-dbus restart"
                    )
                time.sleep(0.25)

        if verified != enabled:
            raise RuntimeError(
                "Charge-disable verification failed "
                f"after mk2-dbus restart: {verified}"
            )

    def read_snapshot(self):
        soc = float(
            self._read(
                self.battery_service,
                "/Soc",
            )
        )

        ac_available = bool(
            self._read(
                self.vebus_service,
                "/Ac/State/AcIn1Available",
            )
        )

        ignore_ac = bool(
            self._read(
                self.vebus_service,
                "/Ac/State/IgnoreAcIn1",
            )
        )

        charge_disabled = bool(
            self._read(
                self.vebus_service,
                "/Devices/0/ExtendStatus/"
                "ChargeIsDisabled",
            )
        )

        if ignore_ac:
            operating_mode = (
                OperatingMode.BATTERY_FIRST
            )

        elif charge_disabled:
            operating_mode = (
                OperatingMode.PASS_THROUGH
            )

        else:
            operating_mode = (
                OperatingMode.CHARGING
            )

        return SystemSnapshot(
            soc=soc,
            ac_available=ac_available,
            operating_mode=operating_mode,
        )

    def set_battery_first(self):
        print("Setting BATTERY_FIRST")

        self._write_verified(
            self.vebus_service,
            "/Ac/Control/IgnoreAcIn1",
            1,
        )

        self._set_disable_charge(False)

    def set_pass_through(self):
        print("Setting PASS_THROUGH")

        self._set_disable_charge(True)

        self._write_verified(
            self.vebus_service,
            "/Ac/Control/IgnoreAcIn1",
            0,
        )

    def set_charging(
        self,
        max_charge_current,
    ):
        print(
            f"Setting CHARGING "
            f"({max_charge_current} A max)"
        )

        self._write_verified(
            self.vebus_service,
            "/Dc/0/MaxChargeCurrent",
            float(max_charge_current),
        )

        self._set_disable_charge(False)

        self._write_verified(
            self.vebus_service,
            "/Ac/Control/IgnoreAcIn1",
            0,
        )

    def battery_status(self):
        voltage = float(
            self._read(
                self.battery_service,
                "/Dc/0/Voltage",
            )
        )

        current = float(
            self._read(
                self.battery_service,
                "/Dc/0/Current",
            )
        )

        power = voltage * current

        if current > 0.5:
            flow = "CHARGING"
        elif current < -0.5:
            flow = "DISCHARGING"
        else:
            flow = "IDLE"

        return {
            "flow": flow,
            "voltage": voltage,
            "current": current,
            "power": power,
        }

    def diagnostics(self):
        return {
            "vebus_service":
                self.vebus_service,

            "battery_service":
                self.battery_service,

            "mk3_path":
                self.mk3_path,

            "product":
                str(
                    self._read(
                        self.vebus_service,
                        "/ProductName",
                    )
                ),

            "battery_product":
                str(
                    self._read(
                        self.battery_service,
                        "/ProductName",
                    )
                ),

            "soc":
                float(
                    self._read(
                        self.battery_service,
                        "/Soc",
                    )
                ),

            "state":
                int(
                    self._read(
                        self.vebus_service,
                        "/State",
                    )
                ),

            "ac_available":
                bool(
                    self._read(
                        self.vebus_service,
                        "/Ac/State/AcIn1Available",
                    )
                ),

            "ac_connected":
                bool(
                    self._read(
                        self.vebus_service,
                        "/Ac/ActiveIn/Connected",
                    )
                ),

            "ignore_ac":
                bool(
                    self._read(
                        self.vebus_service,
                        "/Ac/State/IgnoreAcIn1",
                    )
                ),

            "charge_disabled":
                bool(
                    self._read(
                        self.vebus_service,
                        "/Devices/0/ExtendStatus/"
                        "ChargeIsDisabled",
                    )
                ),

            "max_charge_current":
                float(
                    self._read(
                        self.vebus_service,
                        "/Dc/0/MaxChargeCurrent",
                    )
                ),
        }


if __name__ == "__main__":
    hardware = VictronHardware()

    print("Solar Fortress Victron Hardware")
    print("=" * 40)

    for key, value in hardware.diagnostics().items():
        print(f"{key}: {value}")

    print()
    print("Snapshot:")
    print(hardware.read_snapshot())
